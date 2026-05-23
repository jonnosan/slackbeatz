"""Walks a :class:`ResolvedSong`, calls each generator, dispatches events.

The scheduler is a pure-Python loop that:

1. Builds a :class:`TempoMap` from the arrangement.
2. Derives a per-(part-instance × generator) :class:`PartContext` with a
   seeded PRNG, calls ``gen.generate(ctx)``, and collects the events.
3. Translates tick-offsets to absolute song ticks and expands each
   :class:`Note` into a ``note_on`` / ``note_off`` pair.
4. Sorts the resulting ``mido.Message`` stream by tick and feeds it to
   the active :class:`ClockSource` + :class:`Sink`.

Reproducibility hinges on :func:`derive_seed` being deterministic across
runs, so Python's randomised string hashing isn't used.
"""

from __future__ import annotations

import hashlib
import random
from typing import TYPE_CHECKING

import mido

from slackbeatz.engine.clock import PPQ, TempoMap, TempoSegment, bars_to_ticks
from slackbeatz.engine.event import CC, Note, PitchBend, validate
from slackbeatz.generators.registry import REGISTRY
from slackbeatz.model.context import PartContext
from slackbeatz.model.song import ResolvedSong

if TYPE_CHECKING:
    from slackbeatz.engine.clock_source import ClockSource
    from slackbeatz.sinks.base import Sink


def derive_seed(base_seed: int, part_name: str, gen_handle: str) -> int:
    """Deterministic 63-bit seed from a base seed plus name pair.

    Python's ``hash()`` is randomised per process for strings; this
    function uses SHA-256 truncated to 63 bits so the same triple always
    produces the same Random stream regardless of when or where the
    process runs.
    """
    h = hashlib.sha256()
    h.update(str(base_seed).encode("utf-8"))
    h.update(b"\x00")
    h.update(part_name.encode("utf-8"))
    h.update(b"\x00")
    h.update(gen_handle.encode("utf-8"))
    return int.from_bytes(h.digest()[:8], "big") & ((1 << 63) - 1)


def resolved_seed_for(
    song: ResolvedSong,
    part_name: str,
    gen_handle: str,
) -> int:
    """Apply the gen → part → song seed hierarchy and derive the stream."""
    gen = song.gens[gen_handle]
    part = song.parts[part_name]
    base = (
        gen.seed_override
        if gen.seed_override is not None
        else part.seed_override
        if part.seed_override is not None
        else song.seed
    )
    return derive_seed(base, part_name, gen_handle)


# --------------------------------------------------------------------------
# Tempo map construction
# --------------------------------------------------------------------------

def build_tempo_map(song: ResolvedSong) -> TempoMap:
    """Walk the arrangement and build a contiguous tempo map.

    Honours per-arrangement-instance bar counts when the part was
    declared with a ``bars=N..M`` range (issue #21).
    """
    segments: list[TempoSegment] = []
    cursor = 0
    for idx, part_name in enumerate(song.arrangement):
        part = song.parts[part_name]
        bars = _bars_for(song, idx, part_name)
        length = bars_to_ticks(bars)
        if segments and segments[-1].bpm == part.tempo:
            # Coalesce adjacent same-tempo segments.
            last = segments.pop()
            segments.append(TempoSegment(last.start_tick, cursor + length, part.tempo))
        else:
            segments.append(TempoSegment(cursor, cursor + length, part.tempo))
        cursor += length
    return TempoMap(segments)


# --------------------------------------------------------------------------
# Event generation per part-instance
# --------------------------------------------------------------------------

_TRANSPOSE_CHOICES = (0, 0, 0, 0, -5, -3, 3, 5, 7)
"""Pool of semitone offsets when ``transpose_prob`` fires for a part-
instance. Weighted toward common harmonic moves (perfect 4th down,
minor 3rd, fifth up) so transpositions feel intentional, not random."""


def _transposition_for(
    song: ResolvedSong,
    arrangement_index: int,
    part_name: str,
) -> int:
    """Roll the part's `transpose_prob` for this arrangement-instance.

    The seed mixes the song seed, part name, and arrangement index so
    different instances of the same part can transpose differently,
    but the same instance is reproducible across runs.
    """
    part = song.parts[part_name]
    if part.transpose_prob <= 0:
        return 0
    seed = derive_seed(song.seed, part_name, f"__transpose_{arrangement_index}")
    rng = random.Random(seed)
    if rng.random() >= part.transpose_prob:
        return 0
    return rng.choice(_TRANSPOSE_CHOICES)


def _bars_for(
    song: ResolvedSong,
    arrangement_index: int,
    part_name: str,
) -> int:
    """Issue #21: resolve the per-arrangement-instance bar count.

    For parts declared as a single int, returns that. For parts
    declared as ``bars=N..M``, picks an integer in ``[N, M]`` using a
    seed deterministic in ``(song.seed, part_name, arrangement_index)``.
    """
    part = song.parts[part_name]
    if part.bars_max is None or part.bars_max == part.bars:
        return part.bars
    seed = derive_seed(song.seed, part_name, f"__bars_{arrangement_index}")
    return random.Random(seed).randint(part.bars, part.bars_max)


def _build_context(
    song: ResolvedSong,
    arrangement_index: int,
    gen_handle: str,
) -> PartContext:
    part_name = song.arrangement[arrangement_index]
    part = song.parts[part_name]
    total = len(song.arrangement)
    prev_role = (
        song.parts[song.arrangement[arrangement_index - 1]].role
        if arrangement_index > 0
        else None
    )
    next_role = (
        song.parts[song.arrangement[arrangement_index + 1]].role
        if arrangement_index + 1 < total
        else None
    )
    seed = resolved_seed_for(song, part_name, gen_handle)
    scale_override = part.scale_override or song.scale_override
    transpose_semitones = _transposition_for(song, arrangement_index, part_name)
    bars = _bars_for(song, arrangement_index, part_name)
    return PartContext(
        name=part.name,
        role=part.role,
        bars=bars,
        tempo=part.tempo,
        key=part.key,
        ppq=PPQ,
        arrangement_index=arrangement_index,
        arrangement_total=total,
        prev_role=prev_role,
        next_role=next_role,
        rng=random.Random(seed),
        scale_override=scale_override,
        transpose_semitones=transpose_semitones,
    )


def render_events(song: ResolvedSong) -> list[tuple[int, mido.Message]]:
    """Render the whole song to a sorted list of ``(abs_tick, mido.Message)``.

    Notes are expanded into ``note_on`` / ``note_off`` pairs. CCs become a
    single ``control_change`` message. The list is sorted by ``abs_tick``
    with ``note_off`` events placed before any ``note_on`` at the same
    tick to avoid retriggering a held note on the same instrument.
    """
    timed: list[tuple[int, int, mido.Message]] = []  # (tick, sort_key, msg)
    cursor = 0
    for idx, part_name in enumerate(song.arrangement):
        part = song.parts[part_name]
        # Per-arrangement-instance bar count (issue #21).
        bars = _bars_for(song, idx, part_name)
        for gen_handle in part.gen_handles:
            ctx = _build_context(song, idx, gen_handle)
            gen_resolved = song.gens[gen_handle]
            algo = _instantiate_algorithm(gen_resolved)
            for event in algo.generate(ctx):
                validate(event)
                if isinstance(event, Note):
                    on_tick = cursor + event.tick
                    off_tick = cursor + event.tick + event.duration
                    timed.append(
                        (
                            on_tick,
                            1,  # note_on sorted after note_off at same tick
                            mido.Message(
                                "note_on",
                                channel=event.channel - 1,
                                note=event.pitch,
                                velocity=event.velocity,
                            ),
                        )
                    )
                    timed.append(
                        (
                            off_tick,
                            0,
                            mido.Message(
                                "note_off",
                                channel=event.channel - 1,
                                note=event.pitch,
                                velocity=0,
                            ),
                        )
                    )
                elif isinstance(event, CC):
                    abs_tick = cursor + event.tick
                    timed.append(
                        (
                            abs_tick,
                            0,
                            mido.Message(
                                "control_change",
                                channel=event.channel - 1,
                                control=event.controller,
                                value=event.value,
                            ),
                        )
                    )
                else:  # PitchBend
                    abs_tick = cursor + event.tick
                    timed.append(
                        (
                            abs_tick,
                            0,
                            mido.Message(
                                "pitchwheel",
                                channel=event.channel - 1,
                                pitch=event.value,
                            ),
                        )
                    )
        cursor += bars_to_ticks(bars)
    timed.sort(key=lambda t: (t[0], t[1]))
    return [(tick, msg) for tick, _key, msg in timed]


def _instantiate_algorithm(gen):
    """Look up ``(type_, style)`` in the registry and build the algorithm."""
    key = (gen.type_, gen.style)
    if key not in REGISTRY:
        raise KeyError(
            f"no generator registered for {key} — available: {sorted(REGISTRY)}"
        )
    cls = REGISTRY[key]
    return cls(
        handle=gen.handle,
        knobs=gen.knobs,
        instrument=gen.instrument,
        kit=gen.kit,
    )


# --------------------------------------------------------------------------
# Scheduler driver
# --------------------------------------------------------------------------

class Scheduler:
    """Runs a :class:`ResolvedSong` through a :class:`ClockSource` and
    :class:`Sink`."""

    def __init__(
        self,
        song: ResolvedSong,
        sink: "Sink",
        clock: "ClockSource",
    ) -> None:
        self.song = song
        self.sink = sink
        self.clock = clock

    def run(self) -> None:
        events = render_events(self.song)
        self.sink.open()
        self.clock.open()
        try:
            self.clock.start()
            for abs_tick, msg in events:
                self.clock.wait_until(abs_tick)
                self.sink.send(msg)
        finally:
            self.clock.close()
            self.sink.close()
