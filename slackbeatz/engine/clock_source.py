"""Pluggable "what advances time" abstraction.

SB ships with :class:`InternalClock` (the v1 default — drives time with
``time.perf_counter`` and the song's :class:`TempoMap`) and a stub
:class:`ExternalClock` that documents the contract for the future
MIDI-Clock-slaved mode. The scheduler talks to the abstract
:class:`ClockSource` only, so adding the external implementation later
won't touch any other module.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from .clock import PPQ, TempoMap


class ClockSource(ABC):
    """Drives the playhead. Open it, call :meth:`start`, then for each
    event call :meth:`wait_until` and dispatch."""

    ppq: int = PPQ

    def open(self) -> None:
        """Hook for sources that need to acquire resources (e.g. open a
        MIDI input port for external clock)."""

    def close(self) -> None:
        """Hook for releasing resources."""

    @abstractmethod
    def start(self) -> None:
        """Mark tick 0 as 'now'. Must be called before :meth:`wait_until`."""

    @abstractmethod
    def wait_until(self, abs_tick: int) -> None:
        """Block until the playhead reaches *abs_tick*."""


class InternalClock(ClockSource):
    """Master-clock implementation backed by ``time.perf_counter``.

    Sleeps in short increments to avoid CPU spin while staying responsive.
    Uses the supplied :class:`TempoMap` so songs with per-part tempo
    changes time their events correctly.
    """

    def __init__(self, tempo_map: TempoMap) -> None:
        self._tempo_map = tempo_map
        self._t0: float | None = None

    def start(self) -> None:
        self._t0 = time.perf_counter()

    def wait_until(self, abs_tick: int) -> None:
        if self._t0 is None:
            raise RuntimeError("InternalClock.start() not called")
        target_wall = self._t0 + self._tempo_map.time_at(abs_tick)
        # Sleep in chunks so a Ctrl-C is responsive even mid-wait.
        while True:
            remaining = target_wall - time.perf_counter()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))


class ExternalClock(ClockSource):
    """Slave to incoming MIDI Clock (0xF8) on an input port.

    v1 stub — the docstring captures the intended contract so the future
    implementation has a clear target:

    * Open a MIDI input port (``mido.open_input``) and subscribe to clock
      messages. Spec: 24 ticks per quarter note.
    * Multiply each incoming tick into ``PPQ / 24`` (= 20 at PPQ 480)
      internal ticks; events at intermediate positions are dispatched on
      a best-effort basis using inter-tick wall-time interpolation.
    * Maintain a rolling tempo estimate (BPM) from inter-tick wall-time
      so generators that ask for the current tempo get a useful answer.
    * Handle MIDI Start (0xFA) / Stop (0xFC) / Continue (0xFB):
      - Start: reset the playhead to tick 0.
      - Stop: pause; ``wait_until`` returns immediately to let the
        scheduler send all-notes-off.
      - Continue: resume from current tick.
    """

    def __init__(self, port_name: str) -> None:
        self.port_name = port_name

    def open(self) -> None:
        raise NotImplementedError(
            "ExternalClock is not implemented in v1. The scaffolding exists "
            "so the scheduler doesn't need to change when MIDI-Clock slave "
            "mode lands — see the class docstring for the planned contract."
        )

    def start(self) -> None:  # pragma: no cover — guarded by open()
        raise NotImplementedError

    def wait_until(self, abs_tick: int) -> None:  # pragma: no cover
        raise NotImplementedError
