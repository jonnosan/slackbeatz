"""Python MIDI-triggered WAV sampler.

Subscribes to one or more virtual MIDI input ports (typically the
``slackbeatz-voice`` + ``slackbeatz-fx`` ports created by
:class:`MultiPortSink`), and plays a WAV file in response to each
``note_on`` whose pitch is mapped in that port's *bank*.

Design choices (locked, see ``docs/design-tts-sampler.md``):

* Native pitch per note — no re-pitching. Each MIDI note maps to one
  WAV. Drum-kit and multi-sample instrument layouts work the same way.
* Velocity → linear amplitude scale (``velocity / 127``).
* Polyphony with LRU eviction at ``max_polyphony``.
* Brief release envelope (~50 ms) on ``note_off`` so short notes don't
  click.
* Stereo or mono WAVs are both fine; mono samples play centred,
  stereo preserve their per-channel content.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# sounddevice + soundfile + numpy are optional at import time so callers
# without these can still ``import slackbeatz.sampler`` — they're only
# required when the Sampler is actually constructed + started. This
# matches how :mod:`surge_host` lazy-imports python-osc.


@dataclass
class _Voice:
    """One currently-playing sample. ``cursor`` is the next-sample
    index into ``audio``. After ``note_off``, ``release_left`` counts
    down to a soft fade (set to the release-envelope length in frames)
    so short notes don't end abruptly. ``port_name`` identifies which
    sampler bank the voice came from, so the audio callback can route
    voices into per-port mix buses (per-port gain + per-port FX chain
    in :meth:`set_port_gain` / :meth:`enable_fx`)."""

    audio: object  # numpy.ndarray, shape (frames, channels)
    cursor: int = 0
    velocity_gain: float = 1.0
    # None = note still held; int = remaining release-envelope frames.
    release_left: Optional[int] = None
    release_total: int = 1
    port_name: str = ""


@dataclass
class _PortListener:
    """Per-port MIDI subscription bundle: the open mido input port + the
    thread reading from it. Owned by :class:`Sampler`."""

    port_name: str
    bank: dict[int, Path]
    midi_port: object = None   # mido.ports.BaseInput
    thread: Optional[threading.Thread] = None
    stop_flag: threading.Event = field(default_factory=threading.Event)


# --------------------------------------------------------------------------
# Pedalboard FX catalog — used by the 🎛 Mixer tab's per-sampler-strip
# FX-slot dropdowns (GH #33)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PedalboardParamSpec:
    """One slider's worth of pedalboard-plugin attribute metadata.

    The Mixer GUI reads each spec, finds the matching ``attr`` on the
    live plugin, and renders a slider whose drag mutates the plugin
    in place. pedalboard supports in-place attribute mutation on a
    plugin that's part of a running ``Pedalboard`` chain.
    """

    label: str          # display name shown next to the slider
    attr: str           # python attribute name on the plugin
    lo: float
    hi: float
    default: float


@dataclass(frozen=True)
class PedalboardFXSpec:
    """One pedalboard FX-type catalog entry: display name + a builder
    callable that returns a fresh plugin instance with neutral
    defaults, plus the param specs for the mixer GUI sliders.

    The builder defaults each plugin to its "no audible effect" state
    so swapping FX types doesn't blast loud audio at the user — they
    have to move a slider to hear anything."""

    name: str
    builder: Callable[[], Any]
    params: tuple[PedalboardParamSpec, ...]


def _pedalboard_catalog() -> "dict[str, PedalboardFXSpec]":
    """Build the catalog lazily so importing this module doesn't
    require pedalboard. Returns an empty dict if pedalboard isn't
    installed; the Mixer GUI then renders sampler strips without FX
    type dropdowns + falls back to the legacy fixed-chain UX."""
    try:
        from pedalboard import (
            Bitcrush,
            Chorus,
            Compressor,
            Delay,
            Distortion,
            Gain,
            HighpassFilter,
            LowpassFilter,
            Phaser,
            Reverb,
        )
    except ImportError:
        return {}

    # Curated subset of pedalboard built-ins biased toward "useful in
    # a live mixer". Insertion order = dropdown order. "Off" maps to
    # Gain(0) — pedalboard has no bypass primitive, so a passthrough
    # gain serves the same purpose at negligible CPU cost.
    catalog: dict[str, PedalboardFXSpec] = {}

    catalog["Off"] = PedalboardFXSpec(
        name="Off",
        builder=lambda: Gain(gain_db=0.0),
        params=(),
    )
    catalog["Distortion"] = PedalboardFXSpec(
        name="Distortion",
        builder=lambda: Distortion(drive_db=0.0),
        params=(
            PedalboardParamSpec("drive", "drive_db", 0.0, 30.0, 0.0),
        ),
    )
    catalog["Delay"] = PedalboardFXSpec(
        name="Delay",
        builder=lambda: Delay(delay_seconds=0.25, feedback=0.0, mix=0.0),
        params=(
            PedalboardParamSpec("time",  "delay_seconds", 0.0,  2.0,  0.25),
            PedalboardParamSpec("fb",    "feedback",      0.0,  0.95, 0.0),
            PedalboardParamSpec("mix",   "mix",           0.0,  1.0,  0.0),
        ),
    )
    catalog["Reverb"] = PedalboardFXSpec(
        name="Reverb",
        builder=lambda: Reverb(
            room_size=0.5, damping=0.5, wet_level=0.0, dry_level=1.0,
        ),
        params=(
            PedalboardParamSpec("size", "room_size", 0.0, 1.0, 0.5),
            PedalboardParamSpec("damp", "damping",   0.0, 1.0, 0.5),
            PedalboardParamSpec("wet",  "wet_level", 0.0, 1.0, 0.0),
        ),
    )
    catalog["Chorus"] = PedalboardFXSpec(
        name="Chorus",
        builder=lambda: Chorus(rate_hz=1.0, depth=0.0, mix=0.0),
        params=(
            PedalboardParamSpec("rate",  "rate_hz", 0.1, 7.0, 1.0),
            PedalboardParamSpec("depth", "depth",   0.0, 1.0, 0.0),
            PedalboardParamSpec("mix",   "mix",     0.0, 1.0, 0.0),
        ),
    )
    catalog["Phaser"] = PedalboardFXSpec(
        name="Phaser",
        builder=lambda: Phaser(rate_hz=0.5, depth=0.0, mix=0.0),
        params=(
            PedalboardParamSpec("rate",  "rate_hz", 0.1, 7.0, 0.5),
            PedalboardParamSpec("depth", "depth",   0.0, 1.0, 0.0),
            PedalboardParamSpec("mix",   "mix",     0.0, 1.0, 0.0),
        ),
    )
    catalog["Compressor"] = PedalboardFXSpec(
        name="Compressor",
        builder=lambda: Compressor(threshold_db=-10.0, ratio=4.0),
        params=(
            PedalboardParamSpec("thresh", "threshold_db", -50.0, 0.0, -10.0),
            PedalboardParamSpec("ratio",  "ratio",         1.0, 20.0, 4.0),
        ),
    )
    catalog["Gain"] = PedalboardFXSpec(
        name="Gain",
        builder=lambda: Gain(gain_db=0.0),
        params=(
            PedalboardParamSpec("dB", "gain_db", -30.0, 30.0, 0.0),
        ),
    )
    catalog["Bitcrush"] = PedalboardFXSpec(
        name="Bitcrush",
        # pedalboard Bitcrush: bit_depth lower = more lo-fi. Default
        # 32 = effectively no crush; user dials down to hear the
        # effect. Range 4..32 keeps the GUI slider linear-musical.
        builder=lambda: Bitcrush(bit_depth=32),
        params=(
            PedalboardParamSpec("bits", "bit_depth", 4.0, 32.0, 32.0),
        ),
    )
    catalog["Highpass"] = PedalboardFXSpec(
        name="Highpass",
        builder=lambda: HighpassFilter(cutoff_frequency_hz=20.0),
        params=(
            PedalboardParamSpec("cutoff", "cutoff_frequency_hz",
                                20.0, 2000.0, 20.0),
        ),
    )
    catalog["Lowpass"] = PedalboardFXSpec(
        name="Lowpass",
        builder=lambda: LowpassFilter(cutoff_frequency_hz=20000.0),
        params=(
            PedalboardParamSpec("cutoff", "cutoff_frequency_hz",
                                200.0, 20000.0, 20000.0),
        ),
    )
    return catalog


# Built once at import time so repeated lookups are cheap. Empty when
# pedalboard isn't installed — callers should treat that as "no FX
# surface on this run" rather than an error.
PEDALBOARD_FX_CATALOG: dict[str, PedalboardFXSpec] = _pedalboard_catalog()

# Default slot loadout for new sampler FX chains. Two slots —
# Distortion in slot 0, Delay in slot 1 — matching the original
# fixed-chain behaviour before #33 introduced the type pickers.
_DEFAULT_FX_SLOT_KEYS: tuple[str, ...] = ("Distortion", "Delay")


@dataclass
class _FXSlot:
    """One slot in a port's pedalboard chain. Each slot tracks its
    FX type (key into :data:`PEDALBOARD_FX_CATALOG`), the live plugin
    instance the GUI mutates, and whether the slot is currently
    audible (Power toggle). Power-off swaps the plugin out of the
    Pedalboard chain rather than zeroing its mix, so re-powering
    restores the user's sliders exactly."""

    type_key: str
    plugin: Any
    powered: bool = False


class Sampler:
    """MIDI-triggered WAV sampler.

    One instance covers any number of input ports (each with its own
    bank). The typical wiring has *one* sampler subscribed to both
    ``slackbeatz-voice`` and ``slackbeatz-fx``.

    Parameters
    ----------
    port_banks:
        Map of MIDI input port name → ``{midi_note: wav_path}``. A
        ``note_on`` whose pitch is absent from the port's bank is
        silently ignored (matches hardware pad-sampler convention).
    sample_rate:
        Output sample rate for the audio stream. WAVs at a different
        rate are linearly resampled at load time (cheap, one-time).
    max_polyphony:
        Maximum number of simultaneously-playing voices. When exceeded,
        the oldest voice is evicted (LRU).
    """

    # Length of the release-envelope tail in seconds.
    _RELEASE_S: float = 0.05

    def __init__(
        self,
        port_banks: dict[str, dict[int, Path]],
        *,
        sample_rate: int = 44100,
        max_polyphony: int = 16,
    ) -> None:
        self.sample_rate = sample_rate
        self.max_polyphony = max_polyphony

        # Per-port subscriptions, keyed by port name.
        self._ports: dict[str, _PortListener] = {
            name: _PortListener(port_name=name, bank=dict(bank))
            for name, bank in port_banks.items()
        }

        # WAV cache: path → (audio, sample_rate). Avoids re-reading from
        # disk on every note_on.
        self._wav_cache: dict[Path, object] = {}

        # Active voices, oldest first (OrderedDict so LRU is a single
        # ``popitem(last=False)``). Keyed by a monotonically-increasing
        # voice id so two strikes of the same note coexist briefly.
        self._voices: "OrderedDict[int, _Voice]" = OrderedDict()
        self._voice_lock = threading.Lock()
        self._next_voice_id = 0

        # Audio output stream — created in :meth:`start`.
        self._stream = None
        self._started = False

        # Per-port mix-bus gain (post-mix, pre-output). Defaults to 1.0
        # (unity). Driven from the slackbeatz Mixer GUI tab via
        # :meth:`set_port_gain`.
        self._port_gains: dict[str, float] = {}
        # Per-port pedalboard FX chains, plus per-port slot state so
        # the Mixer GUI can render type-pickers + power toggles for
        # each slot. The chains are mutable from the GUI thread via
        # :meth:`set_slot_fx` / :meth:`set_slot_power`; the audio
        # callback reads atomically via dict.get.
        self._fx_chains: dict[str, object] = {}  # port_name → pedalboard.Pedalboard
        self._fx_slots: dict[str, list[_FXSlot]] = {}  # port_name → [slot0, slot1, ...]

    # ------------------------------------------------------------------
    # Bank management
    # ------------------------------------------------------------------

    def set_sample(self, port_name: str, midi_note: int, wav_path: Path) -> None:
        """Map *midi_note* on *port_name* to *wav_path*. Creates the
        per-port bank if it doesn't exist yet."""
        listener = self._ports.get(port_name)
        if listener is None:
            listener = _PortListener(port_name=port_name, bank={})
            self._ports[port_name] = listener
            if self._started:
                self._open_listener(listener)
        listener.bank[int(midi_note)] = Path(wav_path)
        # Drop the cached audio so a re-set picks up file changes.
        self._wav_cache.pop(Path(wav_path), None)

    def remove_sample(self, port_name: str, midi_note: int) -> None:
        """Unmap *midi_note* on *port_name*. Silent on missing entries."""
        listener = self._ports.get(port_name)
        if listener is not None:
            listener.bank.pop(int(midi_note), None)

    def get_bank(self, port_name: str) -> dict[int, Path]:
        """Snapshot of the current bank for *port_name*. Editing the
        returned dict has no effect — use :meth:`set_sample` /
        :meth:`remove_sample`."""
        listener = self._ports.get(port_name)
        return dict(listener.bank) if listener is not None else {}

    # ------------------------------------------------------------------
    # Per-port mix-bus controls — drive the slackbeatz Mixer tab
    # ------------------------------------------------------------------

    def set_port_gain(self, port_name: str, gain: float) -> None:
        """Multiplier (0.0–N) applied to *port_name*'s mix before it
        sums into the master output buffer. 1.0 = unity. Lock-free —
        the audio callback reads via dict.get(name, 1.0), so a torn
        read just gives the old value for one block."""
        self._port_gains[port_name] = max(0.0, float(gain))

    def get_port_gain(self, port_name: str) -> float:
        """Current per-port gain (default 1.0 if never set)."""
        return float(self._port_gains.get(port_name, 1.0))

    def enable_fx(
        self,
        port_name: str,
        slot_keys: tuple[str, ...] = _DEFAULT_FX_SLOT_KEYS,
    ) -> bool:
        """Install an FX chain on *port_name* with one slot per
        entry of *slot_keys* (each a key into
        :data:`PEDALBOARD_FX_CATALOG`). Default loadout: Distortion
        + Delay, both Power=off so the chain is audibly transparent
        until the user opts in via the Mixer's Power toggles.

        Returns True on success, False if pedalboard isn't installed
        (the Mixer GUI then renders the strip without FX controls +
        prints a one-line install hint)."""
        if not PEDALBOARD_FX_CATALOG:
            import sys
            print(
                "slackbeatz sampler: pedalboard not installed — FX chain "
                f"on {port_name!r} skipped. Install via "
                "`pip install slackbeatz[tts]` (same dep as TTS post-FX).",
                file=sys.stderr,
            )
            return False
        slots = [
            _FXSlot(
                type_key=key,
                plugin=PEDALBOARD_FX_CATALOG[key].builder(),
                powered=False,
            )
            for key in slot_keys
            if key in PEDALBOARD_FX_CATALOG
        ]
        self._fx_slots[port_name] = slots
        self._rebuild_chain(port_name)
        return True

    def set_slot_fx(
        self, port_name: str, slot_idx: int, type_key: str,
    ) -> bool:
        """Swap slot *slot_idx*'s FX type to *type_key* (a key into
        :data:`PEDALBOARD_FX_CATALOG`). The slot keeps its Power
        state but loses its prior slider values — switching FX type
        builds a fresh plugin with the new type's defaults. Returns
        True on success."""
        slots = self._fx_slots.get(port_name)
        if slots is None or slot_idx >= len(slots):
            return False
        spec = PEDALBOARD_FX_CATALOG.get(type_key)
        if spec is None:
            return False
        slots[slot_idx] = _FXSlot(
            type_key=type_key,
            plugin=spec.builder(),
            powered=slots[slot_idx].powered,
        )
        self._rebuild_chain(port_name)
        return True

    def set_slot_power(
        self, port_name: str, slot_idx: int, powered: bool,
    ) -> bool:
        """Toggle slot *slot_idx*'s Power state. Power-off removes
        the slot's plugin from the live Pedalboard chain (vs zeroing
        its mix) so re-powering restores the user's slider values
        exactly. Returns True on success."""
        slots = self._fx_slots.get(port_name)
        if slots is None or slot_idx >= len(slots):
            return False
        slots[slot_idx].powered = bool(powered)
        self._rebuild_chain(port_name)
        return True

    def get_slot_state(
        self, port_name: str, slot_idx: int,
    ) -> Optional[_FXSlot]:
        """Return the live :class:`_FXSlot` for read-only inspection
        (type_key + plugin instance + powered flag). The Mixer GUI
        mutates plugin attributes (``slot.plugin.drive_db = 12.0``)
        in place — pedalboard supports that on a running chain."""
        slots = self._fx_slots.get(port_name)
        if slots is None or slot_idx >= len(slots):
            return None
        return slots[slot_idx]

    def get_fx_chain(self, port_name: str):
        """Return the live :class:`pedalboard.Pedalboard` chain for
        *port_name*, or ``None`` if FX aren't enabled. Kept for
        backward compat with the pre-#33 GUI which mutated the chain
        directly; new code should use :meth:`get_slot_state` /
        :meth:`set_slot_fx`."""
        return self._fx_chains.get(port_name)

    def _rebuild_chain(self, port_name: str) -> None:
        """Atomically swap *port_name*'s Pedalboard chain to a fresh
        instance containing only the currently-powered slot plugins.
        Called whenever slot type / power state changes. The audio
        callback's ``dict.get`` is atomic in CPython, so the worst
        case is one audio block still using the old chain — no
        torn-state risk."""
        if not PEDALBOARD_FX_CATALOG:
            return
        from pedalboard import Pedalboard
        slots = self._fx_slots.get(port_name, [])
        live_plugins = [s.plugin for s in slots if s.powered]
        if live_plugins:
            self._fx_chains[port_name] = Pedalboard(live_plugins)
        else:
            # No plugins powered → no chain at all. The audio
            # callback's `if chain is not None` skip avoids the
            # round-trip into pedalboard for a no-op.
            self._fx_chains.pop(port_name, None)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the audio output stream + spawn one reader thread per
        subscribed MIDI port. Idempotent.

        Raises :class:`RuntimeError` with a clear install hint if
        ``sounddevice`` / ``soundfile`` aren't available."""
        if self._started:
            return
        try:
            import sounddevice as sd  # noqa: F401 — probe
            import soundfile  # noqa: F401 — probe
            import numpy  # noqa: F401 — probe
        except ImportError as e:
            raise RuntimeError(
                f"slackbeatz sampler needs sounddevice + soundfile + numpy "
                f"({e}). Install with:\n"
                f"  pip install sounddevice soundfile numpy"
            ) from e

        import sounddevice as sd

        # Output stream — float32 stereo. The callback mixes all
        # currently-active voices.
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=2,
            dtype="float32",
            callback=self._audio_callback,
        )
        self._stream.start()

        for listener in self._ports.values():
            self._open_listener(listener)

        self._started = True

    def stop(self) -> None:
        """Tear down the audio stream + all reader threads. Safe to
        call twice or before :meth:`start`."""
        for listener in self._ports.values():
            listener.stop_flag.set()
            # Closing the input port wakes a blocked ``port.receive()``
            # so the reader thread can observe stop_flag.
            if listener.midi_port is not None:
                try:
                    listener.midi_port.close()
                except Exception:
                    pass
                listener.midi_port = None
            if listener.thread is not None:
                listener.thread.join(timeout=1.0)
                listener.thread = None

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        with self._voice_lock:
            self._voices.clear()

        self._started = False

    # ------------------------------------------------------------------
    # Internal: MIDI port subscription
    # ------------------------------------------------------------------

    def _open_listener(self, listener: _PortListener) -> None:
        """Open the MIDI input + spawn the reader thread for one port.

        On macOS / Linux, ``mido.open_input(name)`` subscribes to a
        virtual MIDI source published by another mido output (in our
        case, :class:`MultiPortSink`). Failure (port missing) is
        logged but not fatal — the rest of the sampler keeps working.
        """
        if listener.midi_port is not None:
            return
        import mido
        try:
            listener.midi_port = mido.open_input(listener.port_name)
        except (OSError, IOError) as e:
            # Port doesn't exist yet, or another process holds it.
            # Don't crash — the sampler should be permissive about
            # the surrounding wiring. Caller can retry by calling
            # start() again after MultiPortSink.open().
            import sys
            print(
                f"slackbeatz sampler: couldn't subscribe to "
                f"{listener.port_name!r} ({e})",
                file=sys.stderr,
            )
            return

        listener.stop_flag.clear()
        listener.thread = threading.Thread(
            target=self._reader_loop, args=(listener,), daemon=True,
        )
        listener.thread.start()

    def _reader_loop(self, listener: _PortListener) -> None:
        """Block-read MIDI messages from one port + dispatch them."""
        port = listener.midi_port
        if port is None:
            return
        try:
            for msg in port:
                if listener.stop_flag.is_set():
                    return
                if msg.type == "note_on" and getattr(msg, "velocity", 0) > 0:
                    self._on_note_on(listener, msg.note, msg.velocity)
                elif msg.type == "note_off" or (
                    msg.type == "note_on" and getattr(msg, "velocity", 0) == 0
                ):
                    self._on_note_off(listener, msg.note)
        except Exception:
            # Port closed mid-iter → we're shutting down. Quiet exit.
            return

    # ------------------------------------------------------------------
    # Internal: note handlers
    # ------------------------------------------------------------------

    def _on_note_on(
        self, listener: _PortListener, midi_note: int, velocity: int,
    ) -> None:
        wav_path = listener.bank.get(int(midi_note))
        if wav_path is None:
            return  # unmapped pad — silent, no error
        audio = self._load_wav(wav_path)
        if audio is None:
            return

        voice = _Voice(
            audio=audio,
            cursor=0,
            velocity_gain=max(0.0, min(1.0, velocity / 127.0)),
            release_total=max(1, int(self._RELEASE_S * self.sample_rate)),
            port_name=listener.port_name,
        )
        with self._voice_lock:
            vid = self._next_voice_id
            self._next_voice_id += 1
            self._voices[vid] = voice
            # LRU eviction.
            while len(self._voices) > self.max_polyphony:
                self._voices.popitem(last=False)

    def _on_note_off(self, listener: _PortListener, midi_note: int) -> None:
        # Trigger release on the most recently started voice for this
        # port whose source path matches the pad's WAV. We don't track
        # voice→note explicitly; instead we look up the pad's audio
        # path and apply release to any voice still playing that
        # buffer. This is good enough for the v1 use cases (drum hits,
        # spoken phrases) and avoids per-voice metadata bloat.
        wav_path = listener.bank.get(int(midi_note))
        if wav_path is None:
            return
        audio = self._wav_cache.get(Path(wav_path))
        if audio is None:
            return
        with self._voice_lock:
            # Apply release to voices still playing this exact buffer
            # whose release isn't already running.
            for v in self._voices.values():
                if v.audio is audio and v.release_left is None:
                    v.release_left = v.release_total

    # ------------------------------------------------------------------
    # Internal: audio loading + mixing
    # ------------------------------------------------------------------

    def _load_wav(self, wav_path: Path):
        """Read *wav_path* into a float32 stereo numpy array at the
        sampler's output rate. Caches the result.

        Returns ``None`` on read failure (logged to stderr)."""
        cached = self._wav_cache.get(wav_path)
        if cached is not None:
            return cached
        try:
            import numpy as np
            import soundfile as sf
        except ImportError:
            return None
        try:
            data, src_sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
        except Exception as e:
            import sys
            print(f"slackbeatz sampler: failed to load {wav_path}: {e}",
                  file=sys.stderr)
            return None

        # Resample to stream rate via naive linear interpolation. WAVs
        # are short (single hits / phrases of a few seconds), so this
        # one-time cost is negligible — and it dodges adding scipy /
        # librosa as a dep.
        if src_sr != self.sample_rate:
            ratio = self.sample_rate / src_sr
            n_out = int(round(data.shape[0] * ratio))
            if n_out > 0:
                t_out = np.linspace(
                    0.0, data.shape[0] - 1, num=n_out, dtype=np.float64,
                )
                t_in = np.arange(data.shape[0], dtype=np.float64)
                resampled = np.stack([
                    np.interp(t_out, t_in, data[:, ch])
                    for ch in range(data.shape[1])
                ], axis=1).astype(np.float32)
                data = resampled

        # Mono → stereo: duplicate the channel.
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        elif data.shape[1] > 2:
            # Down-mix anything weirder (5.1 etc.) to stereo by
            # averaging — slackbeatz isn't a surround sampler.
            data = np.stack([
                data[:, :data.shape[1] // 2].mean(axis=1),
                data[:, data.shape[1] // 2:].mean(axis=1),
            ], axis=1).astype(np.float32)

        self._wav_cache[wav_path] = data
        return data

    def _audio_callback(self, outdata, frames, _time_info, _status) -> None:
        """sounddevice OutputStream callback. Mixes active voices into
        *outdata* (shape: ``(frames, 2)``). Runs on the portaudio
        thread — keep it cheap, no I/O, no Python locks beyond the
        single voice-list snapshot.

        Pipeline: per-voice render → per-port mix buffer → optional
        per-port pedalboard FX chain (Phase 3) → per-port gain →
        sum into output → soft-clip. The per-port stage exists so the
        mixer GUI can fade / FX each subscribed port independently."""
        import numpy as np

        outdata.fill(0.0)
        # Snapshot voice ids so we don't hold the lock during mixing.
        with self._voice_lock:
            voice_items = list(self._voices.items())

        # Per-port mix buffers, allocated lazily as we encounter voices
        # from a port. Skipping zero-allocs for ports with no active
        # voices keeps the no-sampler-traffic path almost free.
        port_bufs: dict[str, "np.ndarray"] = {}

        finished: list[int] = []
        for vid, v in voice_items:
            remaining = v.audio.shape[0] - v.cursor
            if remaining <= 0:
                finished.append(vid)
                continue
            n = min(frames, remaining)
            chunk = v.audio[v.cursor:v.cursor + n]
            gain = v.velocity_gain

            buf = port_bufs.get(v.port_name)
            if buf is None:
                buf = np.zeros_like(outdata)
                port_bufs[v.port_name] = buf

            if v.release_left is None:
                buf[:n] += chunk * gain
            else:
                # Apply a linear-fade envelope over up to release_total
                # frames, then stop.
                env_start = v.release_left
                env_end = max(0, env_start - n)
                env = np.linspace(
                    env_start / v.release_total,
                    env_end / v.release_total,
                    n,
                    dtype=np.float32,
                )[:, None]
                buf[:n] += chunk * gain * env
                v.release_left = env_end
                if env_end <= 0:
                    finished.append(vid)
                    continue

            v.cursor += n
            if v.cursor >= v.audio.shape[0]:
                finished.append(vid)

        if finished:
            with self._voice_lock:
                for vid in finished:
                    self._voices.pop(vid, None)

        # Per-port FX chain (Phase 3) + per-port gain, then sum into
        # the master output buffer.
        for port_name, buf in port_bufs.items():
            chain = self._fx_chains.get(port_name)
            if chain is not None:
                try:
                    # pedalboard.process expects (channels, samples) or
                    # (samples, channels); our buf is (samples, 2). The
                    # reset=False keeps state (delay lines etc.) across
                    # callback invocations.
                    buf[:] = chain.process(
                        buf, self.sample_rate, reset=False,
                    )
                except Exception:
                    # Don't kill the audio thread if a chain throws —
                    # just skip FX for this block.
                    pass
            port_gain = self._port_gains.get(port_name, 1.0)
            if port_gain != 1.0:
                buf *= port_gain
            outdata += buf

        # Soft clip to [-1, 1] to defend against accidental mix overflow
        # from many simultaneous voices.
        np.clip(outdata, -1.0, 1.0, out=outdata)


# --------------------------------------------------------------------------
# Module-level handle for generators
# --------------------------------------------------------------------------
#
# Speech / sample generators run inside the scheduler and need to push
# WAV-bank entries into the Sampler at resolve time. Threading the
# Sampler instance through PartContext would bloat every generator's
# signature for one specialised pair of gens, so instead the CLI parks
# the active sampler here at startup. Generators that don't need it
# never look — :func:`get_active_sampler` simply returns ``None``.

_ACTIVE_SAMPLER: Optional[Sampler] = None


def set_active_sampler(sampler: Optional[Sampler]) -> None:
    """Register *sampler* as the process-wide active sampler. Called
    by :mod:`slackbeatz.cli` after :meth:`Sampler.start` succeeds.
    Pass ``None`` to clear (e.g. on shutdown)."""
    global _ACTIVE_SAMPLER
    _ACTIVE_SAMPLER = sampler


def get_active_sampler() -> Optional[Sampler]:
    """Return the currently-active sampler, or ``None`` if no
    sampler is running (e.g. ``--surge`` is off, or sounddevice
    isn't installed)."""
    return _ACTIVE_SAMPLER
