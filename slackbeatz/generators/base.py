"""Generator abstract base class.

All algorithm classes subclass :class:`Generator` and decorate themselves
with :func:`slackbeatz.generators.registry.register_generator` to be
discoverable. The scheduler instantiates them per song-resolution and
calls :meth:`generate` once per part-instance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Iterator

from slackbeatz.engine.event import Event
from slackbeatz.model.context import PartContext
from slackbeatz.setup.model import Instrument, Kit


class Generator(ABC):
    """One concrete musical algorithm bound to (type, style)."""

    # Set by the @register_generator decorator at class-definition time.
    type_: ClassVar[str]
    style: ClassVar[str]

    def __init__(
        self,
        *,
        handle: str,
        knobs: dict[str, Any],
        instrument: Instrument | None = None,
        kit: Kit | None = None,
    ) -> None:
        self.handle = handle
        self.knobs = knobs
        self.instrument = instrument
        self.kit = kit

    # ----------------------- knob accessors -----------------------------

    def knob_int(self, key: str, default: int) -> int:
        v = self.knobs.get(key, default)
        return int(v) if isinstance(v, (int, float)) else default

    def knob_float(self, key: str, default: float) -> float:
        v = self.knobs.get(key, default)
        return float(v) if isinstance(v, (int, float)) else default

    def knob_str(self, key: str, default: str) -> str:
        v = self.knobs.get(key, default)
        return str(v) if v is not None else default

    @abstractmethod
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        """Yield events for one part-instance.

        Tick offsets are relative to the start of the part. Algorithms
        must use ``ctx.rng`` for any randomness so the same seed
        produces the same output.
        """
