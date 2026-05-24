"""``subbass drum_and_bass`` — Reese-style sustains.

In DnB the sub IS the bass — long Reese pads sitting around 30 Hz
that move once per phrase. We emit one sustained root per bar with
``alternate_fifth`` so adjacent bars don't sit dead still — the
fifth wobble keeps the sub interesting without breaking the floor.
The matching ``bass drum_and_bass`` lays its detuned-saw Reese tones
an octave higher; the two reinforce each other.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext

from ._common import drone_generate


@register_generator("subbass", "drum_and_bass")
class SubBassDrumAndBass(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        yield from drone_generate(self, ctx, bars_per_note=1, alternate_fifth=True)
