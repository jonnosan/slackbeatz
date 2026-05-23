"""``chords psytrance`` — sparse sus2 voicings, modal i-v progression.

Sus2 = root + 2nd + 5th. No 3rd, so the modal flavour (phrygian b2)
stays open rather than locking the listener into major-vs-minor. One
chord per 4 bars.
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


# Sus2 voicing: root, 2nd, 5th — scale-degree offsets from the chord root.
_SUS2 = (0, 1, 4)


@register_generator("chords", "psytrance")
class ChordsPsytrance(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = self.knob_int("octave", 0)
        intensity = self.knob_float("intensity", 1.0)
        gate = self.knob_float("gate", 0.9)
        base_vel = 75

        tonic, _ = parse_key(ctx.key)
        prog = ChordProgression("i-v", bars_per_chord=4)

        ticks_per_bar = 4 * ctx.ppq
        chord_ticks = prog.bars_per_chord * ticks_per_bar
        dur = max(1, int(chord_ticks * gate))

        bar = 0
        while bar < ctx.bars:
            chord_root = prog.degree_at_bar(bar)
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-3, 3)
            vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
            for off in _SUS2:
                # Use phrygian for the modal flavour even though resolver
                # parsed the key as minor.
                pitch = scale_note(
                    chord_root + off, tonic, "phrygian", 4 + octave_off
                )
                if not 0 <= pitch <= 127:
                    continue
                remaining = (ctx.bars - bar) * ticks_per_bar
                yield Note(
                    tick=tick, duration=min(dur, remaining - 1),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord
