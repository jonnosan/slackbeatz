"""``bass drum_and_bass`` — deep sub-bass with octave drops.

Long sustained root notes in the lowest playable register, occasional
drops to the lower octave for the classic "wobble" feel.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    evolution_multiplier,
    maybe_octave_jump,
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
    octave_jump_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note


@register_generator("bass", "reese")
class BassReese(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        duck = duck_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        octave_jump = octave_jump_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        tonic, _ = parse_key(ctx.key)
        # Sub-bass register. Use the standard `2 + octave_off` formula
        # every other bass generator uses; with the style's default
        # `octave_off=-1` that places the root at A1 (MIDI 33 ≈ 55 Hz)
        # — the fundamental of a classic DnB Reese. Earlier versions
        # used `1 + octave_off` which combined with the (then) default
        # `octave_off=-2` to drop the bass to A-1 (≈14 Hz), subsonic
        # and inaudible on every playback system.
        root = transposed_pitch(
            midi_note(tonic, 2 + octave_off), ctx.transpose_semitones
        )

        ticks_per_bar = ctx.ticks_per_bar
        # One sub hit per BAR (was every 2 bars — too sparse for tracks
        # that only run 2-4 bars in a preview). Each note holds for most
        # of the bar (gate=0.95 default for DnB) so it still feels like
        # a sustained sub, but every bar re-articulates so short songs
        # actually have audible bass.
        cell_ticks = ticks_per_bar
        dur = max(1, int(cell_ticks * gate))
        # Octave below the main sub — for the "wobble" octave drop.
        root_low = transposed_pitch(
            midi_note(tonic, 1 + octave_off), ctx.transpose_semitones,
        )

        bar = 0
        while bar < ctx.bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += 1
                continue
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-3, 3)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            vel_base = int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter
            env = sidechain_envelope(0, ctx.ppq, duck=duck)
            vel = max(1, min(127, int(round(vel_base * env))))
            remaining = (ctx.bars - bar) * ticks_per_bar
            note_dur = apply_gate_jitter(min(dur, remaining - 1), gate_jitter, ctx.rng)
            # Every odd-numbered bar (bar 1, 3, 5, ...) splits the sub
            # into root for the first half + octave-down "drop" for the
            # second half — the classic DnB sub-wobble gesture.
            if bar % 2 == 1:
                half_dur = max(1, note_dur // 2)
                pitch_a = maybe_octave_jump(root, octave_jump, ctx.rng)
                yield Note(
                    tick=tick, duration=half_dur,
                    channel=inst.channel, pitch=pitch_a, velocity=vel,
                )
                yield Note(
                    tick=tick + half_dur, duration=half_dur,
                    channel=inst.channel, pitch=root_low,
                    velocity=max(1, vel - 8),  # slight dip for the drop
                )
            else:
                pitch = maybe_octave_jump(root, octave_jump, ctx.rng)
                yield Note(
                    tick=tick, duration=max(1, note_dur),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += 1
