"""``subbass acid`` — sustained root behind the 303 line.

The acid bass IS the bass — squelchy, busy, mid-register. A separate
sub layer sits underneath as a steady root drone, one note per bar.
Gate just below 1.0 so each bar's note clears the next on the
downbeat boundary (avoids re-trigger clicks on hardware sub bass).
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext

from ._common import drone_generate


@register_generator("subbass", "acid")
class SubBassAcid(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        yield from drone_generate(self, ctx, bars_per_note=1)
