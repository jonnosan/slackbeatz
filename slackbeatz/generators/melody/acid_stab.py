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
    apply_mistake,
    melody_phrase_bump,
    apply_gate_jitter,
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
    transposed_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    mistakes_for,
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


@register_generator("melody", "acid_stab")
class MelodyAcidStab(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        if intensity <= 0:
            return
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="minor")

        mistakes = mistakes_for(self)

        tonic, _ = parse_key(ctx.key)
        ticks_per_bar = ctx.ticks_per_bar
        ppq = ctx.ppq

        # One stab note every 8 bars on a random scale degree from
        # {tonic, 5th, octave} — the simplest possible organ stab.
        for bar in range(0, ctx.bars, 8):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            deg = ctx.rng.choice([0, 4, 7])  # root / 5th / octave
            pitch = transposed_pitch(
                scale_note(deg, tonic, scale, 5 + octave_off),
                ctx.transpose_semitones,
            )
            if not 0 <= pitch <= 127:
                continue
            tick = bar * ticks_per_bar
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            jitter = ctx.rng.randint(-3, 3)
            vel = max(1, min(127, int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter + melody_phrase_bump(bar, self)))
            base_dur = max(1, int(2 * ppq * gate))
            dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
            pitch, tick, vel = apply_mistake(pitch, tick, vel, mistakes, ctx.rng)
            yield Note(
                tick=tick, duration=dur,
                channel=inst.channel, pitch=pitch, velocity=vel,
            )
