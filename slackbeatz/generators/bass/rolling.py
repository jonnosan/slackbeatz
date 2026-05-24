"""``bass euclid`` — rolling 8ths with optional chord-following, walking
notes, pickup anticipations, and chord-tone variation.

Defaults to the Arduino-derived behaviour: straight 8ths on the part's
tonic with a sidechain-pumped feel and a structural "skip beat 1 every
other bar" gesture. Five knobs progressively turn that into a real
bassline:

* ``progression=NAME`` — bass follows the named progression's chord
  roots instead of holding tonic. Recognised names match the chord
  generators (``i-iv``, ``i-VI-ii-IV``, ``ii-V-I``, etc.).
* ``bars_per_chord=N`` — how fast the progression advances.
* ``fifth_prob=N`` — probability (0..1) of playing the chord 5th
  instead of the root on any given step.
* ``walking=N`` — probability of inserting a chromatic step-up on the
  last 8th of a chord whose successor has a different root. Jazz /
  funk walking-bass behaviour.
* ``pickup=N`` — probability of inserting an 8th-note anticipation
  before each chord change. Hints the next root half a beat early.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    euclid,
    evolution_multiplier,
    maybe_octave_jump,
    pick_evolution_direction,
    should_mute_bar,
    sidechain_envelope,
    step_duration,
    step_to_ticks,
    transposed_pitch,
    walking_step_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    bass_progression_for,
    base_octave_for,
    base_vel_for,
    duck_for,
    fifth_prob_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    octave_jump_for,
    pickup_for,
    scale_for,
    walking_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note, scale_note


@register_generator("bass", "rolling")
class BassRolling(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched, "bass needs a pitched instrument"

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        duck = duck_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        octave_jump = octave_jump_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="minor")

        # Bass register: octave 2 by default (E2 ~ 40). octave_off shifts.
        base_octave = 2 + octave_off

        # Optional chord-following + variety knobs.
        prog = bass_progression_for(self)
        fifth_prob = fifth_prob_for(self)
        walking = walking_for(self)
        pickup = pickup_for(self)

        tonic, _scale = parse_key(ctx.key)

        def root_for_bar(bar: int) -> int:
            """MIDI note of the chord root at *bar*."""
            chord_deg = prog.degree_at_bar(bar) if prog is not None else 0
            return transposed_pitch(
                scale_note(chord_deg, tonic, scale, base_octave),
                ctx.transpose_semitones,
            )

        def fifth_for_bar(bar: int) -> int:
            chord_deg = prog.degree_at_bar(bar) if prog is not None else 0
            # +4 scale degrees ≈ a 5th in any minor / major mode.
            return transposed_pitch(
                scale_note(chord_deg + 4, tonic, scale, base_octave),
                ctx.transpose_semitones,
            )

        # Fallback when no progression: hold the part's tonic.
        if prog is None:
            root_for_bar = lambda _b, _r=midi_note(tonic, base_octave): transposed_pitch(_r, ctx.transpose_semitones)  # noqa: E731
            fifth_for_bar = lambda _b, _r=midi_note(tonic + 7, base_octave): transposed_pitch(_r, ctx.transpose_semitones)  # noqa: E731

        pulses = 8
        pattern = euclid(pulses, ctx.steps_per_bar, 0)

        step_ticks = step_duration(ctx.ppq)
        ticks_per_bar = ctx.ticks_per_bar
        base_dur = max(1, int(step_ticks * 2 * gate))  # 8th note long * gate

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            bar_start = bar * ticks_per_bar
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            # Skip beat 1 on every other bar (structural "drop" on top
            # of the sidechain pumping).
            duck_beat1 = bar % 2 == 1
            # Chord change detection — is the next bar a different chord?
            chord_changes_next = False
            if prog is not None and bar + 1 < ctx.bars:
                chord_changes_next = (
                    prog.degree_at_bar(bar) != prog.degree_at_bar(bar + 1)
                )
            bar_root = root_for_bar(bar)
            bar_fifth = fifth_for_bar(bar)

            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                if duck_beat1 and step == 0:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                jitter = ctx.rng.randint(-6, 6)
                vel_base = int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter
                env = sidechain_envelope(tick - bar_start, ctx.ppq, duck=duck)
                vel = max(1, min(127, int(round(vel_base * env))))
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)

                # Note pitch selection.
                if step == ctx.steps_per_bar - 2 and chord_changes_next and walking > 0 and ctx.rng.random() < walking:
                    # Walking step into the next chord's root.
                    next_root = root_for_bar(bar + 1)
                    pitch = walking_step_pitch(bar_root, next_root)
                elif fifth_prob > 0 and ctx.rng.random() < fifth_prob:
                    pitch = bar_fifth
                else:
                    pitch = bar_root
                pitch = maybe_octave_jump(pitch, octave_jump, ctx.rng)
                yield Note(
                    tick=tick, duration=dur, channel=inst.channel,
                    pitch=pitch, velocity=vel,
                )

            # Pickup anticipation: on the LAST 16th of the bar before a
            # chord change, fire the next chord's root with reduced
            # velocity. Acts as a "lead-in" to the next chord.
            if chord_changes_next and pickup > 0 and ctx.rng.random() < pickup:
                next_root = root_for_bar(bar + 1)
                tick = bar_start + (ctx.steps_per_bar - 1) * (ctx.ppq // 4)
                jitter = ctx.rng.randint(-6, 6)
                # Pickup is a touch quieter than the main hits.
                vel = max(
                    1,
                    min(
                        127,
                        int(round(base_vel * intensity * evo_mult * ctx.tension * 0.75))
                        + jitter,
                    ),
                )
                dur = max(1, int(step_ticks * gate))
                pitch = maybe_octave_jump(next_root, octave_jump, ctx.rng)
                yield Note(
                    tick=tick, duration=dur, channel=inst.channel,
                    pitch=pitch, velocity=vel,
                )
