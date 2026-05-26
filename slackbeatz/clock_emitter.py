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
* ``songpos``  (0xF2) — Song Position Pointer; sent before Start/Continue
  and on seek so the slave knows where in the song we are. Quantised
  to 16th-notes per the MIDI spec.

Implementation notes:

* Timing uses absolute-target scheduling
  (``target = start_perf + n * interval``) rather than incremental
  ``time.sleep(interval)`` so drift doesn't accumulate over long sets.
* The interval is recomputed from the current tempo each tick, so
  per-part tempo changes (and live ``/tempo N`` slider moves) are
  reflected without restarting the emitter.
* We open a *separate* MIDI port from the scheduler's so the two
  threads don't have to share a mido port (rtmidi's port-send
  thread-safety is platform-dependent on macOS CoreMIDI). When the
  target port doesn't exist (e.g. ``slackbeatz-transport-out`` in
  ableton-blackhole mode), we create it as a virtual CoreMIDI port.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import mido

from slackbeatz.engine.clock import PPQ, TempoMap
from slackbeatz.sinks.realtime import RealtimeSink, available_ports


# MIDI Clock spec: 24 pulses per quarter note.
CLOCK_PPQN = 24

# Song Position Pointer unit = 1/16th note = PPQ/4 ticks.
_SPP_TICK_UNIT = PPQ // 4


def _tick_to_spp_value(tick: int) -> int:
    """Convert song-tick → 14-bit SPP value (clamped to spec max)."""
    return max(0, min(16383, tick // _SPP_TICK_UNIT))


class ClockEmitter:
    """Sends 0xF8 clock + transport bytes on a daemon thread."""

    def __init__(
        self,
        port_name: str,
        tempo_map: TempoMap,
        stop_event: threading.Event,
        *,
        start_at_tick: int = 0,
        transport_listener=None,
    ) -> None:
        self.port_name = port_name
        self.tempo_map = tempo_map
        self.stop_event = stop_event
        self.start_at_tick = start_at_tick
        self.transport_listener = transport_listener
        self._thread: Optional[threading.Thread] = None
        self._port: Optional[mido.ports.BaseOutput] = None
        self._sink: Optional[RealtimeSink] = None  # legacy path; None when virtual

    def _open_port(self) -> None:
        """Open ``port_name`` for output.

        Tries the existing-port path first (RealtimeSink with
        all-notes-off-on-close). If the named port doesn't exist,
        creates it as a virtual CoreMIDI source — that's how the
        ableton-blackhole mode's ``slackbeatz-transport-out`` port
        comes into being (Ableton subscribes to it as a Sync source).
        """
        ports = available_ports()
        if self.port_name in ports:
            self._sink = RealtimeSink(port_name=self.port_name)
            try:
                self._sink.open()
            except Exception:
                self._sink = None
                return
            self._port = self._sink._port  # type: ignore[union-attr]
            return
        # Virtual port path: open with virtual=True so Ableton (or any
        # other subscriber) can listen to it.
        try:
            self._port = mido.open_output(self.port_name, virtual=True)
        except Exception:
            self._port = None

    def start(self) -> None:
        """Open the port, send SPP + Start/Continue, kick off the daemon."""
        self._open_port()
        if self._port is None:
            # Port unavailable — silently no-op. Errors here would
            # otherwise abort the whole playback worker; downstream
            # gear simply won't sync.
            return
        # SPP first — Per MIDI spec, slaves use the most recent SPP
        # at the next Start/Continue to locate. Always emit so a
        # mid-song Continue lands on the right beat.
        self._note_outbound("songpos")
        try:
            self._port.send(
                mido.Message("songpos", pos=_tick_to_spp_value(self.start_at_tick))
            )
        except Exception:
            pass
        # Transport byte. Continue (0xFB) signals "resume from previous
        # position".
        opening = "continue" if self.start_at_tick > 0 else "start"
        self._note_outbound(opening)
        try:
            self._port.send(mido.Message(opening))
        except Exception:
            pass

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _note_outbound(self, kind: str) -> None:
        """Tell the paired TransportListener we just sent *kind*."""
        if self.transport_listener is not None:
            try:
                self.transport_listener.note_outbound_event(kind)
            except Exception:
                pass

    def stop(self) -> None:
        """Send Stop and drop the port reference. Idempotent.

        We send the Stop byte so downstream slaves get a clean shutdown
        signal, but we do NOT explicitly close virtual MIDI ports
        created via ``mido.open_output(..., virtual=True)``. CoreMIDI
        via python-rtmidi can block port close when Ableton has the
        port subscribed (Sync IN), which would freeze SB on exit.
        Daemon-thread cleanup + process exit reap the port safely.
        Existing-port path (RealtimeSink) is still close()d because
        those subscribers attached to OUR port, not the other way.
        """
        if self._port is None:
            return
        self._note_outbound("stop")
        try:
            self._port.send(mido.Message("stop"))
        except Exception:
            pass
        # Only call close() on existing-port sinks (no virtual=True
        # involved). Virtual ports are dropped on the floor for
        # process exit to clean up.
        if self._sink is not None:
            try:
                self._sink.close()
            except Exception:
                pass
        self._port = None
        self._sink = None
        # Don't join the thread here — it'll exit when stop_event is
        # set by the caller, and we're a daemon so interpreter exit
        # cleans up regardless.

    def _run(self) -> None:
        """Emit 0xF8 ticks at 24 PPQN using absolute-target scheduling."""
        if self._port is None:
            return
        port = self._port

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
