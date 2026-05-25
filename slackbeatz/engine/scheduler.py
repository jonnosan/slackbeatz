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
    declared with a ``bars=N..M`` range (issue #21) and per-part time
    signatures (Phase 1 — non-4/4 meters use ``meter.ticks_per_bar``).
    """
    segments: list[TempoSegment] = []
    cursor = 0
    for idx, part_name in enumerate(song.arrangement):
        part = song.parts[part_name]
        bars = _bars_for(song, idx, part_name)
        length = bars_to_ticks(bars, meter=part.meter)
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


# Issue #14: per-role default tension (energy) when a part doesn't set
# `tension=N` explicitly. The values aim to match how the role labels
# feel in arrangement context — intro/break/outro pull back, drop is
# full energy, build sits in between because it ramps via `evolution`.
_ROLE_TENSION_DEFAULTS: dict[str, float] = {
    "intro":     0.55,
    "build":     0.80,
    "buildup":   0.80,
    "drop":      1.00,
    "main":      0.90,
    "verse":     0.85,
    "chorus":    0.95,
    "break":     0.50,
    "bridge":    0.65,
    "outro":     0.55,
    "transition": 0.85,  # issue #20 — fills should hit
}


def _tension_for(part) -> float:
    """Resolve the part's tension multiplier — explicit > role default > 1.0."""
    if part.tension is not None:
        return part.tension
    return _ROLE_TENSION_DEFAULTS.get(part.role, 1.0)


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
    part_bars = _bars_for(song, arrangement_index, part_name)
    tension = _tension_for(part)
    # Polymeter: if the gen has its own meter, recompute how many of
    # *its* bars fit into the part's total duration. Other gens in the
    # same part still see the part's meter, so the patterns drift in
    # and out of phase naturally — the defining feature of polymeter.
    gen = song.gens[gen_handle]
    gen_meter = gen.meter if gen.meter is not None else part.meter
    part_total_ticks = part_bars * part.meter.ticks_per_bar(PPQ)
    bars = part_total_ticks // gen_meter.ticks_per_bar(PPQ)
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
        tension=tension,
        meter=gen_meter,
    )


def render_events(song: ResolvedSong) -> list[tuple[int, mido.Message]]:
    """Render the whole song to a sorted list of ``(abs_tick, mido.Message)``.

    Notes are expanded into ``note_on`` / ``note_off`` pairs. CCs become a
    single ``control_change`` message. The list is sorted by ``abs_tick``
    with ``note_off`` events placed before any ``note_on`` at the same
    tick to avoid retriggering a held note on the same instrument.

    After per-gen rendering, any gen with the ``harmonize_with=H`` knob
    is processed by reading H's already-emitted Notes and adding
    transposed copies on the harmonizing gen's own channel. ``interval``
    knob (semitones, default +4 = major 3rd) controls the harmony.
    """
    # Two-pass: collect events keyed by gen handle so the harmonize
    # pass can read another gen's Note pitches.
    events_by_gen: dict[str, list[tuple[int, int, mido.Message]]] = {}
    cursor = 0
    for idx, part_name in enumerate(song.arrangement):
        part = song.parts[part_name]
        bars = _bars_for(song, idx, part_name)
        for gen_handle in part.gen_handles:
            gen_resolved = song.gens[gen_handle]
            # Gens with harmonize_with set don't generate their own
            # notes — they emit transposed copies of another gen's
            # output in the harmonize pass below. Their own algorithm
            # output would conflict with the harmonized copy.
            if isinstance(gen_resolved.knobs.get("harmonize_with"), str):
                events_by_gen.setdefault(gen_handle, [])
                continue
            ctx = _build_context(song, idx, gen_handle)
            algorithm = part.algorithm_overrides.get(
                gen_handle, gen_resolved.style,
            )
            algo = _instantiate_algorithm(gen_resolved, algorithm=algorithm)
            bucket = events_by_gen.setdefault(gen_handle, [])
            for event in algo.generate(ctx):
                validate(event)
                if isinstance(event, Note):
                    on_tick = cursor + event.tick
                    off_tick = cursor + event.tick + event.duration
                    bucket.append((on_tick, 1, mido.Message(
                        "note_on", channel=event.channel - 1,
                        note=event.pitch, velocity=event.velocity,
                    )))
                    bucket.append((off_tick, 0, mido.Message(
                        "note_off", channel=event.channel - 1,
                        note=event.pitch, velocity=0,
                    )))
                elif isinstance(event, CC):
                    abs_tick = cursor + event.tick
                    bucket.append((abs_tick, 0, mido.Message(
                        "control_change", channel=event.channel - 1,
                        control=event.controller, value=event.value,
                    )))
                else:  # PitchBend
                    abs_tick = cursor + event.tick
                    bucket.append((abs_tick, 0, mido.Message(
                        "pitchwheel", channel=event.channel - 1,
                        pitch=event.value,
                    )))
        cursor += bars_to_ticks(bars, meter=part.meter)

    # Harmonize pass: for each gen with harmonize_with=H, emit
    # transposed copies of H's note_on/note_off events on this gen's
    # channel. ``interval`` is in semitones (default +4 = major 3rd).
    for gen_handle, gen_resolved in song.gens.items():
        target = gen_resolved.knobs.get("harmonize_with")
        if not isinstance(target, str) or target not in events_by_gen:
            continue
        interval = gen_resolved.knobs.get("interval", 4)
        if not isinstance(interval, (int, float)):
            interval = 4
        interval = int(interval)
        if gen_resolved.instrument is None:
            continue  # nothing to route to
        out_channel = gen_resolved.instrument.channel - 1
        bucket = events_by_gen.setdefault(gen_handle, [])
        for tick, sort_key, msg in events_by_gen[target]:
            if msg.type not in ("note_on", "note_off"):
                continue
            new_note = msg.note + interval
            if not 0 <= new_note <= 127:
                continue
            bucket.append((tick, sort_key, msg.copy(
                channel=out_channel, note=new_note,
            )))

    timed = [t for events in events_by_gen.values() for t in events]
    timed.sort(key=lambda t: (t[0], t[1]))
    return [(tick, msg) for tick, _key, msg in timed]


def _instantiate_algorithm(gen, *, algorithm: str | None = None):
    """Look up ``(type_, algorithm)`` in the registry and build the algorithm.

    Defaults to the gen's own ``style`` column when no per-part
    override is in play. The instrument / knobs / kit binding always
    comes from the song-level gen — only the algorithm class changes
    per part.
    """
    style = algorithm if algorithm is not None else gen.style
    key = (gen.type_, style)
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
        # Updated as events fire — the live transport reads this to
        # implement "resume from current bar" after a parameter change.
        self.current_tick: int = 0

    def run(
        self,
        stop_event=None,
        *,
        resume_from_tick: int = 0,
        stop_at_tick: int | None = None,
        muted_channels=None,
    ) -> None:
        """Render the song to MIDI events and stream them via the sink.

        Parameters
        ----------
        stop_event:
            Optional :class:`threading.Event`. When set, the loop exits
            after the next event boundary. Used by the live transport
            to interrupt playback for re-composition / tempo change /
            seek / etc.
        resume_from_tick:
            Skip events whose absolute tick is below this value (but
            still issue the initial ``program_change`` events so the
            destination synth picks the right patches). The clock is
            also fast-forwarded so the first wait_until at or above
            this tick returns immediately. Used by the Player to
            implement "preserve current bar across parameter changes".
        stop_at_tick:
            Optional upper bound — when set, the loop exits the
            instant an event's absolute tick reaches this value.
            Used by the Player's part-loop feature to bound playback
            to one arrangement position's span; the surrounding
            ``_play_loop`` re-runs the scheduler with the same
            (resume_from_tick, stop_at_tick) pair so the part
            repeats until the user clears the loop.
        muted_channels:
            Optional ``set[int]`` (1-indexed) of muted channels. Read
            by reference on every event — the caller can mutate it
            mid-playback and the scheduler honours it from the next
            event onward. Channel-10 (drums) is muteable like any other.
        """
        events = render_events(self.song)
        self.current_tick = resume_from_tick
        self.sink.open()
        self.clock.open()
        try:
            # Start the clock at resume_from_tick so wait_until calls
            # before / at that tick return immediately.
            self.clock.start(initial_tick=resume_from_tick)
            # Send GM program_change up front so the destination synth
            # picks the right patch per channel — without this every
            # pitched channel stays on whatever default it powered up on
            # (typically GM Acoustic Grand Piano), and a deep_techno bass
            # at A1 sounds like a faint piano thump rather than a synth
            # bass. The midifile renderer already emits these; the
            # realtime scheduler used to skip them.
            for msg in _initial_program_changes(self.song):
                self.sink.send(msg)
            stopped_at_part_end = False
            for abs_tick, msg in events:
                if stop_event is not None and stop_event.is_set():
                    break
                if stop_at_tick is not None and abs_tick >= stop_at_tick:
                    stopped_at_part_end = True
                    break
                if abs_tick < resume_from_tick:
                    # Skip pre-seek events. The notes we drop here are
                    # those that should already have been playing — we
                    # accept that they won't be re-triggered (held pad
                    # notes etc. will start at the next note_on in the
                    # generator's stream).
                    continue
                self.clock.wait_until(abs_tick)
                self.current_tick = abs_tick
                # Per-channel mute. Skip every event on a muted channel
                # — including note_off, which means held notes need to
                # be silenced explicitly by the caller via CC 123. The
                # Player does this when the mute set grows.
                if muted_channels is not None and hasattr(msg, "channel"):
                    if (msg.channel + 1) in muted_channels:
                        continue
                self.sink.send(msg)
            # If stop_at_tick is set and we exited the event loop
            # before reaching it (either because we hit a "future"
            # event we explicitly skipped, or we ran out of events
            # mid-part), drain the wall-clock so the silent tail
            # plays through before the caller restarts us for
            # another loop iteration. Without this, a part that
            # ends with a sustained note's tail would loop the
            # instant the last event fires rather than at the
            # part's true boundary.
            need_drain = (
                stop_at_tick is not None
                and (stop_event is None or not stop_event.is_set())
                and self.current_tick < stop_at_tick
            )
            if need_drain:
                try:
                    self.clock.wait_until(stop_at_tick)
                except Exception:
                    pass
                self.current_tick = stop_at_tick
            # stopped_at_part_end is informational — current_tick is
            # what callers read for "where did we end".
            _ = stopped_at_part_end
        finally:
            self.clock.close()
            self.sink.close()


def _initial_program_changes(song: ResolvedSong) -> list[mido.Message]:
    """One ``program_change`` per pitched channel, picked from the gen's
    ``(type, style)`` GM default. Drum channels (rhythm/drums gens) are
    skipped — those auto-route to the GM percussion bank on ch 10.

    If two gens share a channel (e.g. two bass gens both on ch 2), the
    first one's program wins — declaring a per-gen ``program=N`` knob
    lets the user disambiguate.
    """
    # Local import: avoid a top-of-module circular import with midifile.py.
    from slackbeatz.engine.midifile import _program_for_gen

    seen: set[int] = set()
    out: list[mido.Message] = []
    for gen in song.gens.values():
        if gen.instrument is None:
            continue
        channel = gen.instrument.channel - 1
        if channel in seen:
            continue
        prog = _program_for_gen(gen)
        if prog is None:
            continue
        out.append(
            mido.Message("program_change", channel=channel, program=prog)
        )
        seen.add(channel)
    return out
