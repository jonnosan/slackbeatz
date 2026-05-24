"""Offline song rendering using Surge XT (VST3) + FluidSynth + the
in-process sampler. Produces a single WAV/MP3 file with the same
sound design that ``slackbeatz live --surge`` uses at playback time —
but rendered faster than real-time and bit-deterministic across runs.

Architecture
------------

Surge XT's CLI binary (``surge-xt-cli``) has no offline render mode —
it only outputs real-time audio. The Surge XT **VST3 plugin** *is*
offline-renderable when hosted in a JUCE-aware engine. We use
`dawdreamer <https://github.com/DBraun/DawDreamer>`_ as that host:
it spins up a JUCE render engine, loads the Surge VST3 with a
specific ``.fxp`` factory patch, accepts a per-channel MIDI file,
and returns a numpy buffer faster than wall-clock.

Per-channel routing:

* **Pitched / sub roles** (lead, bass, pad, candy, sub — channels 1,
  2, 3, 4, 6) → dawdreamer + Surge XT VST3 with the role's
  :data:`SYNTH_ROLES` patch.
* **Drum channel** (10) → existing FluidSynth render path
  (:func:`slackbeatz.audio.render_audio`).
* **Sampler roles** (voice on ch 5, fx on ch 11) → mix the WAVs that
  speech / sample gens registered with the active sampler, at the
  tick offsets from the resolved song.
* **Any other channel** → falls through to FluidSynth so the song
  still renders if a user's setup uses non-standard channels.

Each per-channel render writes a stem buffer in memory; the master
buffer is the sample-aligned sum of the stems. ffmpeg encodes the
final WAV to MP3 / etc. if the user's output extension calls for it.

Optional dependency
-------------------

dawdreamer is a JUCE-backed binary wheel (~50 MB) and currently
publishes wheels for Python 3.9-3.12. On Python 3.13+ the install
fails; this module surfaces a clear install hint in that case.
Install via the optional ``offline-render`` extra::

    pip install 'slackbeatz[offline-render]'

Surge XT must be installed system-wide for its VST3 to be loadable
(``brew install --cask surge-xt`` on macOS).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Imports kept lazy so just importing this module never pulls in the
# heavy deps (numpy / dawdreamer) when a user is only running headless
# MIDI rendering.


# --------------------------------------------------------------------------
# VST3 discovery
# --------------------------------------------------------------------------

def _surge_vst3_path() -> Optional[Path]:
    """Locate the Surge XT VST3 plugin on the host. Returns ``None``
    if Surge XT isn't installed.

    Search order matches the platform's VST3 conventions: system path
    first, then per-user (in case a user installed Surge into their
    home dir but not system-wide)."""
    candidates: list[Path]
    if sys.platform == "darwin":
        candidates = [
            Path("/Library/Audio/Plug-Ins/VST3/Surge XT.vst3"),
            Path.home() / "Library/Audio/Plug-Ins/VST3/Surge XT.vst3",
        ]
    elif sys.platform.startswith("linux"):
        candidates = [
            Path("/usr/lib/vst3/Surge XT.vst3"),
            Path("/usr/local/lib/vst3/Surge XT.vst3"),
            Path.home() / ".vst3/Surge XT.vst3",
        ]
    elif sys.platform.startswith("win"):
        candidates = [
            Path(r"C:\Program Files\Common Files\VST3\Surge XT.vst3"),
        ]
    else:
        candidates = []
    for c in candidates:
        if c.exists():
            return c
    return None


# --------------------------------------------------------------------------
# Stub sampler — captures bank entries during MIDI build
# --------------------------------------------------------------------------

class _RenderingSampler:
    """Drop-in stand-in for :class:`slackbeatz.sampler.Sampler` used
    during offline rendering.

    Speech / sample generators look up the active sampler via
    :func:`slackbeatz.sampler.get_active_sampler` and call
    :meth:`set_sample` on it as part of resolve / generate. The live
    Sampler also opens an audio output stream — which we don't want
    during offline render. This stub exposes only the bank-management
    surface (``set_sample`` / ``remove_sample`` / ``get_bank``) and
    leaves audio output to the caller (we mix the WAVs ourselves).
    """

    def __init__(self) -> None:
        self._ports: dict[str, dict[int, Path]] = {}

    def set_sample(self, port_name: str, midi_note: int, wav_path) -> None:
        self._ports.setdefault(port_name, {})[int(midi_note)] = Path(wav_path)

    def remove_sample(self, port_name: str, midi_note: int) -> None:
        if port_name in self._ports:
            self._ports[port_name].pop(int(midi_note), None)

    def get_bank(self, port_name: str) -> dict[int, Path]:
        return dict(self._ports.get(port_name, {}))

    # The live Sampler has start()/stop() — keep the interface symmetric
    # so any future caller that constructs a sampler-like via duck typing
    # doesn't accidentally bypass us.
    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

class OfflineRenderError(RuntimeError):
    """Raised when offline rendering can't proceed (missing dep,
    missing VST3, render failure). Carries an install / fix hint."""


def _require_offline_deps():
    """Import the offline-render dependency triple (dawdreamer, numpy,
    soundfile) or raise a friendly :class:`OfflineRenderError` with
    the install hint that covers all three.

    All three deps are co-installed by ``pip install slackbeatz[offline-render]``
    (numpy + soundfile are transitives of dawdreamer); listing them
    together here keeps the user from having to play whack-a-mole on
    individual ImportErrors."""
    missing: list[str] = []
    try:
        import dawdreamer  # noqa: F401
    except ImportError:
        missing.append("dawdreamer")
    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")
    try:
        import soundfile  # noqa: F401
    except ImportError:
        missing.append("soundfile")
    if not missing:
        return
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    hint = (
        "Install via the slackbeatz optional extra:\n"
        "  pip install 'slackbeatz[offline-render]'\n"
        "\n"
        "Note: dawdreamer publishes wheels for Python 3.9-3.12; this "
        f"venv is Python {py}."
    )
    if sys.version_info >= (3, 13):
        hint += (
            "\nYou're on 3.13+ which dawdreamer doesn't ship wheels for "
            "yet — create a 3.12 venv just for offline rendering:\n"
            "  python3.12 -m venv .venv-offline\n"
            "  .venv-offline/bin/pip install -e '.[offline-render,sampler]'"
        )
    raise OfflineRenderError(
        f"offline render needs: {', '.join(missing)} (none found in this venv).\n"
        f"{hint}"
    )


# --------------------------------------------------------------------------
# Per-stem rendering
# --------------------------------------------------------------------------

def _render_surge_stem(
    *,
    midi_path: Path,
    patch_path: Optional[Path],
    duration_s: float,
    sample_rate: int,
    vst3_path: Path,
):
    """Render one pitched channel via Surge XT VST3 + dawdreamer.

    Returns a ``numpy.ndarray`` shaped ``(2, n_samples)`` (stereo
    float32). The patch's release tail is included in *duration_s* —
    callers should add a small head-room (~2 s) so reverb tails don't
    clip.
    """
    import numpy as np
    import dawdreamer as dd  # caller has already passed _require_offline_deps

    engine = dd.RenderEngine(sample_rate, 512)
    synth = engine.make_plugin_processor("surge", str(vst3_path))
    # .fxp factory patches load via load_preset; .vstpreset would use
    # load_vst3_preset. The dawdreamer error on the wrong call is loud,
    # so we try the right one first.
    if patch_path is not None and patch_path.is_file():
        try:
            synth.load_preset(str(patch_path))
        except Exception as e:  # noqa: BLE001 — surface via stderr, keep going
            print(
                f"slackbeatz audio --surge: failed to load patch "
                f"{patch_path.name} ({e}); using default Surge XT state",
                file=sys.stderr,
            )

    synth.load_midi(str(midi_path))
    engine.load_graph([(synth, [])])
    engine.render(duration_s)
    audio = synth.get_audio()  # shape (channels, samples), float32

    # Pad-or-truncate to exactly duration_s samples so all stems align
    # when we sum them.
    target = int(round(duration_s * sample_rate))
    if audio.shape[1] < target:
        pad = np.zeros((audio.shape[0], target - audio.shape[1]), dtype=audio.dtype)
        audio = np.concatenate([audio, pad], axis=1)
    elif audio.shape[1] > target:
        audio = audio[:, :target]
    # Force stereo: mono → duplicate, more-than-stereo → downmix front pair.
    if audio.shape[0] == 1:
        audio = np.concatenate([audio, audio], axis=0)
    elif audio.shape[0] > 2:
        audio = audio[:2]
    return audio


def _render_drum_stem(
    *,
    midi_path: Path,
    soundfont: Path,
    duration_s: float,
    sample_rate: int,
):
    """Render the drum channel via FluidSynth's ``--fast-render`` mode.

    FluidSynth handles GM channel 10 directly; we feed it a per-channel
    MIDI file containing only ch 10's events plus the tempo map.
    Returns a stereo float32 numpy array of shape ``(2, n_samples)``.
    """
    import numpy as np
    import soundfile as sf

    from slackbeatz.audio import render_audio  # uses --fast-render

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = Path(tmp.name)
    try:
        render_audio(midi_path, tmp_wav, soundfont, sample_rate=sample_rate)
        data, sr = sf.read(str(tmp_wav), dtype="float32", always_2d=True)
    finally:
        tmp_wav.unlink(missing_ok=True)

    # soundfile returns (samples, channels); transpose to (channels, samples).
    audio = data.T
    target = int(round(duration_s * sample_rate))
    if audio.shape[1] < target:
        pad = np.zeros((audio.shape[0], target - audio.shape[1]), dtype=audio.dtype)
        audio = np.concatenate([audio, pad], axis=1)
    elif audio.shape[1] > target:
        audio = audio[:, :target]
    if audio.shape[0] == 1:
        audio = np.concatenate([audio, audio], axis=0)
    return audio


def _render_sampler_stem(
    *,
    bank: dict[int, Path],
    note_events: list[tuple[float, int, int]],
    duration_s: float,
    sample_rate: int,
):
    """Mix the bank's WAVs at the given note positions into a stem.

    *note_events* is a list of ``(start_seconds, midi_note, velocity)``
    tuples — one per note_on. Each note triggers playback of the
    matching ``bank[note]`` WAV from its start_seconds; samples whose
    pitches aren't in the bank are silently skipped (matches the live
    sampler's behaviour).

    Velocity scales the contribution linearly (``vel / 127``). No
    polyphony cap — overlapping samples sum.
    """
    import numpy as np
    import soundfile as sf

    n_samples = int(round(duration_s * sample_rate))
    out = np.zeros((2, n_samples), dtype=np.float32)

    # WAV cache so the same phrase / sample triggered N times reads
    # from disk once.
    cache: dict[Path, np.ndarray] = {}

    def _load(p: Path) -> "np.ndarray":
        if p in cache:
            return cache[p]
        data, sr = sf.read(str(p), dtype="float32", always_2d=True)
        # naive linear resample to the master rate
        if sr != sample_rate:
            ratio = sample_rate / sr
            n_out = int(round(data.shape[0] * ratio))
            if n_out > 0:
                t_in = np.arange(data.shape[0], dtype=np.float64)
                t_out = np.linspace(0.0, data.shape[0] - 1, num=n_out, dtype=np.float64)
                data = np.stack([
                    np.interp(t_out, t_in, data[:, c])
                    for c in range(data.shape[1])
                ], axis=1).astype(np.float32)
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        elif data.shape[1] > 2:
            data = data[:, :2]
        chunk = data.T  # (2, samples)
        cache[p] = chunk
        return chunk

    for start_s, note, velocity in note_events:
        wav_path = bank.get(int(note))
        if wav_path is None:
            continue
        chunk = _load(wav_path)
        start_sample = int(round(start_s * sample_rate))
        if start_sample >= n_samples:
            continue
        # Truncate the WAV if it would overshoot the song end.
        avail = n_samples - start_sample
        n = min(chunk.shape[1], avail)
        gain = max(0.0, min(1.0, velocity / 127.0))
        out[:, start_sample:start_sample + n] += chunk[:, :n] * gain
    return out


# --------------------------------------------------------------------------
# Master render
# --------------------------------------------------------------------------

def render_song_with_surge(
    resolved,
    output_path: Path,
    *,
    soundfont: Optional[Path] = None,
    sample_rate: int = 44100,
    bitrate: str = "192k",
    tail_seconds: float = 2.0,
    progress: Optional[callable] = None,
) -> Path:
    """Render *resolved* to *output_path* using Surge XT (pitched
    channels) + FluidSynth (drums) + the sampler bank (voice / fx).

    Parameters
    ----------
    resolved:
        A :class:`slackbeatz.model.song.ResolvedSong`.
    output_path:
        Destination file. ``.wav`` writes the master directly;
        ``.mp3`` (or anything else ffmpeg understands) goes through an
        encode step. Parent directories are created.
    soundfont:
        Override for the drum render's soundfont. Defaults to whatever
        :func:`slackbeatz.audio.find_soundfont` picks.
    sample_rate:
        Master sample rate. 44.1 kHz / 48 kHz are sensible.
    bitrate:
        ffmpeg encode bitrate for non-WAV outputs.
    tail_seconds:
        Extra render time tacked onto the song length so Surge XT's
        reverb tails / sampler-bank phrases finish cleanly instead of
        clipping at the bar boundary.
    progress:
        Optional callback ``progress(stage: str)``. The CLI uses
        :func:`print` here so users see ``rendering bass (Surge XT)``
        lines as each stem completes.

    Returns the resolved output path.

    Raises :class:`OfflineRenderError` for missing deps / missing
    VST3.
    """
    # Pre-flight pass 1: the Python deps. Do this BEFORE any
    # top-of-function ``import numpy`` so the user sees the friendly
    # install hint and not a raw ModuleNotFoundError.
    _require_offline_deps()

    import numpy as np
    import soundfile as sf

    from slackbeatz.audio import find_soundfont, require_tool, MissingToolError
    from slackbeatz.engine.midifile import build_midifile
    from slackbeatz.sampler import get_active_sampler, set_active_sampler
    from slackbeatz.synthhost import OSC_CHANNELS
    from slackbeatz.surge_host import _SURGE_FACTORY, SYNTH_ROLES

    if progress is None:
        progress = lambda _msg: None  # noqa: E731 — small enough

    # Pre-flight pass 2: VST3 + FluidSynth on the host.
    vst3 = _surge_vst3_path()
    if vst3 is None:
        raise OfflineRenderError(
            "Surge XT VST3 not found. Install Surge XT system-wide:\n"
            "  brew install --cask surge-xt        (macOS)\n"
            "  apt install surge-xt                (Debian/Ubuntu)\n"
            "  https://surge-synthesizer.github.io/  (Windows)"
        )
    try:
        require_tool("fluidsynth")  # for drums
    except MissingToolError as e:
        raise OfflineRenderError(str(e)) from e

    sf_path = soundfont or find_soundfont(None)

    # Drive the generators with a stub sampler so speech/sample gens
    # populate WAV banks that we can mix later. We snapshot the prior
    # active sampler in case some other path had set one.
    prior_sampler = get_active_sampler()
    stub = _RenderingSampler()
    set_active_sampler(stub)
    try:
        full_midi = build_midifile(resolved)
    finally:
        set_active_sampler(prior_sampler)

    voice_bank = stub.get_bank(OSC_CHANNELS["voice"][1])
    fx_bank = stub.get_bank(OSC_CHANNELS["fx"][1])

    # Compute total render duration including a tail for reverb /
    # sampler phrases that extend past the last note_on.
    song_seconds = full_midi.length + tail_seconds

    # Map: channel_1idx → role → patch path. Built from SYNTH_ROLES
    # so the same patches that --surge live uses get baked into the
    # offline render.
    surge_role_by_channel: dict[int, dict] = {
        cfg.channel_1idx: {
            "role": cfg.role,
            "patch": _SURGE_FACTORY / cfg.initial_patch,
        }
        for cfg in SYNTH_ROLES
    }

    # Group MIDI events by channel for stem rendering. Use mido
    # 0-indexed channels internally; report 1-indexed in progress.
    used_channels = sorted({
        msg.channel for tr in full_midi.tracks for msg in tr
        if msg.type == "note_on" and msg.velocity > 0
    })

    stems: list = []  # list of (channel_1idx, role, np.ndarray)

    for ch0 in used_channels:
        ch1 = ch0 + 1
        # 1) Drums via FluidSynth
        if ch1 == 10:
            progress(f"  rendering ch{ch1} (drums) → FluidSynth")
            stem_midi = _filter_to_channel(full_midi, ch0)
            with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
                tmp_midi = Path(tmp.name)
            try:
                stem_midi.save(str(tmp_midi))
                audio = _render_drum_stem(
                    midi_path=tmp_midi, soundfont=sf_path,
                    duration_s=song_seconds, sample_rate=sample_rate,
                )
            finally:
                tmp_midi.unlink(missing_ok=True)
            stems.append((ch1, "drums", audio))
            continue

        # 2) Sampler channels (voice / fx) — mix bank WAVs at tick offsets.
        if ch1 == OSC_CHANNELS["voice"][0] and voice_bank:
            progress(f"  rendering ch{ch1} (voice) → sampler bank ({len(voice_bank)} WAV(s))")
            events = _note_events_in_seconds(full_midi, ch0)
            audio = _render_sampler_stem(
                bank=voice_bank, note_events=events,
                duration_s=song_seconds, sample_rate=sample_rate,
            )
            stems.append((ch1, "voice", audio))
            continue
        if ch1 == OSC_CHANNELS["fx"][0] and fx_bank:
            progress(f"  rendering ch{ch1} (fx) → sampler bank ({len(fx_bank)} WAV(s))")
            events = _note_events_in_seconds(full_midi, ch0)
            audio = _render_sampler_stem(
                bank=fx_bank, note_events=events,
                duration_s=song_seconds, sample_rate=sample_rate,
            )
            stems.append((ch1, "fx", audio))
            continue

        # 3) Surge VST3 — pitched roles with a matching SYNTH_ROLES entry.
        role_info = surge_role_by_channel.get(ch1)
        if role_info is not None:
            progress(f"  rendering ch{ch1} ({role_info['role']}) → Surge XT VST3")
            stem_midi = _filter_to_channel(full_midi, ch0)
            with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
                tmp_midi = Path(tmp.name)
            try:
                stem_midi.save(str(tmp_midi))
                audio = _render_surge_stem(
                    midi_path=tmp_midi,
                    patch_path=role_info["patch"],
                    duration_s=song_seconds,
                    sample_rate=sample_rate,
                    vst3_path=vst3,
                )
            finally:
                tmp_midi.unlink(missing_ok=True)
            stems.append((ch1, role_info["role"], audio))
            continue

        # 4) Anything else — fall back to FluidSynth so songs with
        # custom channels still render fully.
        progress(f"  rendering ch{ch1} (unmapped) → FluidSynth fallback")
        stem_midi = _filter_to_channel(full_midi, ch0)
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
            tmp_midi = Path(tmp.name)
        try:
            stem_midi.save(str(tmp_midi))
            audio = _render_drum_stem(
                midi_path=tmp_midi, soundfont=sf_path,
                duration_s=song_seconds, sample_rate=sample_rate,
            )
        finally:
            tmp_midi.unlink(missing_ok=True)
        stems.append((ch1, f"ch{ch1}", audio))

    if not stems:
        raise OfflineRenderError("song produced no audible events on any channel")

    # Sum the stems into the master. They're all the same shape
    # because _render_*_stem pads to song_seconds * sample_rate.
    master = np.zeros_like(stems[0][2])
    for _ch, _role, audio in stems:
        master += audio
    # Soft-clip protection — sum of N stems can easily exceed ±1.
    peak = float(np.abs(master).max() or 1.0)
    if peak > 0.99:
        master *= 0.99 / peak

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ext = output_path.suffix.lower()
    progress(f"  mixing {len(stems)} stem(s) → {output_path}")
    if ext == ".wav":
        sf.write(str(output_path), master.T, sample_rate)
        return output_path
    # Non-WAV: write a temp WAV and encode through ffmpeg.
    ffmpeg = require_tool("ffmpeg")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = Path(tmp.name)
    try:
        sf.write(str(tmp_wav), master.T, sample_rate)
        subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "warning",
                "-i", str(tmp_wav),
                "-b:a", bitrate, str(output_path),
            ],
            check=True,
        )
    finally:
        tmp_wav.unlink(missing_ok=True)
    return output_path


# --------------------------------------------------------------------------
# MIDI helpers
# --------------------------------------------------------------------------

def _filter_to_channel(full_mid, target_channel_0idx: int):
    """Build a new ``mido.MidiFile`` with the tempo map + only events
    on *target_channel_0idx*. Mirrors :func:`slackbeatz.export._filter_midi_to_channel`
    but is local so this module stays self-contained."""
    import mido
    out = mido.MidiFile(ticks_per_beat=full_mid.ticks_per_beat, type=full_mid.type)
    if full_mid.tracks:
        out.tracks.append(full_mid.tracks[0])  # tempo map / meta
    for tr in full_mid.tracks[1:]:
        for msg in tr:
            if hasattr(msg, "channel") and msg.channel == target_channel_0idx:
                out.tracks.append(tr)
                break
    return out


def _note_events_in_seconds(full_mid, target_channel_0idx: int):
    """Walk *full_mid* and collect ``(start_s, note, velocity)`` for
    every note_on on *target_channel_0idx*. Honours tempo changes via
    mido's per-message ``time`` aggregation."""
    import mido
    out: list[tuple[float, int, int]] = []
    # mido yields messages in absolute order across tracks when we iterate
    # the file directly, with `time` in seconds based on the tempo map.
    elapsed_s = 0.0
    for msg in full_mid:  # type: ignore[assignment]
        elapsed_s += msg.time
        if (
            getattr(msg, "type", None) == "note_on"
            and getattr(msg, "velocity", 0) > 0
            and getattr(msg, "channel", None) == target_channel_0idx
        ):
            out.append((elapsed_s, msg.note, msg.velocity))
    return out
