"""``chords garage`` — jazzy minor-7th stabs on a 4-chord progression.

Short stab voicings on beats 1 and 3 (matching the 2-step drum
pattern). Minor 7th voicing for the soulful R&B-into-garage flavour.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    chord_velocity_mods,
    maybe_emit_drop_sweep,
    apply_gate_jitter,
    build_chord,
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
    step_to_ticks,
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


@register_generator("chords", "garage")
class ChordsGarage(Generator):
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
        scale = scale_for(self, ctx, fallback="minor")

        tonic, _ = parse_key(ctx.key)
        prog = progression_for(self, default_name="i-VI-ii-IV", default_bars=4)
        voicing = voicing_for(self, fallback="seventh")
        inversion = inversion_for(self)
        ticks_per_bar = ctx.ticks_per_bar

        # Stab on beats 1 and 3 of each bar — quarter-bar grid.
        beat_step = ctx.steps_per_bar // 4
        stab_steps = (0, beat_step * 2)  # beats 1 and 3

        bar = 0
        while bar < ctx.bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += prog.bars_per_chord
                continue
            chord_root = prog.degree_at_bar(bar)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            base_dur = max(1, int(beat_step * (ctx.ppq // 4) * gate))  # ~beat duration * gate
            # Build the chord pitches once for this chord; same set is
            # stabbed on each beat across each bar of the chord.
            chord_pitches = build_chord(
                chord_root, tonic=tonic, scale=scale,
                base_octave=4 + octave_off,
                voicing=voicing, inversion=inversion,
                transpose=ctx.transpose_semitones,
            )
            # Stab for each bar of the chord placement.
            for stab_bar in range(prog.bars_per_chord):
                if bar + stab_bar >= ctx.bars:
                    break
                bar_start = (bar + stab_bar) * ticks_per_bar
                jitter = ctx.rng.randint(-3, 3)
                vel = max(1, min(127, int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter + chord_velocity_mods(bar, chord_root, base_vel, self)))
                for step in stab_steps:
                    if step >= ctx.steps_per_bar:
                        continue
                    tick = bar_start + step_to_ticks(step, ctx.ppq)
                    for pitch in chord_pitches:
                        dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                        yield Note(
                            tick=tick, duration=max(1, dur),
                            channel=inst.channel, pitch=pitch, velocity=vel,
                        )
            bar += prog.bars_per_chord
        yield from maybe_emit_drop_sweep(ctx, inst.channel, self)
