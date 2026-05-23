"""``chords vaporwave`` — lush 9th voicings over the i-VII-VI-V descent.

The signature vaporwave chord move: minor-tonic → b7 major → b6 major
→ 5 minor, with each chord voiced as root + 3rd + 5th + 9th (the 2nd
scale degree played an octave up). Sustains the full 4 bars of each
chord at a high gate so they bleed into each other like a Rhodes
electric piano with the sustain pedal down.

Emits CC 91 (reverb send) at the start of each chord to enforce a
deep-reverb tail (``reverb=N`` knob, default 100 of 127 — vaporwave
runs wet).
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note
from slackbeatz.generators._shared import (
    ChordProgression,
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    gate_for,
    macro_knobs,
)
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

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        reverb = self.knob_int("reverb", 100)  # CC 91 send level
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        tonic, _ = parse_key(ctx.key)
        prog = ChordProgression("i-VII-VI-V", bars_per_chord=4)

        ticks_per_bar = 4 * ctx.ppq
        chord_ticks = prog.bars_per_chord * ticks_per_bar
        dur = max(1, int(chord_ticks * gate))

        # One-shot reverb-send setup at tick 0 — stays for the whole part.
        if reverb > 0:
            yield CC(
                tick=0, channel=inst.channel, controller=91,
                value=max(0, min(127, reverb)),
            )

        bar = 0
        while bar < ctx.bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += prog.bars_per_chord
                continue
            chord_root = prog.degree_at_bar(bar)
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-3, 3)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            vel = max(1, min(127, int(round(base_vel * intensity * evo_mult)) + jitter))
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
