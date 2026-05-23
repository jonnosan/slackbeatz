"""``melody vaporwave`` — sparse sax-like phrases.

One phrase per chord (so every 4 bars by default), each phrase is 2–3
notes from the dorian scale in the upper register. Notes are long and
overlap slightly into the next phrase — the smooth-jazz "noodle".
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


# Dorian degrees we draw from — colourful upper-extension notes.
# Tonic / 3rd / 5th / 6th / 7th / 9th = 0, 2, 4, 5, 6, 8.
_DEGREES = [0, 2, 4, 5, 6, 8]


@register_generator("melody", "vaporwave")
class MelodyVaporwave(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = self.knob_int("octave", 1)
        intensity = self.knob_float("intensity", 1.0)
        gate = self.knob_float("gate", 0.85)
        base_vel = 75

        tonic, _ = parse_key(ctx.key)
        prog = ChordProgression("i-VII-VI-V", bars_per_chord=4)

        ticks_per_bar = 4 * ctx.ppq
        ppq = ctx.ppq

        last_deg = -1
        bar = 0
        while bar < ctx.bars:
            chord_root_deg = prog.degree_at_bar(bar)
            # 2 or 3 notes per chord, placed on quarter-note boundaries
            # within the chord's bars. Lower density than ``melody
            # deep_techno`` to match the vaporwave aesthetic.
            n_notes = ctx.rng.choice([2, 2, 3])
            slots = sorted(ctx.rng.sample(range(prog.bars_per_chord * 4), n_notes))
            for slot in slots:
                # Pick a degree relative to the chord root, avoiding the
                # last one we played for a less mechanical melodic line.
                candidates = [d for d in _DEGREES if d != last_deg] or _DEGREES
                deg = ctx.rng.choice(candidates)
                last_deg = deg
                pitch = scale_note(
                    chord_root_deg + deg, tonic, "dorian", 4 + octave_off
                )
                if not 0 <= pitch <= 127:
                    continue
                # Note lands on a quarter-note grid relative to chord start.
                tick = bar * ticks_per_bar + slot * ppq
                # Long sustain — half a bar by default.
                dur = max(1, int(2 * ppq * gate))
                jitter = ctx.rng.randint(-4, 4)
                vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord
