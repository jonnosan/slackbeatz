"""``candy acid`` — periodic noise/whoosh on build → drop.

Acid tracks don't usually rely on candy FX — the 303 *is* the FX —
but a build into the drop still benefits from a single quick filter
sweep. Quieter and shorter than ``candy euclid``; lets the 303 stay
the centre of attention.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note
from slackbeatz.generators._shared import is_build_part
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import macro_knobs
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note


@register_generator("candy", "acid_sweep")
class CandyAcidSweep(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        if inst is None:
            return
        if not is_build_part(ctx):
            return
        macro = macro_knobs(self)
        if macro["mute_prob"] > 0 and ctx.rng.random() < macro["mute_prob"]:
            return

        intensity = self.knob_float("intensity", 1.0)
        cc_num = self.knob_int("cc", 74)
        ticks_per_bar = ctx.ticks_per_bar
        total_ticks = ctx.bars * ticks_per_bar
        # Short ramp — last 2 bars only.
        ramp_bars = min(2, ctx.bars)
        ramp_start = total_ticks - ramp_bars * ticks_per_bar
        n = 16
        for i in range(n):
            tick = ramp_start + int((total_ticks - ramp_start) * i / max(1, n - 1))
            frac = i / max(1, n - 1)
            yield CC(
                tick=tick, channel=inst.channel, controller=cc_num,
                value=max(0, min(127, int(round(20 + 100 * frac * intensity)))),
            )
        # Single noise burst on the last beat.
        if inst.is_pitched:
            tonic, _ = parse_key(ctx.key)
            pitch = midi_note(tonic, 5)
        else:
            assert inst.note is not None
            pitch = inst.note
        yield Note(
            tick=max(0, total_ticks - ctx.ppq // 4),
            duration=ctx.ppq // 2,
            channel=inst.channel, pitch=pitch, velocity=105,
        )
