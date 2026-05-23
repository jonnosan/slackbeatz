"""``rhythm vaporwave`` — lazy, half-time, low-velocity.

The signature is the *lack* of drive: kick on beats 1 and 3 only (so
``2/16 offset=0``), snare on 2 & 4 like every backbeat, closed hat on
the quarters (not 8ths — too busy for the genre). Velocities sit ~20%
lower than ``euclid`` so the drums sit *under* the chords rather than
in front.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import euclid, step_duration, step_to_ticks
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


# (pulses, offset) on the 16-step bar.
_DEFAULTS: dict[str, tuple[int, int]] = {
    "kick":  (2, 0),    # beats 1 & 3 — half-time feel
    "bd":    (2, 0),
    "snare": (2, 4),    # beats 2 & 4 backbeat (classic)
    "sd":    (2, 4),
    "clap":  (1, 12),   # beat 4 only — soft accent
    "hat":   (4, 0),    # quarter-note closed hat, no offbeats
    "hh":    (4, 0),
    "hats":  (4, 0),
    "ohat":  (0, 0),    # silent — vaporwave doesn't open the hat
    "rim":   (4, 2),    # jazz-comp rim on the offbeats if present
}

_VELS: dict[str, int] = {
    "kick":  90,  "bd": 90,
    "snare": 85,  "sd": 85,
    "clap":  75,
    "hat":   65,  "hh": 65, "hats": 65,
    "rim":   70,
}


@register_generator("rhythm", "vaporwave")
class RhythmVaporwave(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.note is not None
        name = self.handle.lower()
        pulses, offset = _DEFAULTS.get(name, (2, 0))
        base_vel = _VELS.get(name, 70)
        if pulses == 0 or base_vel == 0:
            return

        intensity = self.knob_float("intensity", 1.0)
        pattern = euclid(pulses, 16, offset)
        step_ticks = step_duration(ctx.ppq)
        dur = max(1, step_ticks // 2)

        for bar in range(ctx.bars):
            bar_start = bar * 4 * ctx.ppq
            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                # Less velocity jitter — vaporwave wants smoother dynamics.
                jitter = ctx.rng.randint(-4, 4)
                vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=inst.note, velocity=vel,
                )
