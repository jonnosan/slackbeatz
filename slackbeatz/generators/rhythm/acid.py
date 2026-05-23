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
    drum_pattern_lookup,
    drum_vel_lookup,
    groove_offset,
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


_DEFAULTS: dict[str, tuple[int, int]] = {
    "kick":  (4, 0),     # 4-on-floor — the steady acid kick
    "bd":    (4, 0),
    # Acid drums DON'T do rock-style 2+4 snare. The clap (when present)
    # owns the backbeat on beat 4 only. Authentic Phuture / 808-State
    # tracks rarely double snare + clap on 2+4.
    "snare": (1, 12),    # beat 4 only — matches the clap
    "sd":    (1, 12),
    "clap":  (1, 12),    # beat 4 only — acid sparse clap
    # Hats sparser than techno — quarter-note closed hits is the
    # period-authentic pattern. The 303 line carries the busy 16th feel.
    "hat":   (4, 0),
    "hh":    (4, 0),
    "hats":  (4, 0),
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
        pulses, offset = drum_pattern_lookup(self.handle, _DEFAULTS)
        base_vel = self.knob_int("base_vel", drum_vel_lookup(self.handle, _DEFAULT_VEL, 90))
        macro = macro_knobs(self)
        groove = self.knobs.get("groove", "linear")
        if not isinstance(groove, str):
            groove = "linear"
        ghost = self.knob_float("ghost", 0.0)
        ghost_vel_ratio = self.knob_float("ghost_vel", 0.25)
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
        # Issue #12: optional secondary euclid layer (cross-rhythm). 0 = off.
        poly_pattern = euclid(polyrhythm, ctx.steps_per_bar, 0) if polyrhythm > 0 else None
        step_ticks = step_duration(ctx.ppq)
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
                tick = bar_start + step_to_ticks(step, ctx.ppq) + groove_offset(groove, step)
                shaped = humanize_hit(params, ctx.rng, step, tick, intensity_mult=evo_mult)
                if shaped is None:
                    continue
                vel, tick = shaped
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=inst.note, velocity=vel,
                )
            # Polyrhythm overlay (issue #12).
            if poly_pattern is not None:
                for ps, ph in enumerate(poly_pattern):
                    if not ph:
                        continue
                    pt = bar_start + step_to_ticks(ps, ctx.ppq)
                    pv = max(1, min(127, int(round(base_vel * params.intensity * evo_mult * 0.65))))
                    yield Note(tick=pt, duration=dur, channel=inst.channel, pitch=inst.note, velocity=pv)
