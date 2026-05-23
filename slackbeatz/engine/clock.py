"""Tempo math + a tempo map for songs whose tempo changes per part.

We use a fixed internal resolution of **PPQ = 480** (the de-facto standard
for software sequencers and easily divisible by 24 for MIDI Clock sync).
That choice is centralised here so other modules don't sprinkle the
constant around.

The :class:`TempoMap` lets a song advance through tempo-varying parts
without the scheduler caring how the segments line up — it asks the map
"what wall-clock time does this absolute tick fall at?" and the map
walks its segments.
"""

from __future__ import annotations

from dataclasses import dataclass

PPQ = 480
"""Pulses per quarter note — the engine's internal tick resolution."""


def bars_to_ticks(bars: int, ppq: int = PPQ, meter=None) -> int:
    """Ticks for *bars* bars.

    Default meter is 4/4 (matching the historical behaviour); pass a
    :class:`slackbeatz.theory.meter.Meter` to compute lengths for
    non-4/4 time signatures.
    """
    if meter is None:
        return bars * 4 * ppq
    return bars * meter.ticks_per_bar(ppq)


def tick_to_seconds(tick: int, bpm: float, ppq: int = PPQ) -> float:
    """Wall-clock seconds for a tick *offset* at constant *bpm*."""
    return tick * 60.0 / (bpm * ppq)


@dataclass(frozen=True)
class TempoSegment:
    """A run of ticks at a constant tempo."""

    start_tick: int  # inclusive
    end_tick: int  # exclusive
    bpm: int


class TempoMap:
    """A piecewise-constant tempo schedule over the whole song.

    Build with a list of :class:`TempoSegment`s in tick order; use
    :meth:`time_at` to translate any absolute tick into seconds-since-
    song-start.
    """

    def __init__(self, segments: list[TempoSegment]) -> None:
        if not segments:
            raise ValueError("tempo map needs at least one segment")
        # Sanity: contiguous, ordered, non-overlapping.
        for prev, curr in zip(segments, segments[1:]):
            if curr.start_tick != prev.end_tick:
                raise ValueError(
                    f"tempo segments must be contiguous: {prev} → {curr}"
                )
            if curr.bpm <= 0:
                raise ValueError(f"tempo segment {curr} has non-positive bpm")
        if segments[0].start_tick != 0:
            raise ValueError(f"first tempo segment must start at tick 0, got {segments[0]}")
        self.segments: list[TempoSegment] = segments
        self.ppq = PPQ

    @property
    def end_tick(self) -> int:
        return self.segments[-1].end_tick

    def time_at(self, abs_tick: int) -> float:
        """Wall-clock seconds at which *abs_tick* occurs.

        Ticks beyond the last segment extrapolate at the final segment's
        tempo — useful for the last note's note-off landing just past the
        nominal song end.
        """
        if abs_tick < 0:
            raise ValueError(f"abs_tick {abs_tick} is negative")
        t = 0.0
        for seg in self.segments:
            if abs_tick < seg.end_tick:
                t += tick_to_seconds(abs_tick - seg.start_tick, seg.bpm, self.ppq)
                return t
            t += tick_to_seconds(seg.end_tick - seg.start_tick, seg.bpm, self.ppq)
        # Past the end — extrapolate at the final tempo.
        last = self.segments[-1]
        t += tick_to_seconds(abs_tick - last.end_tick, last.bpm, self.ppq)
        return t

    def bpm_at(self, abs_tick: int) -> int:
        """The tempo in effect at *abs_tick* (used for live status display)."""
        for seg in self.segments:
            if abs_tick < seg.end_tick:
                return seg.bpm
        return self.segments[-1].bpm
