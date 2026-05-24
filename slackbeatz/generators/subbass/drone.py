"""``subbass drone`` — root drone every N bars.

Consolidated algorithm for every sub-bass voice whose shape is
"hold a long note then move". The 9 per-style sub-bass files this
replaces (acid / deep_techno / drum_and_bass / dub_techno / lofi /
vaporwave + the rest of the drone family) varied only in two
parameters:

* ``bars_per_note`` — how many bars one drone cell spans.
* ``alternate_fifth`` — when truthy, swing between root and
  fifth on alternate cells (deep_techno / drum_and_bass).

Everything else (pitch resolution, sidechain ducking, velocity
shaping) lives in :mod:`_common` and is shared with the
:class:`SubBassPulse` algorithm below.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext

from ._common import drone_generate


@register_generator("subbass", "drone")
class SubBassDrone(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        bars_per_note = max(1, self.knob_int("bars_per_note", 1))
        alternate_fifth = bool(self.knob_int("alternate_fifth", 0))
        yield from drone_generate(
            self, ctx,
            bars_per_note=bars_per_note,
            alternate_fifth=alternate_fifth,
        )
