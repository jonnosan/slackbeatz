"""``bass psytrance`` — the gallop.

Per beat: rest on the downbeat, then root-root-root on the three
following 16ths. So a 16-step bar is::

    . R R R . R R R . R R R . R R R

Short ``gate`` (~0.3) so each note pumps off before the next, giving
the signature rolling psytrance bassline.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note, PitchBend
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
    sidechain_envelope,
    step_duration,
    step_to_ticks,
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
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note


@register_generator("bass", "psytrance")
class BassPsytrance(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        duck = duck_for(self)
        base_vel = base_vel_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        # `bend` is the max pitch-wobble per note in pitchwheel units
        # (±N around 0; 8192 = one semitone). Default 150 ≈ ±4 cents,
        # the right size for an "analogue oscillator drift" feel that
        # is subliminal in isolation but sells the squelch.
        bend_amount = self.knob_int("bend", 150)
        # Issue #15: phrygian b2 "burble". When this rolls, a note plays
        # one semitone above the root instead of the root — the
        # signature acid-flavoured ornament of psytrance bass.
        burble_prob = self.knob_float("burble_prob", 0.05)
        gate_jitter = gate_jitter_for(self)

        tonic, _ = parse_key(ctx.key)
        root_raw = midi_note(tonic, 2 + octave_off)
        root = transposed_pitch(root_raw, ctx.transpose_semitones)
        # b2 is one semitone above the root — the phrygian flavour note.
        b2 = transposed_pitch(root_raw + 1, ctx.transpose_semitones)

        step_ticks = step_duration(ctx.ppq)
        ticks_per_bar = ctx.ticks_per_bar
        dur = max(1, int(step_ticks * gate))

        # Gallop: pulses on every 16th *except* the first 16th of each beat.
        # In 16-step terms: steps 1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15.
        gallop_steps = [s for s in range(16) if s % 4 != 0]

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            bar_start = bar * ticks_per_bar
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            for step in gallop_steps:
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                jitter = ctx.rng.randint(-4, 4)
                vel_base = int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter
                env = sidechain_envelope(tick - bar_start, ctx.ppq, duck=duck)
                vel = max(1, min(127, int(round(vel_base * env))))
                # PitchBend immediately before each note for the analogue
                # wobble. Pitch reset at note-off so subsequent notes don't
                # inherit a bias from this one's bend.
                if bend_amount > 0:
                    bend = ctx.rng.randint(-bend_amount, bend_amount)
                    yield PitchBend(tick=max(0, tick - 1), channel=inst.channel, value=bend)
                # Phrygian b2 burble — occasionally substitute root → b2.
                pitch = b2 if (burble_prob > 0 and ctx.rng.random() < burble_prob) else root
                note_dur = apply_gate_jitter(dur, gate_jitter, ctx.rng)
                yield Note(
                    tick=tick, duration=max(1, note_dur),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
                if bend_amount > 0:
                    yield PitchBend(tick=tick + note_dur, channel=inst.channel, value=0)
