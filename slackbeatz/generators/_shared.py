"""Shared algorithm primitives.

* :func:`euclid` — Bjorklund-style even pulse distribution, ported in
  spirit from the Arduino prototype's ``distribute_notes``. The bread-
  and-butter primitive for techno-style 4-on-the-floor / off-beat hat /
  rolling-snare patterns.
* :func:`bar_to_ticks` / :func:`step_to_ticks` — small helpers to convert
  between the 16-step grid every algorithm uses and the engine's PPQ
  ticks.
* :class:`ChordProgression` — picks a chord-root degree for each bar of
  a part. Captures the Arduino "Track Chords" idea so a melody gen can
  follow the same progression a chord gen is playing.
* :func:`fill_perturb` — re-rolls drum pulse counts upward by a small
  amount, used as the 4-bar fill behaviour of the ``drums euclid`` /
  ``drums deep_techno`` algorithms.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from slackbeatz.engine.event import CC
    from slackbeatz.model.context import PartContext

# Every algorithm uses a 16-step grid per bar (the Arduino convention).
STEPS_PER_BAR = 16


# --------------------------------------------------------------------------
# Per-hit shaping (humanize / drop / accent / velocity jitter)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class HitParams:
    """Bundle of per-hit chance knobs all rhythm/drums gens read.

    These are uniform across styles so every rhythm/drums voice can wear
    the same set of small humanising knobs in the DSL.
    """

    base_vel: int = 100
    intensity: float = 1.0
    vel_jitter: int = 8         # ±N velocity points (random)
    humanize: int = 0           # ±N tick offset per hit
    drop_prob: float = 0.0      # chance to drop a hit entirely
    accent: int = 0             # every accent-th step gets +12 velocity


def humanize_hit(
    params: HitParams,
    rng: random.Random,
    step: int,
    tick: int,
    *,
    intensity_mult: float = 1.0,
) -> tuple[int, int] | None:
    """Apply the chance knobs to one rhythmic hit.

    Returns ``(velocity, tick)`` or ``None`` if the hit was dropped by
    ``drop_prob``. Algorithms call this once per pattern position they
    intend to emit a note at.

    ``intensity_mult`` is an extra multiplier (typically the per-bar
    ``evolution`` ramp) layered on top of ``params.intensity``.
    """
    if params.drop_prob > 0 and rng.random() < params.drop_prob:
        return None
    vel = int(round(params.base_vel * params.intensity * intensity_mult))
    if params.vel_jitter > 0:
        vel += rng.randint(-params.vel_jitter, params.vel_jitter)
    if params.accent > 0 and step % params.accent == 0:
        vel += 12
    vel = max(1, min(127, vel))
    new_tick = tick
    if params.humanize > 0:
        new_tick = max(0, tick + rng.randint(-params.humanize, params.humanize))
    return (vel, new_tick)


# --------------------------------------------------------------------------
# Pattern + macro chance (issues #2, #8, #9)
# --------------------------------------------------------------------------

def drift_pulses(base: int, drift: float, rng: random.Random) -> int:
    """Per-bar Euclidean pulse-count perturbation.

    ``drift=0`` returns *base* unchanged. ``drift=0.5`` means roughly
    half of bars roll ±1 around the base; ``drift=1`` always perturbs.
    Result is clamped to ``[0, STEPS_PER_BAR]`` so an extreme drift
    doesn't degenerate a 4/16 kick into all-pulses.
    """
    if drift <= 0:
        return base
    if rng.random() >= drift:
        return base
    return max(0, min(STEPS_PER_BAR, base + rng.choice([-1, 1])))


def should_mute_bar(rng: random.Random, mute_prob: float) -> bool:
    """Roll the per-bar gen-drop chance. ``True`` ⇒ skip this bar."""
    return mute_prob > 0 and rng.random() < mute_prob


def evolution_multiplier(
    bar: int,
    total_bars: int,
    evolution: float,
    direction: int,
) -> float:
    """Linear ramp across a part for an ``evolution`` energy curve.

    Maps bar position ``[0, total_bars-1]`` onto a multiplier in
    ``[1 - evolution, 1 + evolution]``. ``direction=1`` ramps up across
    the part; ``-1`` ramps down; ``0`` (or ``evolution=0``) returns
    ``1.0``. Callers typically pick the direction once per part-instance
    via ``ctx.rng.choice([-1, 1])`` and reuse it for every bar.
    """
    if evolution <= 0 or direction == 0 or total_bars <= 1:
        return 1.0
    frac = bar / (total_bars - 1)
    return 1.0 + direction * evolution * (2 * frac - 1)


def pick_evolution_direction(rng: random.Random, evolution: float) -> int:
    """Returns +1 or -1 per part-instance, or 0 if evolution is disabled."""
    if evolution <= 0:
        return 0
    return rng.choice([-1, 1])


# --------------------------------------------------------------------------
# Sidechain ducking envelope (kick-on-each-beat assumption)
# --------------------------------------------------------------------------

def sidechain_envelope(tick_in_bar: int, ppq: int, duck: float = 0.5) -> float:
    """Velocity multiplier in ``[duck, 1.0]`` for a tick inside a bar.

    Assumes 4-on-the-floor kicks land on every quarter beat (the
    overwhelming default in techno-derived styles). At each beat the
    multiplier is ``duck`` (so ``duck=0.5`` means "halve velocity on
    the downbeat"); it ramps linearly back to ``1.0`` by the midpoint
    of the beat. ``duck=1.0`` disables the envelope.
    """
    if duck >= 1.0:
        return 1.0
    pos = tick_in_bar % ppq
    half_beat = ppq // 2
    if pos >= half_beat or half_beat == 0:
        return 1.0
    return duck + (1.0 - duck) * (pos / half_beat)


def euclid(pulses: int, steps: int = STEPS_PER_BAR, offset: int = 0) -> list[bool]:
    """Linear Bjorklund-style Euclidean rhythm.

    Returns a list of *steps* booleans with *pulses* ``True`` values
    distributed as evenly as possible. The first pulse is rotated to
    position 0 (so ``euclid(4, 16)`` gives 4-on-the-floor without
    needing a manual offset), then *offset* shifts the result right.

    >>> euclid(4, 16)
    [True, False, False, False, True, False, False, False, True, False, False, False, True, False, False, False]
    >>> sum(euclid(7, 16))
    7
    >>> euclid(0, 16) == [False] * 16
    True
    >>> euclid(16, 16) == [True] * 16
    True
    """
    if pulses <= 0:
        return [False] * steps
    if pulses >= steps:
        return [True] * steps
    pattern: list[bool] = []
    bucket = 0
    for _ in range(steps):
        bucket += pulses
        if bucket >= steps:
            bucket -= steps
            pattern.append(True)
        else:
            pattern.append(False)
    # Rotate so the first True lands at position 0.
    first = pattern.index(True)
    pattern = pattern[first:] + pattern[:first]
    if offset:
        offset %= steps
        pattern = pattern[-offset:] + pattern[:-offset]
    return pattern


# --------------------------------------------------------------------------
# Tick / step conversions
# --------------------------------------------------------------------------

def step_to_ticks(step: int, ppq: int, steps_per_bar: int = STEPS_PER_BAR) -> int:
    """Convert a 0..15 step index in a bar to a tick offset within that bar."""
    ticks_per_bar = 4 * ppq  # 4/4 only in v1
    return step * ticks_per_bar // steps_per_bar


def bar_to_ticks(bar: int, ppq: int) -> int:
    """Tick offset to the *start* of *bar* (0-indexed) within a part."""
    return bar * 4 * ppq


def step_duration(ppq: int, steps_per_bar: int = STEPS_PER_BAR) -> int:
    """Ticks per 16th-note step at the given PPQ."""
    return 4 * ppq // steps_per_bar


# --------------------------------------------------------------------------
# Chord progressions
# --------------------------------------------------------------------------

# Chord progressions expressed as scale degrees (0-indexed). i = 0, ii = 1,
# … vii = 6. Names use lowercase for minor, uppercase for major, matching
# the convention of relative-roman-numeral notation in minor keys.
PROGRESSIONS: dict[str, list[int]] = {
    # The Arduino default: minor i, VI (relative major), ii, IV
    "i-VI-ii-IV": [0, 5, 1, 3],
    # Deep techno: slow modal swap
    "i-iv":       [0, 3],
    # Psytrance: skeletal modal back-and-forth
    "i-v":        [0, 4],
    # Vaporwave: classic descending minor — bass walks 1, b7, b6, 5
    "i-VII-VI-V": [0, 6, 5, 4],
}


@dataclass(frozen=True)
class ChordProgression:
    """Names a progression + how many bars each chord lasts.

    ``degree_at_bar(b)`` returns the scale degree of the chord active in
    bar ``b``, wrapping around the progression's length.
    """

    name: str
    bars_per_chord: int = 4

    def __post_init__(self) -> None:
        if self.name not in PROGRESSIONS:
            raise ValueError(
                f"unknown chord progression {self.name!r} "
                f"(known: {sorted(PROGRESSIONS)})"
            )

    @property
    def degrees(self) -> list[int]:
        return PROGRESSIONS[self.name]

    def degree_at_bar(self, bar: int) -> int:
        slot = (bar // self.bars_per_chord) % len(self.degrees)
        return self.degrees[slot]


# --------------------------------------------------------------------------
# Drum fills
# --------------------------------------------------------------------------

def fill_perturb(
    base_pulses: int,
    rng: random.Random,
    bump: int = 2,
    cap: int = STEPS_PER_BAR,
) -> int:
    """Return a pulse count for a fill bar: *base_pulses* plus a small
    upward perturbation.

    ``cap`` clamps the result so a 16-step pattern doesn't degenerate to
    all-pulses.
    """
    return min(cap, base_pulses + rng.randint(1, max(1, bump)))


def is_fill_bar(bar: int, group: int = 4) -> bool:
    """Is *bar* (0-indexed) the last bar of a *group*-bar group?"""
    return (bar % group) == (group - 1)


_BUILD_ROLES = frozenset({"build", "buildup"})


def is_build_part(ctx: "PartContext") -> bool:
    """True if this part should swell into the next — either its role
    is build-shaped, or it sits directly before a drop."""
    return ctx.role in _BUILD_ROLES or ctx.next_role == "drop"


def expression_ramp(
    ctx: "PartContext",
    channel: int,
    *,
    start: int = 70,
    end: int = 127,
    events_per_bar: int = 4,
    bars_to_ramp: int | None = None,
) -> Iterator["CC"]:
    """Yield CC 11 (Expression) events ramping ``start`` → ``end`` over
    the last ``bars_to_ramp`` bars (defaults to *all* bars of the part).

    Algorithms call this only when :func:`is_build_part` returns True,
    so the expression curve sells the build → drop transition. The
    final value persists into the next part (no snap-back).
    """
    # Local import to avoid the cycle the TYPE_CHECKING guard documents.
    from slackbeatz.engine.event import CC

    ticks_per_bar = 4 * ctx.ppq
    total_ticks = ctx.bars * ticks_per_bar
    ramp_bars = min(ctx.bars, bars_to_ramp) if bars_to_ramp else ctx.bars
    ramp_start = total_ticks - ramp_bars * ticks_per_bar
    n = max(2, ramp_bars * events_per_bar)
    span = total_ticks - ramp_start
    step = max(1, span // (n - 1))
    for i in range(n):
        tick = ramp_start + i * step
        if tick >= total_ticks:
            tick = total_ticks - 1
        frac = i / (n - 1)
        value = int(round(start + (end - start) * frac))
        yield CC(
            tick=max(0, tick), channel=channel, controller=11,
            value=max(0, min(127, value)),
        )
