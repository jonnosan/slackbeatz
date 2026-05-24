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
from typing import Optional


# sounddevice + soundfile + numpy are optional at import time so callers
# without these can still ``import slackbeatz.sampler`` — they're only
# required when the Sampler is actually constructed + started. This
# matches how :mod:`surge_host` lazy-imports python-osc.


@dataclass
class _Voice:
    """One currently-playing sample. ``cursor`` is the next-sample
    index into ``audio``. After ``note_off``, ``release_left`` counts
    down to a soft fade (set to the release-envelope length in frames)
    so short notes don't end abruptly."""

    audio: object  # numpy.ndarray, shape (frames, channels)
    cursor: int = 0
    velocity_gain: float = 1.0
    # None = note still held; int = remaining release-envelope frames.
    release_left: Optional[int] = None
    release_total: int = 1


@dataclass
class _PortListener:
    """Per-port MIDI subscription bundle: the open mido input port + the
    thread reading from it. Owned by :class:`Sampler`."""

    port_name: str
    bank: dict[int, Path]
    midi_port: object = None   # mido.ports.BaseInput
    thread: Optional[threading.Thread] = None
    stop_flag: threading.Event = field(default_factory=threading.Event)


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
        single voice-list snapshot."""
        import numpy as np

        outdata.fill(0.0)
        # Snapshot voice ids so we don't hold the lock during mixing.
        with self._voice_lock:
            voice_items = list(self._voices.items())

        finished: list[int] = []
        for vid, v in voice_items:
            remaining = v.audio.shape[0] - v.cursor
            if remaining <= 0:
                finished.append(vid)
                continue
            n = min(frames, remaining)
            chunk = v.audio[v.cursor:v.cursor + n]
            gain = v.velocity_gain

            if v.release_left is None:
                outdata[:n] += chunk * gain
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
                outdata[:n] += chunk * gain * env
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
