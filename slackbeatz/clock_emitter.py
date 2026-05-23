"""MIDI Clock 0xF8 emitter.

When :attr:`Player.emit_clock` is True, the playback worker spawns a
sibling thread that pumps ``mido.Message("clock")`` messages out of
the realtime port at 24 PPQN — the rate every MIDI-Clock-aware
receiver (Roland TR-8, Elektron Digitakt, Eurorack via clock-to-CV,
Ableton Live in External Sync mode) expects.

The emitter also fires the transport bytes that gate that pulse:

* ``start``    (0xFA) — when playback begins at tick 0
* ``continue`` (0xFB) — when playback begins at a non-zero tick (seek)
* ``stop``     (0xFC) — when playback ends

Implementation notes:

* Timing uses absolute-target scheduling
  (``target = start_perf + n * interval``) rather than incremental
  ``time.sleep(interval)`` so drift doesn't accumulate over long sets.
* The interval is recomputed from the current tempo each tick, so
  per-part tempo changes (and live ``/tempo N`` slider moves) are
  reflected without restarting the emitter.
* We open a *separate* :class:`RealtimeSink` from the scheduler's so
  the two threads don't have to share a mido port (rtmidi's port-send
  thread-safety is platform-dependent on macOS CoreMIDI).
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import mido

from slackbeatz.engine.clock import PPQ, TempoMap
from slackbeatz.sinks.realtime import RealtimeSink


# MIDI Clock spec: 24 pulses per quarter note.
CLOCK_PPQN = 24


class ClockEmitter:
    """Sends 0xF8 clock + transport bytes on a daemon thread."""

    def __init__(
        self,
        port_name: str,
        tempo_map: TempoMap,
        stop_event: threading.Event,
        *,
        start_at_tick: int = 0,
    ) -> None:
        self.port_name = port_name
        self.tempo_map = tempo_map
        self.stop_event = stop_event
        self.start_at_tick = start_at_tick
        self._thread: Optional[threading.Thread] = None
        self._sink: Optional[RealtimeSink] = None

    def start(self) -> None:
        """Open the port, send Start/Continue, kick off the daemon thread."""
        self._sink = RealtimeSink(port_name=self.port_name)
        try:
            self._sink.open()
        except Exception:
            # Port unavailable — silently no-op. Errors here would
            # otherwise abort the whole playback worker; downstream
            # gear simply won't sync.
            self._sink = None
            return
        # Transport byte. Continue (0xFB) signals "resume from previous
        # position"; some gear interprets it via the most recent SPP.
        opening = "continue" if self.start_at_tick > 0 else "start"
        try:
            self._sink._port.send(mido.Message(opening))  # type: ignore[union-attr]
        except Exception:
            pass

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Send Stop and close the port. Idempotent."""
        # The thread sees stop_event via its own caller; we just close
        # the port + send the byte. Caller may also set stop_event.
        if self._sink is None:
            return
        try:
            self._sink._port.send(mido.Message("stop"))  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            self._sink.close()
        except Exception:
            pass
        self._sink = None
        # Don't join the thread here — it'll exit when stop_event is
        # set by the caller, and we're a daemon so interpreter exit
        # cleans up regardless.

    def _run(self) -> None:
        """Emit 0xF8 ticks at 24 PPQN using absolute-target scheduling."""
        if self._sink is None:
            return
        port = self._sink._port  # type: ignore[union-attr]

        # Wall-clock origin: now corresponds to tick start_at_tick.
        start_perf = time.perf_counter()
        # The internal tick spacing for one MIDI Clock pulse:
        ticks_per_midi_clock = PPQ / CLOCK_PPQN  # = 20 at PPQ 480

        clock_index = 0
        try:
            while not self.stop_event.is_set():
                # What absolute song-tick does THIS clock pulse fall at?
                song_tick = int(self.start_at_tick + clock_index * ticks_per_midi_clock)
                # Per-tempo-segment scheduling: ask the tempo map what
                # wall-time that tick should land at. Subtract the
                # song-time consumed before start_at_tick so we measure
                # from when WE started, not from song tick 0.
                target_offset = (
                    self.tempo_map.time_at(song_tick)
                    - self.tempo_map.time_at(self.start_at_tick)
                )
                target_wall = start_perf + target_offset
                now = time.perf_counter()
                if target_wall > now:
                    # Wait, but in chunks so stop_event is responsive.
                    remaining = target_wall - now
                    if remaining > 0.05:
                        time.sleep(0.05)
                        continue
                    time.sleep(remaining)
                # Send the clock pulse.
                try:
                    port.send(mido.Message("clock"))
                except Exception:
                    break
                clock_index += 1
        finally:
            # Caller-side stop() will fire the Stop byte + close the
            # port — we don't do it here so a "play another song" path
            # (where the caller stops + immediately re-emitter-starts)
            # doesn't bounce the port.
            pass
