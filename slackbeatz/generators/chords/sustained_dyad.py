"""``chords acid`` — occasional sustained organ pad.

Acid tracks tend to be 303 + drums for long stretches, then a single
sustained organ chord enters in the back half. The default is silence
on early bars and a held root-fifth-octave dyad (no third — keeps it
modal) from the half-way point onward. ``mute_prob`` lets the user
roll for whether the pad enters this part at all.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    chord_velocity_mods,
    maybe_emit_drop_sweep,
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
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note


@register_generator("chords", "sustained_dyad")
class ChordsSustainedDyad(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        tonic, _ = parse_key(ctx.key)
        ticks_per_bar = ctx.ticks_per_bar

        # Skip the entire part with mute_prob — pads in acid house are
        # an occasional event rather than a constant presence.
        if should_mute_bar(ctx.rng, macro["mute_prob"]):
            return

        # Hold a root + 5th + octave dyad (no third = modal) from the
        # midpoint of the part to the end. Short gate ⇒ stabs, not pad.
        gate_jitter = gate_jitter_for(self)
        enter_bar = ctx.bars // 2
        tick = enter_bar * ticks_per_bar
        remaining = (ctx.bars - enter_bar) * ticks_per_bar
        base_dur = max(1, int(remaining * gate))
        dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
        root_raw = midi_note(tonic, 4 + octave_off)
        root = transposed_pitch(root_raw, ctx.transpose_semitones)
        fifth = transposed_pitch(root_raw + 7, ctx.transpose_semitones)
        octave_up = transposed_pitch(root_raw + 12, ctx.transpose_semitones)
        evo_mult = evolution_multiplier(enter_bar, ctx.bars, macro["evolution"], direction)
        jitter = ctx.rng.randint(-3, 3)
        # acid chord stab is fixed on the tonic (no progression); pass
        # 0 for chord_root_deg + enter_bar for bar so phrase_lift still
        # fires correctly if the stab happens to land on a phrase
        # boundary.
        vel = max(1, min(127, int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter + chord_velocity_mods(enter_bar, 0, base_vel, self)))
        for pitch in (root, fifth, octave_up):
            if not 0 <= pitch <= 127:
                continue
            yield Note(
                tick=tick, duration=max(1, dur),
                channel=inst.channel, pitch=pitch, velocity=vel,
            )
        yield from maybe_emit_drop_sweep(ctx, inst.channel, self)
