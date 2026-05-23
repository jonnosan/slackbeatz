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
