"""Time-signature support.

The engine operates on a fixed 16th-note step grid: every step is
``PPQ / 4`` ticks regardless of the meter. What changes per meter is
the *number of steps in a bar* — 4/4 has 16 steps, 3/4 has 12, 7/8
has 14, etc.

Restricting the denominator to powers of 2 that divide 16 (1, 2, 4,
8, 16) keeps the step grid clean. Compound time (6/8, 9/8, 12/8) is
supported naturally: 6/8 has 12 steps (6 eighth-notes × 2 sixteenths).
Anything more exotic (e.g. 5/16, 11/32) is rejected for v1.
"""

from __future__ import annotations

from dataclasses import dataclass


_ALLOWED_DENOMINATORS = frozenset({1, 2, 4, 8, 16})

# Steps per beat at each denominator: how many 16ths make up one beat
# (= 16 / denominator). 4/4 → 4 16ths per beat; 6/8 → 2 16ths per beat.
_STEPS_PER_BEAT: dict[int, int] = {1: 16, 2: 8, 4: 4, 8: 2, 16: 1}


@dataclass(frozen=True)
class Meter:
    """A time signature like ``Meter(3, 4)`` for 3/4.

    Internally the engine uses a 16th-note step grid; ``steps_per_bar``
    and ``ticks_per_bar(ppq)`` derive everything else.
    """

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if self.numerator < 1:
            raise ValueError(f"meter numerator must be >= 1, got {self.numerator}")
        if self.denominator not in _ALLOWED_DENOMINATORS:
            raise ValueError(
                f"meter denominator must be one of {sorted(_ALLOWED_DENOMINATORS)}, "
                f"got {self.denominator}"
            )

    @property
    def steps_per_beat(self) -> int:
        """Number of 16th-note steps in one beat at this denominator."""
        return _STEPS_PER_BEAT[self.denominator]

    @property
    def steps_per_bar(self) -> int:
        """Total 16th-note steps in one bar."""
        return self.numerator * self.steps_per_beat

    @property
    def beats_per_bar(self) -> int:
        return self.numerator

    def ticks_per_bar(self, ppq: int) -> int:
        """Ticks per bar at the given PPQ. One 16th = ``ppq / 4`` ticks."""
        return self.steps_per_bar * (ppq // 4)

    def __str__(self) -> str:
        return f"{self.numerator}/{self.denominator}"

    @classmethod
    def parse(cls, s: str) -> "Meter":
        """Parse ``"3/4"`` → ``Meter(3, 4)``.

        Raises ``ValueError`` on malformed strings.
        """
        if "/" not in s:
            raise ValueError(f"meter must be N/M, got {s!r}")
        n_str, _, d_str = s.partition("/")
        try:
            n = int(n_str)
            d = int(d_str)
        except ValueError:
            raise ValueError(f"meter must be N/M of ints, got {s!r}") from None
        return cls(n, d)


COMMON_TIME = Meter(4, 4)
"""4/4 — the default if no `meter` is specified at song or part level."""
