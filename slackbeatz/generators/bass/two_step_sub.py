"""``bass garage`` — punchy syncopated sub-bass.

Euclidean 4-pulse pattern with an offbeat lean — the bass leans
into the gaps in the 2-step drum pattern.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    euclid,
    evolution_multiplier,
    maybe_octave_jump,
    pick_evolution_direction,
    should_mute_bar,
    sidechain_envelope,
    step_duration,
    step_to_ticks,
    transposed_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    duck_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    octave_jump_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note


@register_generator("bass", "two_step_sub")
class BassTwoStepSub(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        duck = duck_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        octave_jump = octave_jump_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        tonic, _ = parse_key(ctx.key)
        root = transposed_pitch(midi_note(tonic, 2 + octave_off), ctx.transpose_semitones)

        # 4 syncopated pulses across the bar — euclid distributes them
        # offbeat-ish vs the kick on 1 + snare on 3.
        pulses = 4
        pattern = euclid(pulses, ctx.steps_per_bar, 2)  # offset 2 = lean-offbeat

        step_ticks = step_duration(ctx.ppq)
        base_dur = max(1, int(step_ticks * 3 * gate))  # ~dotted 8th-ish

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction) * ctx.tension
            bar_start = bar * ctx.ticks_per_bar
            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                jitter = ctx.rng.randint(-4, 4)
                vel_base = int(round(base_vel * intensity * evo_mult)) + jitter
                env = sidechain_envelope(tick - bar_start, ctx.ppq, duck=duck)
                vel = max(1, min(127, int(round(vel_base * env))))
                pitch = maybe_octave_jump(root, octave_jump, ctx.rng)
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
