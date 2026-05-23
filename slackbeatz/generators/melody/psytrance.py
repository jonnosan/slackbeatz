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
from slackbeatz.generators._shared import step_duration, step_to_ticks
from slackbeatz.generators.base import Generator
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

        octave_off = self.knob_int("octave", 0)
        intensity = self.knob_float("intensity", 1.0)
        gate = self.knob_float("gate", 0.5)
        base_vel = 88

        tonic, _ = parse_key(ctx.key)
        step_ticks = step_duration(ctx.ppq)
        dur = max(1, int(step_ticks * gate))

        # Each beat plays the 4-note motif on the four 16th steps of the beat.
        # The rotation index advances every 4 bars.
        for bar in range(ctx.bars):
            bar_start = bar * 4 * ctx.ppq
            rotation = bar // 4
            motif = [
                _MOTIF_DEGREES[(i + rotation) % len(_MOTIF_DEGREES)]
                for i in range(len(_MOTIF_DEGREES))
            ]
            for beat in range(4):
                for sub in range(4):
                    step = beat * 4 + sub
                    deg = motif[sub]
                    pitch = scale_note(deg, tonic, "phrygian", 4 + octave_off)
                    if not 0 <= pitch <= 127:
                        continue
                    tick = bar_start + step_to_ticks(step, ctx.ppq)
                    jitter = ctx.rng.randint(-5, 5)
                    vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
                    yield Note(
                        tick=tick, duration=dur,
                        channel=inst.channel, pitch=pitch, velocity=vel,
                    )
