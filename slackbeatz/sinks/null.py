"""No-op sink that drops every message.

Used as :class:`CompositeSink`'s default in ableton-blackhole mode,
where every relevant MIDI channel is routed through MultiPortSink to
a dedicated virtual port and there's no general MIDI destination
worth opening (no FluidSynth, no IAC). Channels that aren't in the
override map silently fall through to here and disappear — which is
exactly the desired behaviour: SB only emits notes on the channels
configured in the setup, so anything unrouted is by definition
something we don't want to broadcast.
"""

from __future__ import annotations

import mido

from .base import Sink


class NullSink(Sink):
    """Sink that accepts and discards every message."""

    def send(self, msg: mido.Message) -> None:  # noqa: ARG002
        pass
