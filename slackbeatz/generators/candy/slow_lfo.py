"""``candy deep_techno`` — slow constant CC modulation, not just builds.

Unlike ``candy euclid`` which fires only at build-into-drop, this gen
keeps a slow filter / resonance modulation going across all parts. The
modulation rate is keyed off the part's tempo, and the depth scales
with ``intensity``.
"""

from __future__ import annotations

import math
from typing import Iterator

from slackbeatz.engine.event import CC, Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import macro_knobs
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


@register_generator("candy", "slow_lfo")
class CandySlowLfo(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        if inst is None:
            return
        macro = macro_knobs(self)
        if macro["mute_prob"] > 0 and ctx.rng.random() < macro["mute_prob"]:
            return

        intensity = self.knob_float("intensity", 1.0)
        depth = self.knob_float("density", 0.4)
        cc_num = self.knob_int("cc", 74)
        # Slow LFO: one full cycle every 8 bars by default.
        cycle_bars = self.knob_int("cycle", 8)

        ticks_per_bar = ctx.ticks_per_bar
        total_ticks = ctx.bars * ticks_per_bar
        # 32 CC events per bar (every 16th note) — smooth enough for a slow LFO.
        events_per_bar = 32
        step_ticks = ticks_per_bar // events_per_bar
        cycle_ticks = cycle_bars * ticks_per_bar

        # Phase offset randomised per part instance for organic feel.
        phase = ctx.rng.random() * math.tau

        n = ctx.bars * events_per_bar
        for i in range(n):
            tick = i * step_ticks
            if tick >= total_ticks:
                break
            theta = phase + math.tau * tick / cycle_ticks
            # Sine LFO in [-1, 1]; scale to [0, 127] by depth+intensity.
            lfo = (math.sin(theta) + 1.0) / 2.0
            value = int(round(20 + lfo * 80 * depth * intensity))
            yield CC(
                tick=tick, channel=inst.channel, controller=cc_num,
                value=max(0, min(127, value)),
            )
