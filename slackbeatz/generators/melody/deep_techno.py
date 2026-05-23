"""``melody deep_techno`` — sparse, modal (dorian), 1–2 notes per bar."""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    evolution_multiplier,
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
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


# Eligible scale degrees in dorian — leans on 3rd, 5th, 7th and 9th for
# Detroit-deep-techno modal flavour. Avoids the leading tone to stay
# sustained / unresolved.
_DEGREES = [2, 4, 6, 9]


@register_generator("melody", "deep_techno")
class MelodyDeepTechno(Generator):
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
        scale = scale_for(self, ctx, fallback="dorian")

        tonic, _ = parse_key(ctx.key)
        ticks_per_bar = 4 * ctx.ppq

        last_deg: int | None = None
        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            # 1 or 2 notes per bar, randomly placed on a quarter-note grid.
            n = 1 if ctx.rng.random() < 0.7 else 2
            beats = sorted(ctx.rng.sample(range(4), n))
            for beat in beats:
                # Pick a degree but avoid repeating the last one.
                candidates = [d for d in _DEGREES if d != last_deg] or _DEGREES
                deg = ctx.rng.choice(candidates)
                last_deg = deg
                pitch = transposed_pitch(
                    scale_note(deg, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                if not 0 <= pitch <= 127:
                    continue
                tick = bar * ticks_per_bar + beat * ctx.ppq
                base_dur = max(1, int(ctx.ppq * 2 * gate))  # half-note-ish
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                jitter = ctx.rng.randint(-4, 4)
                vel = max(1, min(127, int(round(base_vel * intensity * evo_mult)) + jitter))
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
