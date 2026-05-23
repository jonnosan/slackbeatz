"""``bass euclid`` — rolling root-note 8ths in the part's key.

Pulse count = 8 by default (8th notes through the bar), with a light
skip on beat 1 of every other bar so it feels less mechanical. Notes
are the root of the part's key, transposed by ``octave`` (default ``-1``
when the user writes ``gen bass bass euclid octave=-1``).
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    euclid,
    sidechain_envelope,
    step_duration,
    step_to_ticks,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note


@register_generator("bass", "euclid")
class BassEuclid(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched, "bass needs a pitched instrument"

        octave_off = self.knob_int("octave", -1)
        intensity = self.knob_float("intensity", 1.0)
        gate = self.knob_float("gate", 0.85)
        # Sidechain depth: 0.0 = silent on kick, 1.0 = no ducking. The
        # 0.55 default produces an audible pumping feel without
        # swallowing the bass entirely.
        duck = self.knob_float("duck", 0.55)
        base_vel = 95

        tonic, _scale = parse_key(ctx.key)
        # Bass register: octave 2 by default (E2 ~ 40). octave_off shifts.
        root = midi_note(tonic, 2 + octave_off)

        pulses = 8
        pattern = euclid(pulses, 16, 0)

        step_ticks = step_duration(ctx.ppq)
        ticks_per_bar = 4 * ctx.ppq
        dur = max(1, int(step_ticks * 2 * gate))  # 8th note long * gate

        for bar in range(ctx.bars):
            bar_start = bar * ticks_per_bar
            # Skip beat 1 on every other bar (structural "drop" on top of
            # the sidechain pumping that follows).
            duck_beat1 = bar % 2 == 1
            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                if duck_beat1 and step == 0:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                jitter = ctx.rng.randint(-6, 6)
                vel_base = int(round(base_vel * intensity)) + jitter
                # Sidechain pumping: ducks on each beat downbeat (where
                # the kick lands), recovers by mid-beat.
                env = sidechain_envelope(tick - bar_start, ctx.ppq, duck=duck)
                vel = max(1, min(127, int(round(vel_base * env))))
                yield Note(
                    tick=tick, duration=dur, channel=inst.channel,
                    pitch=root, velocity=vel,
                )
