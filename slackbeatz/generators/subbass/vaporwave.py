"""``subbass vaporwave`` — 2-bar root drones.

Vaporwave wants everything slow-mo. Two-bar root cells sustain
under the chord stabs + tape-warble melody, giving the chest-feel
weight that vaporwave's nostalgic mids alone can't deliver.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext

from ._common import drone_generate


@register_generator("subbass", "vaporwave")
class SubBassVaporwave(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        yield from drone_generate(self, ctx, bars_per_note=2)
