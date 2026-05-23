"""``chords deep_techno`` — minor-7th voicings on a 2-chord progression.

i and iv only, each lasting 8 bars. The minor 7th (degree 6 of the
chord-root scale) gives the jazzy / Detroit-deep-techno colour.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    build_chord,
    drop_sweep_events,
    evolution_multiplier,
    expression_ramp,
    is_build_part,
    pick_evolution_direction,
    should_mute_bar,
    tension_velocity_boost,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    drop_intensity_for,
    gate_for,
    gate_jitter_for,
    inversion_for,
    macro_knobs,
    phrase_lift_for,
    progression_for,
    scale_for,
    tension_dyn_for,
    voicing_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key


@register_generator("chords", "deep_techno")
class ChordsDeepTechno(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        arp_prob = self.knob_float("arp_prob", 0.0)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="minor")

        tonic, _ = parse_key(ctx.key)
        # Progression + voicing knobs (with style defaults). Users can
        # override any of these per-gen, e.g.:
        #   gen pad chords deep_techno progression=I-V-vi-IV voicing=ninth inversion=1
        prog = progression_for(self, default_name="i-iv", default_bars=8)
        voicing = voicing_for(self, fallback="seventh")  # min7 by default
        inversion = inversion_for(self)
        tension_dyn = tension_dyn_for(self)
        phrase_lift = phrase_lift_for(self)

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
            # Phrase lift fires +8 on bar 0 of each N-bar phrase.
            phrase_bump = 8 if phrase_lift > 0 and bar % phrase_lift == 0 else 0
            # Tension-dyn boost based on which chord we're on (0 = tonic,
            # 4 = dominant → maximum boost).
            tension_boost = tension_velocity_boost(chord_root, tension_dyn, base_vel)
            vel = max(
                1,
                min(
                    127,
                    int(round(base_vel * intensity * evo_mult * ctx.tension))
                    + jitter + phrase_bump + tension_boost,
                ),
            )

            chord_pitches = build_chord(
                chord_root, tonic=tonic, scale=scale,
                base_octave=4 + octave_off,
                voicing=voicing, inversion=inversion,
                transpose=ctx.transpose_semitones,
            )
            remaining = (ctx.bars - bar) * ticks_per_bar

            if arp_prob > 0 and ctx.rng.random() < arp_prob and chord_pitches:
                # Slow 8th-note arpeggio for deep_techno's longer chord
                # durations — 16ths would be too busy for 8-bar chords.
                step_ticks = ctx.ppq // 2
                n_steps = max(1, min(chord_ticks, remaining) // step_ticks)
                arp_dur = max(1, int(step_ticks * 0.85))
                for i in range(n_steps):
                    arp_tick = tick + i * step_ticks
                    pitch = chord_pitches[i % len(chord_pitches)]
                    yield Note(
                        tick=arp_tick, duration=apply_gate_jitter(arp_dur, gate_jitter, ctx.rng),
                        channel=inst.channel, pitch=pitch, velocity=vel,
                    )
            else:
                for pitch in chord_pitches:
                    dur = apply_gate_jitter(min(base_dur, remaining - 1), gate_jitter, ctx.rng)
                    yield Note(
                        tick=tick, duration=max(1, dur),
                        channel=inst.channel, pitch=pitch, velocity=vel,
                    )
            bar += prog.bars_per_chord

        # Gentle expression swell on build → drop. Deep techno wants a
        # smaller dynamic range than euclid (75 → 110 instead of
        # 80 → 127) — it should feel restrained, not climactic.
        if is_build_part(ctx):
            yield from expression_ramp(ctx, inst.channel, start=75, end=110)

        # Drop automation: coordinated CC sweep (filter+reverb+volume)
        # across the last 4 bars of this part if the next part is a drop.
        drop_intensity = drop_intensity_for(self)
        if drop_intensity > 0 and ctx.next_role == "drop":
            yield from drop_sweep_events(ctx, inst.channel, drop_intensity)
