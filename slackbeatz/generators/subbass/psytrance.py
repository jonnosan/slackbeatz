"""``subbass psytrance`` — quarter-note root pulses.

Psy's classic kick + gallop topology has a hole at the bottom of the
mix: the gallop bass sits in the midrange, the kick is one-shot at
the start of each beat, and there's no sustained sub. This gen fills
it — a root pulse on every quarter note (steps 0, 4, 8, 12) with a
short gate so each pulse pumps off before the next kick.

Strong sidechain duck (per ``BASS_DUCK['psytrance']`` = 0.45) keeps
the kick punch intact.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext

from ._common import pulse_generate


@register_generator("subbass", "psytrance")
class SubBassPsytrance(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        # Quarter-note pulses. step_dur_frac=4 stretches a 16th-step
        # base duration up to nearly a full quarter so adjacent pulses
        # almost touch — the sub feels continuous under the gallop.
        yield from pulse_generate(
            self, ctx, steps=[0, 4, 8, 12], step_dur_frac=4.0,
        )
