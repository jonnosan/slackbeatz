"""Per-part context passed to each generator.

Carries everything an algorithm needs to render its slice of the song
without inspecting the wider arrangement directly: its position, its
neighbours' roles, its tempo, its key, and — crucially — a
pre-seeded :class:`random.Random` so that chance-driven choices are
reproducible from the song's seed.

Algorithms **must** use ``ctx.rng`` for any randomness. Calling the bare
``random`` module breaks the reproducibility contract.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from slackbeatz.theory.meter import Meter, COMMON_TIME


@dataclass
class PartContext:
    """Read-only-ish context for one part-instance × one generator.

    Not frozen because :class:`random.Random` instances aren't trivially
    hashable / immutable — but treat the data fields as read-only.
    """

    name: str
    role: str
    bars: int
    tempo: int
    key: str
    ppq: int = 480
    arrangement_index: int = 0
    arrangement_total: int = 0
    prev_role: str | None = None
    next_role: str | None = None
    rng: random.Random = field(default_factory=random.Random)
    # Set by the scheduler if any `scale=` knob (part or song) is in
    # play. Pitched gens read this with a fallback to their style's
    # hardcoded default.
    scale_override: str | None = None
    # Semitone offset applied per arrangement-instance — picked by the
    # scheduler from the part's `transpose_prob` knob. 0 = no shift.
    transpose_semitones: int = 0
    # Issue #14: part-level "energy" multiplier all gens see. Default
    # 1.0; auto-derived from role when the part doesn't set it
    # explicitly (intro=0.5, build=ramps 0.5→1.0 — though gens still
    # apply that ramp themselves via `evolution`, drop=1.0,
    # break/outro=0.5).
    tension: float = 1.0
    # Time signature for this part (Phase 1 of composition iteration).
    # Default 4/4. Gens use the derived helpers below in place of the
    # old hardcoded `16` / `4 * ctx.ppq`.
    meter: Meter = COMMON_TIME

    @property
    def steps_per_bar(self) -> int:
        """16th-note steps in one bar — meter-aware replacement for the
        previously-hardcoded ``16``."""
        return self.meter.steps_per_bar

    @property
    def beats_per_bar(self) -> int:
        return self.meter.beats_per_bar

    @property
    def ticks_per_bar(self) -> int:
        """Ticks in one bar at this part's PPQ + meter — meter-aware
        replacement for ``4 * ctx.ppq``."""
        return self.meter.ticks_per_bar(self.ppq)
