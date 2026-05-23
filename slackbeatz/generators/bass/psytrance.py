"""``bass psytrance`` — the gallop.

Per beat: rest on the downbeat, then root-root-root on the three
following 16ths. So a 16-step bar is::

    . R R R . R R R . R R R . R R R

Short ``gate`` (~0.3) so each note pumps off before the next, giving
the signature rolling psytrance bassline.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import step_duration, step_to_ticks
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note


@register_generator("bass", "psytrance")
class BassPsytrance(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = self.knob_int("octave", -1)
        intensity = self.knob_float("intensity", 1.0)
        gate = self.knob_float("gate", 0.3)
        base_vel = 105

        tonic, _ = parse_key(ctx.key)
        root = midi_note(tonic, 2 + octave_off)

        step_ticks = step_duration(ctx.ppq)
        dur = max(1, int(step_ticks * gate))

        # Gallop: pulses on every 16th *except* the first 16th of each beat.
        # In 16-step terms: steps 1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15.
        gallop_steps = [s for s in range(16) if s % 4 != 0]

        for bar in range(ctx.bars):
            bar_start = bar * 4 * ctx.ppq
            for step in gallop_steps:
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                jitter = ctx.rng.randint(-4, 4)
                vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=root, velocity=vel,
                )
