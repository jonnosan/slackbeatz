"""``bass acid`` — TB-303 line.

The signature instrument of acid house. Single-note 16th-pulse bassline
with occasional octave-up "pings" and minor-3rd interjections,
continuous CC 74 (filter cutoff) modulation, CC 71 (resonance) climb,
and per-note pitch-bend wobble. The bass gen does *all* the heavy
lifting in an acid track — drums + 303 is the whole sound of Phuture's
*Acid Tracks*.

Knobs of interest:

* ``cutoff_lfo_cycles`` — number of CC 74 LFO cycles per part. Default
  2 (so the filter slowly opens and closes once or twice through a
  section).
* ``resonance`` — CC 71 ceiling. Default 100 (high for that screech).
* ``bend`` — pitchwheel wobble amount per note. Default 80 (≈ ±2 cents).
"""

from __future__ import annotations

import math
from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note, PitchBend
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


# Per-step probabilities driving the 303 line. Each 16th step is
# considered independently:
#   - first roll: should we play a note at all? (the rest = the rest;
#     303 patterns have plenty of empty steps).
#   - if yes, what pitch? (mostly root, sometimes octave-up, sometimes
#     minor third).
_PLAY_PROB = 0.65       # ~10/16 steps fire by default
_OCTAVE_UP_PROB = 0.20  # of the playing steps, ~20% jump an octave
_THIRD_PROB = 0.10      # ~10% land on the minor third


@register_generator("bass", "acid_303")
class BassAcid303(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        duck = duck_for(self)
        base_vel = base_vel_for(self)
        # Filter LFO cycles across the part. 2 = filter opens once and
        # closes once per part-instance, the classic "303 evolves over
        # the bar count" feel.
        lfo_cycles = self.knob_int("cycle", 2)
        resonance_ceiling = self.knob_int("resonance", 100)
        bend_amount = self.knob_int("bend", 80)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        gate_jitter = gate_jitter_for(self)

        tonic, _ = parse_key(ctx.key)
        # TB-303 sits high for a lead-bass — octave 2 is sub-low; octave
        # TB-303 lead-bass sits in A2 register (~110 Hz fundamental).
        # That's high enough for the squelchy filter character but
        # still in bass-frequency territory. Previous default of
        # octave 3 (= A3, 220 Hz) was lead-melody register and felt
        # detached from the rest of the mix. Users wanting the
        # higher 303 "ping" can set octave=3 explicitly on the gen.
        register_octave = 2 + octave_off
        root_raw = midi_note(tonic, register_octave)
        root_pitch = transposed_pitch(root_raw, ctx.transpose_semitones)
        third_pitch = transposed_pitch(root_raw + 3, ctx.transpose_semitones)
        oct_pitch = transposed_pitch(root_raw + 12, ctx.transpose_semitones)

        step_ticks = step_duration(ctx.ppq)
        ticks_per_bar = ctx.ticks_per_bar
        total_ticks = ctx.bars * ticks_per_bar
        dur = max(1, int(step_ticks * gate))

        # -------- CC modulation across the whole part ---------------
        # CC 74 cutoff + CC 71 resonance LFO. Step every quarter beat
        # for smooth filter motion.
        cc_step_ticks = ctx.ppq  # one CC event per quarter note
        n_cc = ctx.bars * 4
        cycle_ticks = max(1, total_ticks // max(1, lfo_cycles))
        phase = ctx.rng.random() * math.tau
        for i in range(n_cc):
            tick = i * cc_step_ticks
            if tick >= total_ticks:
                break
            theta = phase + math.tau * tick / cycle_ticks
            lfo = (math.sin(theta) + 1.0) / 2.0
            # Cutoff sweeps 30 → 110 over the LFO.
            cutoff = int(round(30 + 80 * lfo * intensity))
            yield CC(
                tick=tick, channel=inst.channel, controller=74,
                value=max(0, min(127, cutoff)),
            )
            # Resonance follows but with a slight phase offset so they
            # don't move identically.
            if resonance_ceiling > 0:
                res_lfo = (math.sin(theta + math.pi / 3) + 1.0) / 2.0
                resonance = int(round(40 + (resonance_ceiling - 40) * res_lfo))
                yield CC(
                    tick=tick, channel=inst.channel, controller=71,
                    value=max(0, min(127, resonance)),
                )

        # -------- Note pattern --------------------------------------
        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            bar_start = bar * ticks_per_bar
            for step in range(ctx.steps_per_bar):
                if ctx.rng.random() >= _PLAY_PROB:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                roll = ctx.rng.random()
                if roll < _OCTAVE_UP_PROB:
                    pitch = oct_pitch
                elif roll < _OCTAVE_UP_PROB + _THIRD_PROB:
                    pitch = third_pitch
                else:
                    pitch = root_pitch
                # Accent every fourth step (downbeat-of-each-beat) +20
                # vel — the unmistakable acid accent pattern.
                accent_boost = 15 if step % 4 == 0 else 0
                jitter = ctx.rng.randint(-4, 4)
                vel_base = int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter + accent_boost
                env = sidechain_envelope(tick - bar_start, ctx.ppq, duck=duck)
                vel = max(1, min(127, int(round(vel_base * env))))
                # Pitch wobble per note for that analogue character.
                if bend_amount > 0:
                    yield PitchBend(
                        tick=max(0, tick - 1), channel=inst.channel,
                        value=ctx.rng.randint(-bend_amount, bend_amount),
                    )
                note_dur = apply_gate_jitter(dur, gate_jitter, ctx.rng)
                yield Note(
                    tick=tick, duration=max(1, note_dur),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
                if bend_amount > 0:
                    yield PitchBend(tick=tick + note_dur, channel=inst.channel, value=0)
