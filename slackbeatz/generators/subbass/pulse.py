"""``subbass pulse`` — short root pulses at named step positions.

Consolidated algorithm for every sub-bass voice whose shape is
"short hits at fixed steps within each bar". Replaces the 3 styles
this covers (euclid / garage / psytrance) with one algorithm that
picks a step pattern by name:

* ``pattern=quarter`` — steps [0, 4, 8, 12] (psytrance — quarter
  pulses that read as a continuous sub under the gallop)
* ``pattern=kick_beats`` — steps [0, 8] (garage — punchy on the 1
  and 3 only)
* ``pattern=euclid_3_16`` — euclidean(3, 16) → [0, 5, 11] (euclid
  style — sparse open-ended hits)

``step_dur_frac`` scales the per-step gate to taste (psytrance
wants ~4.0 so pulses almost touch; garage wants ~3.0 for snap).
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event
from slackbeatz.generators._shared import euclid
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext

from ._common import pulse_generate


_PATTERNS: dict[str, list[int]] = {
    "quarter":     [0, 4, 8, 12],
    "kick_beats":  [0, 8],
    # euclid_3_16 resolved at gen time — see generate().
}


@register_generator("subbass", "pulse")
class SubBassPulse(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        pattern_name = self.knob_str("pattern", "quarter")
        if pattern_name == "euclid_3_16":
            pat = euclid(3, ctx.steps_per_bar)
            steps = [s for s, hit in enumerate(pat) if hit]
        else:
            steps = list(_PATTERNS.get(pattern_name, _PATTERNS["quarter"]))
        step_dur_frac = self.knob_float("step_dur_frac", 3.0)
        yield from pulse_generate(
            self, ctx, steps=steps, step_dur_frac=step_dur_frac,
        )
