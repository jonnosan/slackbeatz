"""``chords deep_techno`` — minor-7th voicings on a 2-chord progression.

i and iv only, each lasting 8 bars. The minor 7th (degree 6 of the
chord-root scale) gives the jazzy / Detroit-deep-techno colour.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    ChordProgression,
    expression_ramp,
    is_build_part,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


# Min7 voicing offsets (root, m3, P5, m7) as scale-degree offsets.
_MIN7 = (0, 2, 4, 6)


@register_generator("chords", "deep_techno")
class ChordsDeepTechno(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = self.knob_int("octave", 0)
        intensity = self.knob_float("intensity", 1.0)
        gate = self.knob_float("gate", 0.98)
        base_vel = 70

        tonic, _ = parse_key(ctx.key)
        prog = ChordProgression("i-iv", bars_per_chord=8)

        ticks_per_bar = 4 * ctx.ppq
        chord_ticks = prog.bars_per_chord * ticks_per_bar
        dur = max(1, int(chord_ticks * gate))

        bar = 0
        while bar < ctx.bars:
            chord_root = prog.degree_at_bar(bar)
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-3, 3)
            vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
            for off in _MIN7:
                pitch = scale_note(chord_root + off, tonic, "minor", 4 + octave_off)
                if not 0 <= pitch <= 127:
                    continue
                remaining = (ctx.bars - bar) * ticks_per_bar
                yield Note(
                    tick=tick, duration=min(dur, remaining - 1),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord

        # Gentle expression swell on build → drop. Deep techno wants a
        # smaller dynamic range than euclid (75 → 110 instead of
        # 80 → 127) — it should feel restrained, not climactic.
        if is_build_part(ctx):
            yield from expression_ramp(ctx, inst.channel, start=75, end=110)
