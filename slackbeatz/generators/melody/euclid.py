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
    apply_mistake,
    melody_phrase_bump,
    ChordProgression,
    apply_gate_jitter,
    call_response_active,
    euclid,
    evolution_multiplier,
    maybe_passing_tone,
    pick_evolution_direction,
    should_mute_bar,
    step_duration,
    step_to_ticks,
    transposed_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    mistakes_for,
    base_octave_for,
    base_vel_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    pair_for,
    passing_tones_for,
    scale_for,
)
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

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        passing_tones = passing_tones_for(self)
        pair = pair_for(self)  # call-and-response partner handle, if any
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="minor")

        mistakes = mistakes_for(self)

        tonic, _ = parse_key(ctx.key)
        progression = ChordProgression("i-VI-ii-IV", bars_per_chord=4)
        step_ticks = step_duration(ctx.ppq)
        base_dur = max(1, int(step_ticks * gate))

        # Compose the bar's pattern: walk the slots from last to first
        # (the Arduino did this too, so later slots get overwritten by
        # earlier ones at conflicting steps).
        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            # Issue #13: skip bars where the paired gen is "speaking".
            if not call_response_active(self.handle, pair, bar):
                continue
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            chord_root_deg = progression.degree_at_bar(bar)
            # Each step gets at most one note. Walk slots from the highest
            # priority (slot 0) to lowest; later writes lose.
            bar_pattern: dict[int, int] = {}  # step → scale_degree
            for slot_idx in range(len(_RIFF_SLOTS) - 1, -1, -1):
                degree, pulses, offset = _RIFF_SLOTS[slot_idx]
                # Light random perturbation per bar so the riff breathes.
                pulses_eff = max(0, min(16, pulses + ctx.rng.choice([-1, 0, 0, 1])))
                pat = euclid(pulses_eff, ctx.steps_per_bar, offset)
                for s, hit in enumerate(pat):
                    if hit:
                        bar_pattern[s] = (degree + chord_root_deg) % 7

            bar_start = bar * ctx.ticks_per_bar
            for step, deg in sorted(bar_pattern.items()):
                pitch = transposed_pitch(
                    scale_note(deg, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                # Issue #4: optional chromatic neighbour substitution.
                pitch = maybe_passing_tone(pitch, passing_tones, ctx.rng)
                if not 0 <= pitch <= 127:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                jitter = ctx.rng.randint(-5, 5)
                # Issue #14: ctx.tension is the part-level energy scalar.
                vel = max(1, min(127, int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter))
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                yield Note(
                    tick=tick, duration=dur, channel=inst.channel,
                    pitch=pitch, velocity=vel,
                )
