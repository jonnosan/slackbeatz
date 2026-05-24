"""``subbass lofi`` — whole-note root, one per bar.

Lofi sits back — a fingered upright bass plays the walking line on
ch 2, the sub layer just glues the room together with a steady root
note per bar. No sidechain (BASS_DUCK['lofi'] = 1.0) so the sub
breathes freely under the dusty drums.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext

from ._common import drone_generate


@register_generator("subbass", "lofi")
class SubBassLofi(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        yield from drone_generate(self, ctx, bars_per_note=1)
