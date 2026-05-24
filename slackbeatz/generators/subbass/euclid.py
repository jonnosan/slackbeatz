"""``subbass euclid`` — Euclidean root pulses.

The 3-of-16 distribution (steps 0, 5, 11) lands one sub hit roughly
every five-and-a-half 16ths, giving the open-ended hypnotic feel
that fits the euclid style's sparse drum patterns.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event
from slackbeatz.generators._shared import euclid
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext

from ._common import pulse_generate


@register_generator("subbass", "euclid")
class SubBassEuclid(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        # Euclidean(3, 16) → steps [0, 5, 11] by convention. Use the
        # shared euclid() so any future tweak to the distribution
        # algorithm carries automatically.
        pattern = euclid(3, ctx.steps_per_bar)
        steps = [s for s, hit in enumerate(pattern) if hit]
        yield from pulse_generate(self, ctx, steps=steps, step_dur_frac=2.5)
