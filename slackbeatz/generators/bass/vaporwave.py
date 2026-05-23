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
from slackbeatz.generators._shared import (
    ChordProgression,
    apply_gate_jitter,
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
    sidechain_envelope,
    transposed_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    duck_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


@register_generator("bass", "vaporwave")
class BassVaporwave(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        # Vaporwave is half-time — kick lands only on beats 1 & 3, so
        # the techno sidechain envelope is the wrong shape. Defaults
        # table sets duck=1.0 (off); opt in with `duck=0.7` for pulse.
        duck = duck_for(self)
        base_vel = base_vel_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        gate_jitter = gate_jitter_for(self)
        scale = scale_for(self, ctx, fallback="minor")

        tonic, _ = parse_key(ctx.key)
        prog = ChordProgression("i-VII-VI-V", bars_per_chord=4)

        ticks_per_bar = ctx.ticks_per_bar
        half_chord_ticks = 2 * ticks_per_bar  # half of a 4-bar chord
        dur = max(1, int(half_chord_ticks * gate))

        bar = 0
        while bar < ctx.bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += prog.bars_per_chord
                continue
            chord_root = prog.degree_at_bar(bar)
            # Root for the first half of the chord …
            root_pitch = transposed_pitch(
                scale_note(chord_root, tonic, scale, 2 + octave_off),
                ctx.transpose_semitones,
            )
            # … fifth (4 scale-degrees up in the scale) for the second
            # half. The 4th degree above the chord root usually lands
            # on the chord's fifth in a triadic harmony.
            fifth_pitch = transposed_pitch(
                scale_note(chord_root + 4, tonic, scale, 2 + octave_off),
                ctx.transpose_semitones,
            )
            for offset_bars, pitch in ((0, root_pitch), (2, fifth_pitch)):
                if bar + offset_bars >= ctx.bars:
                    break
                tick = (bar + offset_bars) * ticks_per_bar
                jitter = ctx.rng.randint(-3, 3)
                evo_mult = evolution_multiplier(
                    bar + offset_bars, ctx.bars, macro["evolution"], direction,
                )
                vel_base = int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter
                env = sidechain_envelope(tick % ticks_per_bar, ctx.ppq, duck=duck)
                vel = max(1, min(127, int(round(vel_base * env))))
                remaining = (ctx.bars - bar - offset_bars) * ticks_per_bar
                note_dur = apply_gate_jitter(min(dur, remaining - 1), gate_jitter, ctx.rng)
                yield Note(
                    tick=tick, duration=max(1, note_dur),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord
