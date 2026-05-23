"""Sink abstraction — where MIDI messages go.

The scheduler computes the right wall-clock moment to dispatch each
message (via the :class:`ClockSource`) and then just calls
``sink.send(msg)``. Sinks don't deal with timing themselves; they only
care about getting a single :class:`mido.Message` to the right
destination.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import mido


class Sink(ABC):
    """Destination for MIDI messages emitted by the scheduler."""

    def open(self) -> None:
        """Acquire any resources (open a port, open a file, …)."""

    def close(self) -> None:
        """Release resources. Called from a ``finally`` so it must be
        safe to call on a half-opened sink."""

    @abstractmethod
    def send(self, msg: mido.Message) -> None:
        """Dispatch one MIDI message."""
