"""``bass euclid`` — rolling root-note 8ths in the part's key.

Pulse count = 8 by default (8th notes through the bar), with a light
skip on beat 1 of every other bar so it feels less mechanical. Notes
are the root of the part's key, transposed by ``octave`` (default ``-1``
when the user writes ``gen bass bass euclid octave=-1``).
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


@register_generator("bass", "euclid")
class BassEuclid(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched, "bass needs a pitched instrument"

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        duck = duck_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        octave_jump = octave_jump_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        tonic, _scale = parse_key(ctx.key)
        # Bass register: octave 2 by default (E2 ~ 40). octave_off shifts.
        root = transposed_pitch(
            midi_note(tonic, 2 + octave_off), ctx.transpose_semitones
        )

        pulses = 8
        pattern = euclid(pulses, ctx.steps_per_bar, 0)

        step_ticks = step_duration(ctx.ppq)
        ticks_per_bar = ctx.ticks_per_bar
        base_dur = max(1, int(step_ticks * 2 * gate))  # 8th note long * gate

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            bar_start = bar * ticks_per_bar
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            # Skip beat 1 on every other bar (structural "drop" on top of
            # the sidechain pumping that follows).
            duck_beat1 = bar % 2 == 1
            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                if duck_beat1 and step == 0:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                jitter = ctx.rng.randint(-6, 6)
                vel_base = int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter
                env = sidechain_envelope(tick - bar_start, ctx.ppq, duck=duck)
                vel = max(1, min(127, int(round(vel_base * env))))
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                pitch = maybe_octave_jump(root, octave_jump, ctx.rng)
                yield Note(
                    tick=tick, duration=dur, channel=inst.channel,
                    pitch=pitch, velocity=vel,
                )
