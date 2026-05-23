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

# Every algorithm uses a 16-step grid per bar (the Arduino convention).
STEPS_PER_BAR = 16


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
