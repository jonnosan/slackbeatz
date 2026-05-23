"""``chords lofi`` — sustained jazz-9th Rhodes pads.

The harmonic centerpiece of lofi: a Rhodes EP playing 9th-chord
voicings (root + 3rd + 5th + 7th + 9th) sustained across each chord
of the progression. Default progression is ii-V-I (the classic jazz
cadence) over 4 bars each. Gate is high (~0.95) so the chords bleed
into each other.

Knobs:
  voicing       chord shape (defaults to ``ninth``; try ``shell``
                for a sparser jazz feel or ``sus2`` for openness)
  inversion     bass note rotation
  progression   pick a different progression (see /knobs)
  bars_per_chord  cadence override
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    build_chord,
    chord_velocity_mods,
    evolution_multiplier,
    maybe_emit_drop_sweep,
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


@register_generator("chords", "lofi")
class ChordsLofi(Generator):
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
        prog = progression_for(self, default_name="ii-V-I", default_bars=4)
        voicing = voicing_for(self, fallback="ninth")
        inversion = inversion_for(self)

        ticks_per_bar = ctx.ticks_per_bar
        chord_ticks = prog.bars_per_chord * ticks_per_bar
        base_dur = max(1, int(chord_ticks * gate))

        # Modest reverb send at part start — lofi often runs wet.
        reverb = self.knob_int("reverb", 70)
        if reverb > 0:
            yield CC(
                tick=0, channel=inst.channel, controller=91,
                value=max(0, min(127, reverb)),
            )

        bar = 0
        while bar < ctx.bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += prog.bars_per_chord
                continue
            chord_root = prog.degree_at_bar(bar)
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-4, 4)
            evo_mult = evolution_multiplier(
                bar, ctx.bars, macro["evolution"], direction,
            )
            vel = max(
                1,
                min(
                    127,
                    int(round(base_vel * intensity * evo_mult * ctx.tension))
                    + jitter + chord_velocity_mods(bar, chord_root, base_vel, self),
                ),
            )

            chord_pitches = build_chord(
                chord_root, tonic=tonic, scale=scale,
                base_octave=4 + octave_off,
                voicing=voicing, inversion=inversion,
                transpose=ctx.transpose_semitones,
            )
            remaining = (ctx.bars - bar) * ticks_per_bar
            for pitch in chord_pitches:
                dur = apply_gate_jitter(
                    min(base_dur, remaining - 1), gate_jitter, ctx.rng,
                )
                yield Note(
                    tick=tick, duration=max(1, dur),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord
        yield from maybe_emit_drop_sweep(ctx, inst.channel, self)
