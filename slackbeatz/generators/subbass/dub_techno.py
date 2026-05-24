"""``subbass dub_techno`` — 4-bar drone, just root.

Dub techno is glacial — chord stabs every couple of bars, sparse
percussion, nothing should move quickly. The sub embodies that: one
sustained root note per 4-bar phrase, ducking gently on each kick.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext

from ._common import drone_generate


@register_generator("subbass", "dub_techno")
class SubBassDubTechno(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        yield from drone_generate(self, ctx, bars_per_note=4)
