"""``rhythm deep_techno`` — sparser, lower velocity, closed-hat quarters."""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import euclid, step_duration, step_to_ticks
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


# Quarter-note hat (4/16 offset 0) is the deep-techno signature; no
# offbeat hats. Snare is silent; claps only on beat 4 occasionally.
_DEFAULTS: dict[str, tuple[int, int]] = {
    "kick":  (4, 0),
    "bd":    (4, 0),
    "snare": (0, 0),
    "sd":    (0, 0),
    "clap":  (1, 12),  # only on beat 4
    "hat":   (4, 0),   # quarter notes only
    "hh":    (4, 0),
    "hats":  (4, 0),
    "ohat":  (0, 0),   # silent — deep techno doesn't shout
    "rim":   (5, 3),   # 5-pulse offset 3 — Arduino "rim shot" euclidean feel
}

_VELS: dict[str, int] = {
    "kick":  85, "bd": 85,
    "snare": 0,  "sd": 0,
    "clap":  72,
    "hat":   70, "hh": 70, "hats": 70,
    "ohat":  0,
    "rim":   78,
}


@register_generator("rhythm", "deep_techno")
class RhythmDeepTechno(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.note is not None
        name = self.handle.lower()
        pulses, offset = _DEFAULTS.get(name, (4, 0))
        base_vel = _VELS.get(name, 70)
        if pulses == 0 or base_vel == 0:
            return

        intensity = self.knob_float("intensity", 1.0)
        pattern = euclid(pulses, 16, offset)
        step_ticks = step_duration(ctx.ppq)
        dur = max(1, step_ticks // 2)

        for bar in range(ctx.bars):
            bar_start = bar * 4 * ctx.ppq
            # Claps only fire ~30% of bars to keep it sparse.
            if name == "clap" and ctx.rng.random() > 0.3:
                continue
            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                jitter = ctx.rng.randint(-5, 5)
                vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=inst.note, velocity=vel,
                )
