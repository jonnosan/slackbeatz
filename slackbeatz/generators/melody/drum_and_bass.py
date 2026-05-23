"""``melody drum_and_bass`` — sparse jazz-flavoured phrases.

Picks notes from the dorian scale, 2-3 notes per 4 bars, with longer
sustains. Channels the atmospheric "liquid DnB" feel.
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


_DEGREES = [0, 2, 4, 6, 8]


@register_generator("melody", "drum_and_bass")
class MelodyDrumAndBass(Generator):
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
        scale = scale_for(self, ctx, fallback="dorian")

        tonic, _ = parse_key(ctx.key)
        ticks_per_bar = ctx.ticks_per_bar

        for bar in range(0, ctx.bars, 4):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            n_notes = ctx.rng.choice([2, 3])
            slots = sorted(ctx.rng.sample(range(16), n_notes))
            for s in slots:
                deg = ctx.rng.choice(_DEGREES)
                pitch = transposed_pitch(
                    scale_note(deg, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                pitch = maybe_passing_tone(pitch, passing_tones, ctx.rng)
                if not 0 <= pitch <= 127:
                    continue
                # Quarter-note grid relative to 4-bar window start.
                tick = bar * ticks_per_bar + s * ctx.ppq
                base_dur = int(2 * ctx.ppq * gate)
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
                jitter = ctx.rng.randint(-4, 4)
                vel = max(1, min(127, int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter))
                yield Note(
                    tick=tick, duration=max(1, dur),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
