"""Inbound MIDI transport listener.

Pairs with :class:`slackbeatz.clock_emitter.ClockEmitter` to make
transport bidirectional in ableton-blackhole mode (see
[[backend_is_setup]]). SB stays the clock master — Ableton is
synced *from* SB's clock — but Ableton (or any other slave) can
still *initiate* Start/Stop/Continue/SPP and have it drive SB's
local transport. Pressing play in either app starts both; pressing
stop in either stops both; dragging the playhead in either seeks
the other.

Implementation:

* Opens a virtual CoreMIDI input port (default
  ``slackbeatz-transport-in``) so any DAW can find and send to it.
* Daemon thread reads transport messages (System Real-Time +
  System Common) and dispatches to callbacks the Player wires up.
* MIDI Clock (0xF8) is ignored — SB is the master and computes its
  own tempo; we never want to slave to incoming clock.
* Loop-prevention: the listener exposes :meth:`note_outbound_event`,
  which the ClockEmitter calls each time it sends a Start/Stop/SPP.
  Inbound events within a small debounce window are dropped, so a
  Start we just emitted (which Ableton echoes back) doesn't trigger
  a redundant SB play call.

SPP value is in 16th-notes (MIDI spec); converted back to absolute
song-tick using ``PPQ // 4`` per unit so the dispatcher can use
``Player.seek_to_tick``.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import mido

from slackbeatz.engine.clock import PPQ


_SPP_TICK_UNIT = PPQ // 4

# How long an outbound Start/Stop/SPP suppresses inbound echoes of
# the same kind. 50ms is generous — Ableton's loopback echo arrives
# within a few ms but a busy daemon could be slower.
_ECHO_DEBOUNCE_S = 0.05


class TransportListener:
    """Reads inbound transport messages, dispatches to player callbacks.

    The player provides three callbacks:

    * ``on_play(from_tick: int)`` — Start sets ``from_tick=0``; Continue
      uses the most recent SPP-derived tick (defaults to 0).
    * ``on_stop()`` — Stop received.
    * ``on_seek(tick: int)`` — SPP received while currently playing;
      idle SPP just updates the next-Continue position.

    The callbacks are invoked on the listener's daemon thread; callers
    that touch Tk should marshal to the main thread themselves.
    """

    def __init__(
        self,
        *,
        port_name: str = "slackbeatz-transport-in",
        on_play: Callable[[int], None],
        on_stop: Callable[[], None],
        on_seek: Callable[[int], None],
    ) -> None:
        self.port_name = port_name
        self._on_play = on_play
        self._on_stop = on_stop
        self._on_seek = on_seek
        self._port: Optional[mido.ports.BaseInput] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pending_spp_tick = 0
        # Per-kind debounce: outbound transport at the same kind
        # within _ECHO_DEBOUNCE_S suppresses the inbound dispatch.
        self._suppress_until: dict[str, float] = {
            "start": 0.0, "continue": 0.0, "stop": 0.0, "songpos": 0.0,
        }
        self._suppress_lock = threading.Lock()

    def start(self) -> None:
        """Open the virtual port + spawn the reader thread."""
        try:
            self._port = mido.open_input(self.port_name, virtual=True)
        except Exception:
            self._port = None
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the reader thread + drop the port reference.

        We do NOT call ``port.close()`` — python-rtmidi's CoreMIDI
        backend can block on close when subscribers (e.g. Ableton's
        Sync OUT) are still attached, causing SB's exit to hang.
        Dropping the reference + daemon-thread cleanup + process
        exit reaps the virtual port safely.
        """
        self._stop_event.set()
        self._port = None

    def note_outbound_event(self, kind: str) -> None:
        """Record that we just SENT *kind* — suppresses inbound echo.

        Called by the ClockEmitter (and SB's seek path) each time a
        transport message leaves on the outbound port. *kind* is one
        of ``"start"``, ``"continue"``, ``"stop"``, ``"songpos"``.
        """
        now = time.monotonic()
        with self._suppress_lock:
            self._suppress_until[kind] = now + _ECHO_DEBOUNCE_S

    def _is_suppressed(self, kind: str) -> bool:
        now = time.monotonic()
        with self._suppress_lock:
            return now < self._suppress_until.get(kind, 0.0)

    def _run(self) -> None:
        port = self._port
        if port is None:
            return
        while not self._stop_event.is_set():
            try:
                # iter_pending so a closed port + stop_event don't
                # deadlock; small sleep to keep CPU low between polls.
                msg = port.poll()
            except Exception:
                break
            if msg is None:
                time.sleep(0.005)
                continue
            t = msg.type
            if t == "clock":
                # Ignore — SB is the clock master.
                continue
            if t == "songpos":
                spp = int(getattr(msg, "pos", 0))
                tick = spp * _SPP_TICK_UNIT
                self._pending_spp_tick = tick
                if self._is_suppressed("songpos"):
                    continue
                # Live seek dispatch — player decides whether playing.
                try:
                    self._on_seek(tick)
                except Exception:
                    pass
                continue
            if t == "start":
                if self._is_suppressed("start"):
                    continue
                self._pending_spp_tick = 0
                try:
                    self._on_play(0)
                except Exception:
                    pass
                continue
            if t == "continue":
                if self._is_suppressed("continue"):
                    continue
                try:
                    self._on_play(self._pending_spp_tick)
                except Exception:
                    pass
                continue
            if t == "stop":
                if self._is_suppressed("stop"):
                    continue
                try:
                    self._on_stop()
                except Exception:
                    pass
                continue
            # other system messages: ignore
