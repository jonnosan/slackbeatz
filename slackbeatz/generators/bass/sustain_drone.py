"""``bass dub_techno`` — sustained drone, one note per 4 bars.

Long-gate root drone underneath everything else. Switches between
root and fifth on the i-iv chord changes (so the bass walks too, but
slowly). Light sidechain ducking from the kick. Soft velocity — this
is the floor, not the foreground.
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
    sidechain_envelope,
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
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


@register_generator("bass", "sustain_drone")
class BassSustainDrone(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        duck = duck_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="dorian")

        tonic, _ = parse_key(ctx.key)
        prog = ChordProgression("i-iv", bars_per_chord=8)
        ticks_per_bar = ctx.ticks_per_bar

        bar = 0
        while bar < ctx.bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += prog.bars_per_chord
                continue
            chord_root = prog.degree_at_bar(bar)
            tick = bar * ticks_per_bar
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            jitter = ctx.rng.randint(-3, 3)
            vel_base = int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter
            env = sidechain_envelope(0, ctx.ppq, duck=duck)
            vel = max(1, min(127, int(round(vel_base * env))))
            # Use the chord-root scale degree at octave 2 + octave_off,
            # so the drone sits at A1 / D2 / etc. depending on the part's
            # key and the chord's degree.
            pitch = scale_note(chord_root, tonic, scale, 2 + octave_off)
            pitch = transposed_pitch(pitch, ctx.transpose_semitones)
            if 0 <= pitch <= 127:
                # Drone lasts the full chord duration (8 bars by default).
                remaining = (ctx.bars - bar) * ticks_per_bar
                base_dur = min(prog.bars_per_chord * ticks_per_bar, remaining - 1)
                dur = max(1, apply_gate_jitter(int(base_dur * gate), gate_jitter, ctx.rng))
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord
