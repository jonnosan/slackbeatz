"""``melody lofi`` — sparse Rhodes-EP phrases over jazz changes.

Picks 2-3 notes per chord from the pentatonic minor or dorian scale.
Notes are LONG (gate ≈ 0.85) and overlap slightly so they bleed into
each other — the smooth EP / Rhodes feel where one note rings while
the next comes in.

Per-chord phrase shape:

* Bar 1 of chord: one note on beat 1, sustained for ~3 beats.
* Bar 2-onwards: occasional 2nd or 3rd note as the phrase decays.

Scale defaults to ``minor_pentatonic`` — the safest choice over a
ii-V-I in a minor key. Override with ``scale=dorian`` for jazz colour
or ``scale=major_pentatonic`` for happier lofi.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    PROGRESSIONS,
    ChordProgression,
    apply_gate_jitter,
    apply_mistake,
    evolution_multiplier,
    melody_phrase_bump,
    pick_evolution_direction,
    should_mute_bar,
    transposed_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    mistakes_for,
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


# Pentatonic minor degrees — restricted to the five notes within one
# octave so the melody stays in C4-C5 range rather than wrapping up
# into Rhodes EP's tinkling top register. minor_pentatonic has 5
# notes (indices 0..4); index 5+ wraps with an octave bump.
_DEGREES = (0, 1, 2, 3, 4)


@register_generator("melody", "lofi")
class MelodyLofi(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="minor_pentatonic")
        mistakes = mistakes_for(self)

        # Jazz progression by default — same as bass lofi.
        prog_name = self.knobs.get("progression", "ii-V-I")
        if not isinstance(prog_name, str) or prog_name not in PROGRESSIONS:
            prog_name = "ii-V-I"
        bars_per_chord = self.knob_int("bars_per_chord", 4)
        prog = ChordProgression(prog_name, bars_per_chord=bars_per_chord)

        tonic, _ = parse_key(ctx.key)
        ticks_per_bar = ctx.ticks_per_bar

        last_deg = -1
        bar = 0
        while bar < ctx.bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += prog.bars_per_chord
                continue
            chord_root_deg = prog.degree_at_bar(bar)
            # 2-3 notes scattered across the chord's bars, weighted
            # toward the first half.
            n_notes = ctx.rng.choice([2, 2, 3])
            # Slot positions on a quarter-note grid within the chord.
            n_slots = prog.bars_per_chord * 4
            slots = sorted(
                ctx.rng.sample(range(n_slots), min(n_notes, n_slots))
            )
            evo_mult = evolution_multiplier(
                bar, ctx.bars, macro["evolution"], direction,
            )
            for slot in slots:
                # Pick a degree from the pentatonic-minor relative to
                # the current chord root (gives different colours per
                # chord without sounding "wrong" over jazz changes).
                candidates = [d for d in _DEGREES if d != last_deg] or list(_DEGREES)
                deg = ctx.rng.choice(candidates)
                last_deg = deg
                pitch = transposed_pitch(
                    scale_note(chord_root_deg + deg, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                if not 0 <= pitch <= 127:
                    continue
                tick = bar * ticks_per_bar + slot * ctx.ppq
                # Long sustain for the Rhodes-EP overlap feel.
                base_dur = max(1, int(2 * ctx.ppq * gate))
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                jitter = ctx.rng.randint(-4, 4)
                vel = max(
                    1,
                    min(
                        127,
                        int(round(base_vel * intensity * evo_mult * ctx.tension))
                        + jitter + melody_phrase_bump(bar, self),
                    ),
                )
                pitch, tick, vel = apply_mistake(pitch, tick, vel, mistakes, ctx.rng)
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord
