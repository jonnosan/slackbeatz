"""``chords vaporwave`` — lush 9th voicings over the i-VII-VI-V descent.

The signature vaporwave chord move: minor-tonic → b7 major → b6 major
→ 5 minor, with each chord voiced as root + 3rd + 5th + 9th (the 2nd
scale degree played an octave up). Sustains the full 4 bars of each
chord at a high gate so they bleed into each other like a Rhodes
electric piano with the sustain pedal down.
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


# Add-9 voicing: root, 3rd, 5th, 9th (the 2nd, one octave higher).
# Expressed as scale-degree offsets from the chord root.
_ADD9 = (0, 2, 4, 8)


@register_generator("chords", "vaporwave")
class ChordsVaporwave(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = self.knob_int("octave", 0)
        intensity = self.knob_float("intensity", 1.0)
        gate = self.knob_float("gate", 0.96)
        base_vel = 70

        tonic, _ = parse_key(ctx.key)
        prog = ChordProgression("i-VII-VI-V", bars_per_chord=4)

        ticks_per_bar = 4 * ctx.ppq
        chord_ticks = prog.bars_per_chord * ticks_per_bar
        dur = max(1, int(chord_ticks * gate))

        bar = 0
        while bar < ctx.bars:
            chord_root = prog.degree_at_bar(bar)
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-3, 3)
            vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
            for off in _ADD9:
                pitch = scale_note(chord_root + off, tonic, "minor", 4 + octave_off)
                if not 0 <= pitch <= 127:
                    continue
                remaining = (ctx.bars - bar) * ticks_per_bar
                yield Note(
                    tick=tick, duration=min(dur, remaining - 1),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord
