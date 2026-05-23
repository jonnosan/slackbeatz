"""Tick-based MIDI event dataclasses.

Generators yield these; the scheduler converts them to ``mido.Message``s
at dispatch time, splitting a :class:`Note` into a paired ``note_on`` /
``note_off`` and routing the result through the active :class:`Sink`.

All ``tick`` fields are offsets from the start of the *part* the
generator is rendering — the scheduler adds the part's start position to
get absolute song ticks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Note:
    """One pitched event with explicit duration."""

    tick: int  # offset from part start
    duration: int  # ticks
    channel: int  # 1..16
    pitch: int  # 0..127
    velocity: int  # 1..127


@dataclass(frozen=True)
class CC:
    """Control-change message (e.g. filter cutoff sweeps, pan)."""

    tick: int
    channel: int
    controller: int  # 0..127
    value: int  # 0..127


Event = Note | CC


def validate(event: Event) -> None:
    """Cheap sanity-check on an event's MIDI ranges.

    Generators are expected to produce in-range values, but a stray
    out-of-bounds value should fail loudly rather than be silently
    clipped by the MIDI library.
    """
    if not 1 <= event.channel <= 16:
        raise ValueError(f"channel {event.channel} out of 1..16")
    if event.tick < 0:
        raise ValueError(f"tick {event.tick} is negative")
    if isinstance(event, Note):
        if event.duration < 1:
            raise ValueError(f"note duration {event.duration} must be >= 1")
        if not 0 <= event.pitch <= 127:
            raise ValueError(f"pitch {event.pitch} out of 0..127")
        if not 1 <= event.velocity <= 127:
            raise ValueError(f"velocity {event.velocity} out of 1..127")
    else:
        if not 0 <= event.controller <= 127:
            raise ValueError(f"controller {event.controller} out of 0..127")
        if not 0 <= event.value <= 127:
            raise ValueError(f"cc value {event.value} out of 0..127")
