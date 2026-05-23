"""``chords euclid`` — sustained pad voicings on a 4-chord progression.

Default progression (Arduino-derived): **i-VI-ii-IV** in the part's
minor key, one chord per 4 bars. Voicing is a simple triad (root + 3rd
+ 5th) of the chord-root scale degree, sustained for the chord duration.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    ChordProgression,
    apply_gate_jitter,
    evolution_multiplier,
    expression_ramp,
    is_build_part,
    pick_evolution_direction,
    should_mute_bar,
    transposed_pitch,
    voice_lead,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    scale_for,
    voice_lead_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


# Chord voicings as scale-degree offsets from the chord root: triad.
_TRIAD = (0, 2, 4)


@register_generator("chords", "euclid")
class ChordsEuclid(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        arp_prob = self.knob_float("arp_prob", 0.0)
        # Issue #6: when set, snap each chord tone to the nearest pitch
        # in the next chord rather than emitting at the literal voicing
        # offset. Smoother chord-to-chord motion.
        do_voice_lead = voice_lead_for(self)
        prev_pitches: list[int] = []
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="minor")

        tonic, _ = parse_key(ctx.key)
        progression = ChordProgression("i-VI-ii-IV", bars_per_chord=4)

        ticks_per_bar = ctx.ticks_per_bar
        chord_ticks = progression.bars_per_chord * ticks_per_bar
        base_dur = max(1, int(chord_ticks * gate))

        bars = ctx.bars
        bar = 0
        while bar < bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += progression.bars_per_chord
                continue
            chord_root = progression.degree_at_bar(bar)
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-4, 4)
            evo_mult = evolution_multiplier(bar, bars, macro["evolution"], direction)
            vel = max(1, min(127, int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter))

            # Build the chord's pitches once. Used both for held and arp.
            chord_pitches = [
                transposed_pitch(
                    scale_note(chord_root + deg_off, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                for deg_off in _TRIAD
            ]
            chord_pitches = [p for p in chord_pitches if 0 <= p <= 127]
            # Voice leading: re-voice the new chord so each tone is the
            # nearest octave equivalent to the previous chord's tones.
            if do_voice_lead and prev_pitches and chord_pitches:
                chord_pitches = voice_lead(prev_pitches, chord_pitches)
            prev_pitches = list(chord_pitches)
            remaining = (bars - bar) * ticks_per_bar

            if arp_prob > 0 and ctx.rng.random() < arp_prob and chord_pitches:
                # Issue #5: arpeggio variant — cycle through the voicing
                # in 16th-notes for the whole chord duration.
                step_ticks = ctx.ppq // 4
                n_steps = max(1, min(chord_ticks, remaining) // step_ticks)
                arp_dur = max(1, int(step_ticks * 0.75))
                for i in range(n_steps):
                    arp_tick = tick + i * step_ticks
                    pitch = chord_pitches[i % len(chord_pitches)]
                    yield Note(
                        tick=arp_tick, duration=apply_gate_jitter(arp_dur, gate_jitter, ctx.rng),
                        channel=inst.channel, pitch=pitch, velocity=vel,
                    )
            else:
                # Normal held chord.
                for pitch in chord_pitches:
                    dur = apply_gate_jitter(min(base_dur, remaining - 1), gate_jitter, ctx.rng)
                    yield Note(
                        tick=tick, duration=max(1, dur),
                        channel=inst.channel, pitch=pitch, velocity=vel,
                    )
            bar += progression.bars_per_chord

        # Build → drop: swell the chord channel via CC 11 so the
        # transition has actual loudness motion, not just brightness.
        if is_build_part(ctx):
            yield from expression_ramp(ctx, inst.channel, start=80, end=127)
