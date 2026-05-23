"""``melody acid`` — minimal, almost silent.

The 303 line *is* the melody in acid house, so this gen ships mostly
silent. When present, drops a single high stab note every 8 bars (an
echo of the kind of organ punctuations Phuture used on top of the
303). Knob ``intensity=0`` disables it entirely.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    gate_for,
    macro_knobs,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


@register_generator("melody", "acid")
class MelodyAcid(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        if intensity <= 0:
            return
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        tonic, _ = parse_key(ctx.key)
        ticks_per_bar = 4 * ctx.ppq
        ppq = ctx.ppq

        # One stab note every 8 bars on a random scale degree from
        # {tonic, 5th, octave} — the simplest possible organ stab.
        for bar in range(0, ctx.bars, 8):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            deg = ctx.rng.choice([0, 4, 7])  # root / 5th / octave
            pitch = scale_note(deg, tonic, "minor", 5 + octave_off)
            if not 0 <= pitch <= 127:
                continue
            tick = bar * ticks_per_bar
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            jitter = ctx.rng.randint(-3, 3)
            vel = max(1, min(127, int(round(base_vel * intensity * evo_mult)) + jitter))
            yield Note(
                tick=tick, duration=max(1, int(2 * ppq * gate)),
                channel=inst.channel, pitch=pitch, velocity=vel,
            )
