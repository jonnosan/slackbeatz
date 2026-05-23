"""``rhythm acid`` — tight 909-style kit voice.

Acid house drums sit between euclid (techno) and psytrance — busy
enough to drive the groove, restrained enough to leave space for the
303. Kick 4-on-the-floor, snare on 2 & 4, hats on 8th notes, clap on
beat 4.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    HitParams,
    drift_pulses,
    euclid,
    evolution_multiplier,
    humanize_hit,
    pick_evolution_direction,
    should_mute_bar,
    step_duration,
    step_to_ticks,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import macro_knobs, vel_jitter_for
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


_DEFAULTS: dict[str, tuple[int, int]] = {
    "kick":  (4, 0),
    "bd":    (4, 0),
    "snare": (2, 4),
    "sd":    (2, 4),
    "clap":  (1, 12),   # beat 4 only — acid sparse clap
    "hat":   (8, 0),
    "hh":    (8, 0),
    "hats":  (8, 0),
    "ohat":  (1, 14),
}

_DEFAULT_VEL: dict[str, int] = {
    "kick":  108, "bd":  108,
    "snare":  92, "sd":   92,
    "clap":   88,
    "hat":    75, "hh":   75, "hats": 75,
    "ohat":   80,
}


@register_generator("rhythm", "acid")
class RhythmAcid(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.note is not None
        name = self.handle.lower()
        pulses, offset = _DEFAULTS.get(name, (4, 0))
        base_vel = self.knob_int("base_vel", _DEFAULT_VEL.get(name, 90))
        macro = macro_knobs(self)
        params = HitParams(
            base_vel=base_vel,
            intensity=self.knob_float("intensity", 1.0),
            vel_jitter=vel_jitter_for(self),
            humanize=self.knob_int("humanize", 0),
            drop_prob=self.knob_float("drop_prob", 0.0),
            accent=self.knob_int("accent", 0),
        )
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        step_ticks = step_duration(ctx.ppq)
        dur = max(1, step_ticks // 2)

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            bar_pulses = drift_pulses(pulses, macro["density_drift"], ctx.rng)
            pattern = euclid(bar_pulses, 16, offset)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            bar_start = bar * 4 * ctx.ppq
            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                shaped = humanize_hit(params, ctx.rng, step, tick, intensity_mult=evo_mult)
                if shaped is None:
                    continue
                vel, tick = shaped
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=inst.note, velocity=vel,
                )
