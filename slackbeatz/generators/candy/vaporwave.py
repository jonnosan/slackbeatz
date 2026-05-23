"""``candy vaporwave`` — gentle CC swells with occasional bell accents.

Unlike ``candy psytrance`` (long sweeps into drops) or ``candy euclid``
(build-only), the vaporwave candy gen runs a *very* slow LFO across
the whole part — picture a Rhodes player slowly riding a wah pedal —
and drops a single high bell-like note on the downbeat of every fourth
bar so the sound has a periodic glint without ever feeling busy.
"""

from __future__ import annotations

import math
from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import macro_knobs
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note


@register_generator("candy", "vaporwave")
class CandyVaporwave(Generator):
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
        # 16-bar LFO cycle — slower than deep_techno, gives a glacial feel.
        cycle_bars = self.knob_int("cycle", 16)

        ticks_per_bar = ctx.ticks_per_bar
        total_ticks = ctx.bars * ticks_per_bar
        events_per_bar = 16
        step_ticks = ticks_per_bar // events_per_bar
        cycle_ticks = cycle_bars * ticks_per_bar

        phase = ctx.rng.random() * math.tau
        n = ctx.bars * events_per_bar
        for i in range(n):
            tick = i * step_ticks
            if tick >= total_ticks:
                break
            theta = phase + math.tau * tick / cycle_ticks
            lfo = (math.sin(theta) + 1.0) / 2.0
            value = int(round(30 + lfo * 70 * depth * intensity))
            yield CC(
                tick=tick, channel=inst.channel, controller=cc_num,
                value=max(0, min(127, value)),
            )

        # Bell glint on the downbeat of every fourth bar. Skipped on
        # bar 0 so the part doesn't start with a transient.
        if inst.is_pitched:
            tonic, _ = parse_key(ctx.key)
            bell_pitch = midi_note(tonic, 6)  # high register
        else:
            assert inst.note is not None
            bell_pitch = inst.note
        for bar in range(4, ctx.bars, 4):
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-6, 6)
            vel = max(1, min(127, 70 + jitter))
            yield Note(
                tick=tick, duration=ctx.ppq * 2,  # half-note bell ring
                channel=inst.channel, pitch=bell_pitch, velocity=vel,
            )
