"""``chords euclid`` — sustained pad voicings on a 4-chord progression.

Default progression (Arduino-derived): **i-VI-ii-IV** in the part's
minor key, one chord per 4 bars. Voicing is a simple triad (root + 3rd
+ 5th) of the chord-root scale degree, sustained for the chord duration.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    ChordProgression,
    evolution_multiplier,
    expression_ramp,
    is_build_part,
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


# Chord voicings as scale-degree offsets from the chord root: triad.
_TRIAD = (0, 2, 4)


@register_generator("chords", "euclid")
class ChordsEuclid(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        tonic, _ = parse_key(ctx.key)
        progression = ChordProgression("i-VI-ii-IV", bars_per_chord=4)

        ticks_per_bar = 4 * ctx.ppq
        chord_ticks = progression.bars_per_chord * ticks_per_bar
        dur = max(1, int(chord_ticks * gate))

        bars = ctx.bars
        bar = 0
        while bar < bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += progression.bars_per_chord
                continue
            chord_root = progression.degree_at_bar(bar)
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-4, 4)
            evo_mult = evolution_multiplier(bar, bars, macro["evolution"], direction)
            vel = max(1, min(127, int(round(base_vel * intensity * evo_mult)) + jitter))
            for deg_off in _TRIAD:
                pitch = scale_note(
                    chord_root + deg_off, tonic, "minor", 4 + octave_off
                )
                if not 0 <= pitch <= 127:
                    continue
                # Truncate chord duration if it would run past the part.
                remaining = (bars - bar) * ticks_per_bar
                yield Note(
                    tick=tick, duration=min(dur, remaining - 1),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += progression.bars_per_chord

        # Build → drop: swell the chord channel via CC 11 so the
        # transition has actual loudness motion, not just brightness.
        if is_build_part(ctx):
            yield from expression_ramp(ctx, inst.channel, start=80, end=127)
