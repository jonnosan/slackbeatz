"""``chords acid_stab`` — sparse, filter-enveloped acid stab.

Replaces the ``sustained_dyad`` organ pad on acid tracks. Real acid
production rarely uses sustained chords — the 303 *is* the focal
element, and any chordal presence shows up as short filter-enveloped
stabs that punctuate the groove rather than wash under it.

This algorithm:

* Plays one short note per bar on the "and-of-2" (step 6 of a 16-step
  bar), syncopated against the kick on 1.
* Picks root or fifth pitch in the same register as the bass (octave
  3 — lower than a melody but higher than the sub). Single-note, not a
  dyad — a chordy stab in acid is usually a synth with stacked saws,
  not multiple notes.
* Per-note CC 74 filter envelope: sweeps high → low over the gate
  duration (~quarter-note). That envelope shape is the entire point of
  this algorithm — the stab "blooms" then closes.
* Optional CC 71 resonance bump per note for extra squelch.

Sparser than the bass on purpose — the listener's ear should be on
the 303, not on the chord.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    chord_velocity_mods,
    evolution_multiplier,
    maybe_emit_drop_sweep,
    pick_evolution_direction,
    should_mute_bar,
    step_duration,
    step_to_ticks,
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


# Filter envelope: how many CC74 events per note. 8 = smooth enough for
# a quarter-note "bloom" without flooding the MIDI bus.
_ENVELOPE_STEPS = 8
# CC74 envelope shape — values along the envelope from attack to decay.
# Starts low (filter mostly closed → attack opens it briefly), rises to
# the bloom, decays back. Mimics a snappy ADSR with short decay.
_ENVELOPE_CURVE = (40, 95, 110, 105, 85, 65, 50, 35)


@register_generator("chords", "acid_stab")
class ChordsAcidStab(Generator):
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
        resonance_bump = self.knob_int("resonance", 0)
        # "fifth_prob" — chance the stab lands on the 5th instead of
        # the root. Default 0.4 so songs vary between root + fifth
        # punctuation across bars.
        fifth_prob = self.knob_float("fifth_prob", 0.4)

        tonic, _ = parse_key(ctx.key)
        ticks_per_bar = ctx.ticks_per_bar
        step_ticks = step_duration(ctx.ppq)
        # Stab gate is one beat long (~quarter note). Long enough for
        # the filter envelope to read; short enough to not pad.
        stab_dur = max(1, int(ctx.ppq * gate))
        root_raw = midi_note(tonic, 3 + octave_off)
        root = transposed_pitch(root_raw, ctx.transpose_semitones)
        fifth = transposed_pitch(root_raw + 7, ctx.transpose_semitones)

        # Stab lands on the "and-of-2" (step 6 of 16) — the classic
        # acid offbeat-3 placement that sits between kick hits.
        stab_step = 6
        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            evo_mult = evolution_multiplier(
                bar, ctx.bars, macro["evolution"], direction,
            )
            bar_start = bar * ticks_per_bar
            tick = bar_start + step_to_ticks(stab_step, ctx.ppq)
            pitch = fifth if ctx.rng.random() < fifth_prob else root
            if not 0 <= pitch <= 127:
                continue
            note_dur = apply_gate_jitter(stab_dur, gate_jitter, ctx.rng)
            jitter = ctx.rng.randint(-3, 3)
            vel = max(1, min(127, int(round(
                base_vel * intensity * evo_mult * ctx.tension,
            )) + jitter + chord_velocity_mods(bar, 0, base_vel, self)))

            # Filter envelope BEFORE + DURING the note: pre-load CC74
            # so the synth has the right cutoff when note_on lands.
            env_span = max(1, note_dur)
            for i, cc_val in enumerate(_ENVELOPE_CURVE):
                env_tick = tick + int(env_span * i / max(1, len(_ENVELOPE_CURVE) - 1))
                yield CC(
                    tick=env_tick, channel=inst.channel,
                    controller=74, value=cc_val,
                )

            # Optional resonance bump at note_on — synth becomes more
            # squelchy for this hit. Decays naturally as the note ends.
            if resonance_bump > 0:
                yield CC(
                    tick=tick, channel=inst.channel,
                    controller=71, value=max(0, min(127, resonance_bump)),
                )

            yield Note(
                tick=tick, duration=note_dur,
                channel=inst.channel, pitch=pitch, velocity=vel,
            )

        # Drop-sweep contribution (CC74/CC91/CC7 ramp into the next
        # part if it's a drop). Honours the gen's drop_intensity knob.
        yield from maybe_emit_drop_sweep(ctx, inst.channel, self)
