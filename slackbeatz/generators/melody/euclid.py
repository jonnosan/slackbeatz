"""``melody euclid`` — Arduino-style "riff": 4 scale-degree note slots
distributed across the bar via Euclidean rhythm, optionally tracking
the chord progression a ``chords`` gen plays in the same part.

Carries forward the Arduino prototype's ``Track Chords`` toggle as
on-by-default: bar-by-bar the root degree of the chord shifts the
riff, giving a coherent melodic line over chord changes.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    ChordProgression,
    euclid,
    step_duration,
    step_to_ticks,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


# The Arduino's default riff: 4 scale-degree positions, each with its own
# pulse count and offset. Carried over directly.
_RIFF_SLOTS: list[tuple[int, int, int]] = [
    # (scale_degree, pulses, offset)
    (0, 8, 0),
    (5, 4, 1),
    (7, 4, 2),
    (5, 4, 3),
]


@register_generator("melody", "euclid")
class MelodyEuclid(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = self.knob_int("octave", 0)
        intensity = self.knob_float("intensity", 1.0)
        gate = self.knob_float("gate", 0.6)
        base_vel = 90

        tonic, _ = parse_key(ctx.key)
        progression = ChordProgression("i-VI-ii-IV", bars_per_chord=4)
        step_ticks = step_duration(ctx.ppq)
        dur = max(1, int(step_ticks * gate))

        # Compose the bar's pattern: walk the slots from last to first
        # (the Arduino did this too, so later slots get overwritten by
        # earlier ones at conflicting steps).
        for bar in range(ctx.bars):
            chord_root_deg = progression.degree_at_bar(bar)
            # Each step gets at most one note. Walk slots from the highest
            # priority (slot 0) to lowest; later writes lose.
            bar_pattern: dict[int, int] = {}  # step → scale_degree
            for slot_idx in range(len(_RIFF_SLOTS) - 1, -1, -1):
                degree, pulses, offset = _RIFF_SLOTS[slot_idx]
                # Light random perturbation per bar so the riff breathes.
                pulses_eff = max(0, min(16, pulses + ctx.rng.choice([-1, 0, 0, 1])))
                pat = euclid(pulses_eff, 16, offset)
                for s, hit in enumerate(pat):
                    if hit:
                        bar_pattern[s] = (degree + chord_root_deg) % 7

            bar_start = bar * 4 * ctx.ppq
            for step, deg in sorted(bar_pattern.items()):
                pitch = scale_note(deg, tonic, "minor", 4 + octave_off)
                if not 0 <= pitch <= 127:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                jitter = ctx.rng.randint(-5, 5)
                vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
                yield Note(
                    tick=tick, duration=dur, channel=inst.channel,
                    pitch=pitch, velocity=vel,
                )
