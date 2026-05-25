"""``melody sh101_arp`` — SH-101-style euclidean-clocked arpeggiator.

Replicates the classic 80s/90s acid-techno lead technique: feed a
Roland SH-101's external step-sequencer clock input from a drum
machine's accent / trigger output, put a euclidean pattern on the
drums, and the SH-101 cycles through its 3–8 note pitch sequence in
fixed order while the trigger pattern dictates WHEN each step fires.

Effect: a melodic pattern whose pitches are deterministic but whose
rhythm follows whatever pulse pattern is feeding the clock. Long gaps
between triggers → long held notes; short gaps → fast bursts. The
pitch sequence "rotates" against the trigger pattern when their
lengths don't align, so the line evolves over many bars without being
a literal loop.

Hardfloor's "Acperience", Plastikman's longer pieces, Drexciya
sequencer lines, lots of Phuture-era acid — all built on this trick.

Knobs:
* ``pitches`` — comma-separated scale degrees (default ``"0,3,7,5"``
  = root, min3, P5, P4 of the active scale). 3–6 entries works best;
  longer sequences take many bars to come around.
* ``pulses`` — euclidean K (number of triggers per cycle). Default 5.
* ``steps`` — euclidean N (cycle length in 16th steps). Default 16.
* ``gate`` — fraction of the gap-to-next-trigger the note holds.
  Default 0.85 (slight separation between notes).
* ``base_octave`` / ``octave`` — register offset around middle C
  (default register 4 → ~C4 root).
* ``scale`` — modal override (default minor_pentatonic for acid).
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    apply_mistake,
    euclid,
    evolution_multiplier,
    melody_phrase_bump,
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
    mistakes_for,
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


def _parse_pitch_sequence(raw: object) -> list[int]:
    """Coerce the ``pitches`` knob string into a list of scale degrees.

    Accepts ``"0,3,7,5"`` style comma-separated strings or a single
    integer. Strips whitespace; ignores empty entries; falls back to
    a sensible 4-note acid motif when input is bad.
    """
    fallback = [0, 3, 7, 5]
    if raw is None:
        return fallback
    if isinstance(raw, int):
        return [raw]
    try:
        parts = [p.strip() for p in str(raw).split(",") if p.strip()]
        if not parts:
            return fallback
        return [int(p) for p in parts]
    except (ValueError, TypeError):
        return fallback


@register_generator("melody", "sh101_arp")
class MelodySh101Arp(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        if intensity <= 0:
            return
        gate = gate_for(self)
        gate_jitter = gate_jitter_for(self)
        base_vel = base_vel_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="minor_pentatonic")
        mistakes = mistakes_for(self)

        # Pitch sequence + trigger pattern.
        degrees = _parse_pitch_sequence(self.knobs.get("pitches"))
        pulses = self.knob_int("pulses", 5)
        steps = self.knob_int("steps", 16)
        trigger = euclid(pulses, steps)
        trigger_positions = [i for i, on in enumerate(trigger) if on]
        if not trigger_positions:
            return  # 0-pulse pattern — silence

        tonic, _ = parse_key(ctx.key)
        step_ticks = step_duration(ctx.ppq)
        bars_per_cycle = max(1, (steps + 15) // 16)  # how many bars one cycle spans

        # The pitch pointer increments on every trigger and is GLOBAL
        # across the whole part — so when the trigger pattern length
        # doesn't divide evenly into the pitch sequence length, the
        # combined pattern "rotates" over many bars (the source of
        # the technique's evolving character).
        pitch_idx = 0

        # Walk the part bar-by-bar. Within each bar we map its 16
        # sixteenth-step positions onto positions in the trigger
        # pattern (which may be a different length than 16 — wraps).
        cycle_step_cursor = 0
        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                # Skipped bar still advances the cycle cursor so the
                # next bar's trigger placement stays in phase.
                cycle_step_cursor = (cycle_step_cursor + 16) % steps
                continue
            evo_mult = evolution_multiplier(
                bar, ctx.bars, macro["evolution"], direction,
            )
            bar_start = bar * ctx.ticks_per_bar
            for sub in range(16):  # one 16th per slot
                cycle_pos = (cycle_step_cursor + sub) % steps
                if not trigger[cycle_pos]:
                    continue
                # This is a trigger — play the current pitch + advance.
                # Note duration: extends to just before the next trigger
                # so gaps + gates BOTH come from the euclidean pattern
                # (long gap → long sustained note; short gap → short
                # burst). The classic SH-101-clocked-by-808 character.
                next_idx = trigger_positions.index(cycle_pos) + 1
                if next_idx >= len(trigger_positions):
                    # Wrap to the first trigger of the next cycle.
                    gap_steps = (steps - cycle_pos) + trigger_positions[0]
                else:
                    gap_steps = trigger_positions[next_idx] - cycle_pos
                base_dur = max(1, int(gap_steps * step_ticks * gate))

                deg = degrees[pitch_idx % len(degrees)]
                pitch_idx += 1
                pitch = transposed_pitch(
                    scale_note(deg, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                if not 0 <= pitch <= 127:
                    continue
                tick = bar_start + step_to_ticks(sub, ctx.ppq)
                jitter = ctx.rng.randint(-4, 4)
                vel = max(1, min(127, int(round(
                    base_vel * intensity * evo_mult * ctx.tension,
                )) + jitter + melody_phrase_bump(bar, self)))
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                pitch, tick, vel = apply_mistake(
                    pitch, tick, vel, mistakes, ctx.rng,
                )
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            cycle_step_cursor = (cycle_step_cursor + 16) % steps
