"""``melody dub_techno`` — silent by default.

The chord stab carries all the melodic motion in dub techno. This gen
ships disabled (intensity=0 by default). If a user explicitly sets
``intensity=1`` they get a single distant pad note per 8 bars on a
random scale degree from {tonic, 5th, octave} — the kind of ghostly
melodic punctuation Maurizio uses sparingly.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
    transposed_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


@register_generator("melody", "dub_techno")
class MelodyDubTechno(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        # Off by default — dub techno melody is silence (or near it).
        intensity = self.knob_float("intensity", 0.0)
        if intensity <= 0:
            return

        octave_off = base_octave_for(self)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="dorian")

        tonic, _ = parse_key(ctx.key)
        ticks_per_bar = 4 * ctx.ppq

        for bar in range(0, ctx.bars, 8):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            deg = ctx.rng.choice([0, 4, 7])
            pitch = scale_note(deg, tonic, scale, 5 + octave_off)
            pitch = transposed_pitch(pitch, ctx.transpose_semitones)
            if not 0 <= pitch <= 127:
                continue
            tick = bar * ticks_per_bar
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            jitter = ctx.rng.randint(-3, 3)
            vel = max(1, min(127, int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter))
            base_dur = int(4 * ctx.ppq * gate)  # long sustain — 4 quarters
            dur = max(1, apply_gate_jitter(base_dur, gate_jitter, ctx.rng))
            yield Note(
                tick=tick, duration=dur,
                channel=inst.channel, pitch=pitch, velocity=vel,
            )
