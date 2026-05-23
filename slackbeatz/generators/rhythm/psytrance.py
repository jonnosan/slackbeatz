"""``rhythm psytrance`` — driving kick + classic offbeat hat.

The signature psytrance hat sits on the second 16th of every beat
(steps 2, 6, 10, 14 in a 16-step bar), producing the "tss-tss-tss-tss"
between the 4-on-the-floor kicks.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    HitParams,
    euclid,
    humanize_hit,
    step_duration,
    step_to_ticks,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


# pulses / offset on the 16-step grid.
# Hat 4/16 offset=2 puts hits at steps 2, 6, 10, 14 — the classic
# psytrance offbeat hat.
_DEFAULTS: dict[str, tuple[int, int]] = {
    "kick":  (4, 0),
    "bd":    (4, 0),
    "snare": (2, 4),
    "sd":    (2, 4),
    "clap":  (1, 12),
    "hat":   (4, 2),
    "hh":    (4, 2),
    "hats":  (4, 2),
    "ohat":  (0, 0),
    "rim":   (8, 2),  # rim on every offbeat 16th for shaker-like motion
}

_VELS: dict[str, int] = {
    "kick":  118, "bd": 118,
    "snare": 75,  "sd": 75,
    "clap":  85,
    "hat":   90, "hh": 90, "hats": 90,
    "rim":   78,
}


@register_generator("rhythm", "psytrance")
class RhythmPsytrance(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.note is not None
        name = self.handle.lower()
        pulses, offset = _DEFAULTS.get(name, (4, 0))
        base_vel = _VELS.get(name, 90)
        if pulses == 0 or base_vel == 0:
            return

        params = HitParams(
            base_vel=base_vel,
            intensity=self.knob_float("intensity", 1.0),
            vel_jitter=6,
            humanize=self.knob_int("humanize", 0),
            drop_prob=self.knob_float("drop_prob", 0.0),
            accent=self.knob_int("accent", 0),
        )
        pattern = euclid(pulses, 16, offset)
        step_ticks = step_duration(ctx.ppq)
        dur = max(1, step_ticks // 2)

        for bar in range(ctx.bars):
            bar_start = bar * 4 * ctx.ppq
            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                shaped = humanize_hit(params, ctx.rng, step, tick)
                if shaped is None:
                    continue
                vel, tick = shaped
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=inst.note, velocity=vel,
                )
