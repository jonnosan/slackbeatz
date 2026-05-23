"""``chords drum_and_bass`` — lush atmospheric pad voicings.

Sustained 9th-flavoured voicings (root + 3rd + 5th + 9th) on a slow
i-iv chord progression. The lush pad sits underneath the breakbeat
energy.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    ChordProgression,
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


_ADD9 = (0, 2, 4, 8)  # root, 3rd, 5th, 9th


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
        prog = ChordProgression("i-iv", bars_per_chord=8)
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
            for off in _ADD9:
                pitch = transposed_pitch(
                    scale_note(chord_root + off, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                if not 0 <= pitch <= 127:
                    continue
                remaining = (ctx.bars - bar) * ticks_per_bar
                dur = apply_gate_jitter(min(base_dur, remaining - 1), gate_jitter, ctx.rng)
                yield Note(
                    tick=tick, duration=max(1, dur),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord
