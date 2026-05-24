"""``subbass deep_techno`` — sustained root / fifth drone.

Two-bar cells alternate between root and fifth — same harmonic
device the matching `bass deep_techno` uses, an octave lower, so the
two voices reinforce each other across the spectrum.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext

from ._common import drone_generate


@register_generator("subbass", "deep_techno")
class SubBassDeepTechno(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        yield from drone_generate(self, ctx, bars_per_note=2, alternate_fifth=True)
