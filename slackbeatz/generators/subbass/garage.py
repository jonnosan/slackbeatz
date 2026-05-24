"""``subbass garage`` — punchy on the 1 and 3.

UK garage's bottom end is felt, not heard — short tight pulses on
the kick beats (steps 0 and 8 of a 16-step bar) with the
characteristic snap that lets the snare on 2 / 4 read clearly. Add
``fifth_prob=0.2`` for occasional fifth substitutions if the bassline
above is doing root-only walks.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext

from ._common import pulse_generate


@register_generator("subbass", "garage")
class SubBassGarage(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        # Steps 0 and 8 = beats 1 and 3 (the kick beats). step_dur_frac
        # below 1.0 keeps each pulse short so it doesn't smear into
        # the snare hit.
        yield from pulse_generate(
            self, ctx, steps=[0, 8], step_dur_frac=3.0,
        )
