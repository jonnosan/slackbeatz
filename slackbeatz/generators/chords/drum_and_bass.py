"""``chords drum_and_bass`` — lush atmospheric pad voicings.

Sustained 9th-flavoured voicings (root + 3rd + 5th + 9th) on a slow
i-iv chord progression. The lush pad sits underneath the breakbeat
energy.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    build_chord,
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    gate_for,
    gate_jitter_for,
    inversion_for,
    macro_knobs,
    progression_for,
    scale_for,
    voicing_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key


@register_generator("chords", "drum_and_bass")
class ChordsDrumAndBass(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="dorian")

        tonic, _ = parse_key(ctx.key)
        prog = progression_for(self, default_name="i-iv", default_bars=8)
        voicing = voicing_for(self, fallback="ninth")
        inversion = inversion_for(self)
        ticks_per_bar = ctx.ticks_per_bar
        chord_ticks = prog.bars_per_chord * ticks_per_bar
        base_dur = max(1, int(chord_ticks * gate))

        bar = 0
        while bar < ctx.bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += prog.bars_per_chord
                continue
            chord_root = prog.degree_at_bar(bar)
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-3, 3)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            vel = max(1, min(127, int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter))
            chord_pitches = build_chord(
                chord_root, tonic=tonic, scale=scale,
                base_octave=4 + octave_off,
                voicing=voicing, inversion=inversion,
                transpose=ctx.transpose_semitones,
            )
            for pitch in chord_pitches:
                remaining = (ctx.bars - bar) * ticks_per_bar
                dur = apply_gate_jitter(min(base_dur, remaining - 1), gate_jitter, ctx.rng)
                yield Note(
                    tick=tick, duration=max(1, dur),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord
