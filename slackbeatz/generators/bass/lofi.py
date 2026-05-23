"""``bass lofi`` — warm walking jazz bass.

Plays the chord root with occasional walks to the 5th, sustained
through each chord. Default progression is ii-V-I (jazz cadence) over
4 bars each. Long gate keeps the bass sustained — picture a fingered
upright bass or a Rhodes EP playing bass.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    PROGRESSIONS,
    ChordProgression,
    apply_gate_jitter,
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
    sidechain_envelope,
    transposed_pitch,
    walking_step_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    duck_for,
    fifth_prob_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    pickup_for,
    scale_for,
    walking_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


@register_generator("bass", "lofi")
class BassLofi(Generator):
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
        # Default progression for lofi: jazz ii-V-I, 4 bars each. User
        # can override via progression= knob.
        prog_name = self.knobs.get("progression", "ii-V-I")
        if not isinstance(prog_name, str) or prog_name not in PROGRESSIONS:
            prog_name = "ii-V-I"
        bars_per_chord = self.knob_int("bars_per_chord", 4)
        prog = ChordProgression(prog_name, bars_per_chord=bars_per_chord)
        fifth_prob = fifth_prob_for(self)
        walking = walking_for(self)
        pickup = pickup_for(self)

        tonic, _ = parse_key(ctx.key)
        base_octave = 2 + octave_off
        ticks_per_bar = ctx.ticks_per_bar
        cell_ticks = ticks_per_bar  # one bass note per bar
        dur = max(1, int(cell_ticks * gate))

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            chord_deg = prog.degree_at_bar(bar)
            chord_root_pitch = transposed_pitch(
                scale_note(chord_deg, tonic, scale, base_octave),
                ctx.transpose_semitones,
            )
            chord_fifth_pitch = transposed_pitch(
                scale_note(chord_deg + 4, tonic, scale, base_octave),
                ctx.transpose_semitones,
            )
            # Pick root or fifth this bar.
            if fifth_prob > 0 and ctx.rng.random() < fifth_prob:
                pitch = chord_fifth_pitch
            else:
                pitch = chord_root_pitch

            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-3, 3)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            vel_base = (
                int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter
            )
            env = sidechain_envelope(0, ctx.ppq, duck=duck)
            vel = max(1, min(127, int(round(vel_base * env))))

            remaining = (ctx.bars - bar) * ticks_per_bar
            note_dur = apply_gate_jitter(min(dur, remaining - 1), gate_jitter, ctx.rng)
            yield Note(
                tick=tick, duration=max(1, note_dur),
                channel=inst.channel, pitch=pitch, velocity=vel,
            )

            # Walking step into the next chord (if the chord is about
            # to change). Lands on the last 8th of the bar.
            chord_changes_next = (
                bar + 1 < ctx.bars
                and prog.degree_at_bar(bar) != prog.degree_at_bar(bar + 1)
            )
            if walking > 0 and chord_changes_next and ctx.rng.random() < walking:
                next_root = transposed_pitch(
                    scale_note(prog.degree_at_bar(bar + 1), tonic, scale, base_octave),
                    ctx.transpose_semitones,
                )
                step_pitch = walking_step_pitch(pitch, next_root)
                tick_walk = tick + int(cell_ticks * 0.75)
                walk_dur = max(1, int(cell_ticks * 0.25 * gate))
                yield Note(
                    tick=tick_walk, duration=walk_dur,
                    channel=inst.channel, pitch=step_pitch,
                    velocity=max(1, vel - 6),
                )
            # Pickup anticipation before chord change.
            if pickup > 0 and chord_changes_next and ctx.rng.random() < pickup:
                next_root = transposed_pitch(
                    scale_note(prog.degree_at_bar(bar + 1), tonic, scale, base_octave),
                    ctx.transpose_semitones,
                )
                tick_pickup = tick + int(cell_ticks * 0.875)
                pickup_dur = max(1, int(cell_ticks * 0.125 * gate))
                yield Note(
                    tick=tick_pickup, duration=pickup_dur,
                    channel=inst.channel, pitch=next_root,
                    velocity=max(1, vel - 10),
                )
