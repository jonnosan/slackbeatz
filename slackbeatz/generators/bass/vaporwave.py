"""``bass vaporwave`` — sustained walking-bass over the descending
i-VII-VI-V minor progression.

Plays the chord root for the first half of each chord, then the chord
fifth for the second half — that "walking" motion behind smooth jazz
changes. Long ``gate`` so notes blur into each other like a fretless
electric bass played with a soft touch.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import ChordProgression
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


@register_generator("bass", "vaporwave")
class BassVaporwave(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = self.knob_int("octave", -1)
        intensity = self.knob_float("intensity", 1.0)
        gate = self.knob_float("gate", 0.9)
        base_vel = 75

        tonic, _ = parse_key(ctx.key)
        prog = ChordProgression("i-VII-VI-V", bars_per_chord=4)

        ticks_per_bar = 4 * ctx.ppq
        half_chord_ticks = 2 * ticks_per_bar  # half of a 4-bar chord
        dur = max(1, int(half_chord_ticks * gate))

        bar = 0
        while bar < ctx.bars:
            chord_root = prog.degree_at_bar(bar)
            # Root for the first half of the chord …
            root_pitch = scale_note(chord_root, tonic, "minor", 2 + octave_off)
            # … fifth (4 scale-degrees up in the natural minor) for the
            # second half. The 4th degree above the chord root usually
            # lands on the chord's fifth in a triadic harmony.
            fifth_pitch = scale_note(chord_root + 4, tonic, "minor", 2 + octave_off)
            for offset_bars, pitch in ((0, root_pitch), (2, fifth_pitch)):
                if bar + offset_bars >= ctx.bars:
                    break
                tick = (bar + offset_bars) * ticks_per_bar
                jitter = ctx.rng.randint(-3, 3)
                vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
                remaining = (ctx.bars - bar - offset_bars) * ticks_per_bar
                yield Note(
                    tick=tick, duration=min(dur, remaining - 1),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord
