"""``rhythm garage`` — UK 2-step pattern (130 bpm).

The defining feature: snare/clap on beat 3 (step 8), NOT on beats 2
and 4. Kick on beat 1 only. Hat: shuffled 16ths with skips.
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
from slackbeatz.generators.defaults import (
    macro_knobs,
    polyrhythm_for,
    vel_jitter_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


# 2-step DNA: kick on 1 only, snare/clap on beat 3 (step 8).
_DEFAULTS: dict[str, tuple[int, int]] = {
    "kick":  (1, 0),     # beat 1 only
    "bd":    (1, 0),
    "snare": (1, 8),     # beat 3 only — the 2-step signature
    "sd":    (1, 8),
    "clap":  (1, 8),
    "hat":   (10, 0),    # busy shuffled hats
    "hh":    (10, 0),
    "hats":  (10, 0),
    "ohat":  (1, 14),
    "rim":   (3, 6),
}

_DEFAULT_VEL: dict[str, int] = {
    "kick":  112, "bd": 112,
    "snare": 95,  "sd": 95,
    "clap":  98,
    "hat":   75, "hh": 75, "hats": 75,
    "ohat":  80,
    "rim":   82,
}


@register_generator("rhythm", "garage")
class RhythmGarage(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.note is not None
        name = self.handle.lower()
        pulses, offset = _DEFAULTS.get(name, (2, 0))
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
        polyrhythm = polyrhythm_for(self)
        poly_pattern = euclid(polyrhythm, ctx.steps_per_bar, 0) if polyrhythm > 0 else None
        # Garage hats have a "skip" shuffle — push every 4th hit slightly later.
        step_ticks = step_duration(ctx.ppq)
        swing = self.knob_float("swing", 0.12)  # default shuffle
        swing_offset = int(step_ticks * swing * 0.5)
        dur = max(1, step_ticks // 2)

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            bar_pulses = drift_pulses(pulses, macro["density_drift"], ctx.rng)
            pattern = euclid(bar_pulses, ctx.steps_per_bar, offset)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction) * ctx.tension
            bar_start = bar * ctx.ticks_per_bar
            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                if step % 2 == 1:
                    tick += swing_offset
                shaped = humanize_hit(params, ctx.rng, step, tick, intensity_mult=evo_mult)
                if shaped is None:
                    continue
                vel, tick = shaped
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=inst.note, velocity=vel,
                )
            if poly_pattern is not None:
                for ps, ph in enumerate(poly_pattern):
                    if not ph:
                        continue
                    pt = bar_start + step_to_ticks(ps, ctx.ppq)
                    pv = max(1, min(127, int(round(base_vel * params.intensity * evo_mult * 0.65))))
                    yield Note(tick=pt, duration=dur, channel=inst.channel, pitch=inst.note, velocity=pv)
