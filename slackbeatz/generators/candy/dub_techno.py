"""``candy dub_techno`` — slow textural drone modulation.

Like ``candy deep_techno`` but slower and wetter. Constant CC 74
(filter cutoff) and CC 91 (reverb send) LFO across the whole part,
no build-triggered sweeps — dub techno is *all* gradual motion, no
drops. Bell-like accents are suppressed (no foreground events).
"""

from __future__ import annotations

import math
from typing import Iterator

from slackbeatz.engine.event import CC, Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import macro_knobs
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


@register_generator("candy", "dub_techno")
class CandyDubTechno(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        if inst is None:
            return
        macro = macro_knobs(self)
        if macro["mute_prob"] > 0 and ctx.rng.random() < macro["mute_prob"]:
            return

        intensity = self.knob_float("intensity", 1.0)
        depth = self.knob_float("density", 0.5)
        cc_num = self.knob_int("cc", 74)
        # 32-bar LFO — even slower than deep_techno's 8-bar cycle.
        cycle_bars = self.knob_int("cycle", 32)

        ticks_per_bar = 4 * ctx.ppq
        total_ticks = ctx.bars * ticks_per_bar
        events_per_bar = 16
        step_ticks = ticks_per_bar // events_per_bar
        cycle_ticks = max(1, cycle_bars * ticks_per_bar)
        phase = ctx.rng.random() * math.tau

        n = ctx.bars * events_per_bar
        for i in range(n):
            tick = i * step_ticks
            if tick >= total_ticks:
                break
            theta = phase + math.tau * tick / cycle_ticks
            lfo = (math.sin(theta) + 1.0) / 2.0
            value = int(round(30 + 80 * lfo * depth * intensity))
            yield CC(
                tick=tick, channel=inst.channel, controller=cc_num,
                value=max(0, min(127, value)),
            )
            # CC 91 reverb send tracks the LFO inversely — when the
            # filter opens, the reverb tail drops back a touch, giving
            # the impression of the sound coming forward.
            reverb_val = int(round(120 - 30 * lfo * intensity))
            yield CC(
                tick=tick, channel=inst.channel, controller=91,
                value=max(0, min(127, reverb_val)),
            )
