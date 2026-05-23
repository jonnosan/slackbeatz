"""``melody garage`` — vocal-style stab phrases.

Short, syncopated, mid-register phrases that emulate chopped vocal
samples. Pentatonic minor for the soulful R&B feel.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    evolution_multiplier,
    maybe_passing_tone,
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
    passing_tones_for,
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


@register_generator("melody", "garage")
class MelodyGarage(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        passing_tones = passing_tones_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="minor_pentatonic")

        tonic, _ = parse_key(ctx.key)

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            # 3-5 short stabs per bar at random 16th-note positions.
            n_stabs = ctx.rng.randint(3, 5)
            slots = sorted(ctx.rng.sample(range(ctx.steps_per_bar), n_stabs))
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            bar_start = bar * ctx.ticks_per_bar
            step_ticks = ctx.ppq // 4   # 16th
            for step in slots:
                deg = ctx.rng.choice([0, 2, 3, 4])  # pent minor degrees
                pitch = transposed_pitch(
                    scale_note(deg, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                pitch = maybe_passing_tone(pitch, passing_tones, ctx.rng)
                if not 0 <= pitch <= 127:
                    continue
                tick = bar_start + step * step_ticks
                # Short stab — half a 16th by default.
                base_dur = max(1, int(step_ticks * gate))
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                jitter = ctx.rng.randint(-4, 4)
                vel = max(1, min(127, int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter))
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
