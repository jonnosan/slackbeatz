"""``melody psytrance`` — phrygian 16th-note arpeggios that evolve.

Picks a 4-note motif from the phrygian scale (root, b2, b3, 5), plays
it on each beat for 4 bars, then rotates the starting degree by 1 for
the next 4 bars. The 16ths-per-beat repetition is the hypnotic
fingerprint; the slow rotation keeps it from being a literal one-bar
loop.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    MotifMemory,
    apply_gate_jitter,
    evolution_multiplier,
    maybe_octave_jump,
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
    motif_memory_for,
    octave_jump_for,
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


# Scale degrees of phrygian we feature: root, b2, b3, P5.
_MOTIF_DEGREES = [0, 1, 2, 4]


@register_generator("melody", "psytrance")
class MelodyPsytrance(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        octave_jump = octave_jump_for(self)
        # motif_memory: with N>0, the gen reuses recent degrees with a
        # probability proportional to N — making the motif repetition
        # more pronounced. Default 0 = the original deterministic motif.
        memory = MotifMemory(motif_memory_for(self))
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="phrygian")

        tonic, _ = parse_key(ctx.key)
        step_ticks = step_duration(ctx.ppq)
        base_dur = max(1, int(step_ticks * gate))

        # Each beat plays the 4-note motif on the four 16th steps of the beat.
        # The rotation index advances every 4 bars.
        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            bar_start = bar * 4 * ctx.ppq
            rotation = bar // 4
            motif = [
                _MOTIF_DEGREES[(i + rotation) % len(_MOTIF_DEGREES)]
                for i in range(len(_MOTIF_DEGREES))
            ]
            for beat in range(4):
                for sub in range(4):
                    step = beat * 4 + sub
                    # motif_memory: when active, may substitute a recently-
                    # played degree for the deterministic motif slot.
                    deg = memory.pick_next(ctx.rng, lambda r, fallback=motif[sub]: fallback)
                    pitch = transposed_pitch(
                        scale_note(deg, tonic, scale, 4 + octave_off),
                        ctx.transpose_semitones,
                    )
                    pitch = maybe_octave_jump(pitch, octave_jump, ctx.rng)
                    if not 0 <= pitch <= 127:
                        continue
                    tick = bar_start + step_to_ticks(step, ctx.ppq)
                    jitter = ctx.rng.randint(-5, 5)
                    vel = max(1, min(127, int(round(base_vel * intensity * evo_mult)) + jitter))
                    dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                    yield Note(
                        tick=tick, duration=dur,
                        channel=inst.channel, pitch=pitch, velocity=vel,
                    )
