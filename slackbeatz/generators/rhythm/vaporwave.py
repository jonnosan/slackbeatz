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


# (pulses, offset) on the 16-step bar.
_DEFAULTS: dict[str, tuple[int, int]] = {
    "kick":  (2, 0),    # beats 1 & 3 — half-time feel
    "bd":    (2, 0),
    # Halftime snare lands on BEAT 3 only (step 8) — doubles with the
    # kick to give vaporwave its "lazy ballad" feel. The previous
    # default of 2+4 backbeat made it sound like rock at slow tempo,
    # not chillwave.
    "snare": (1, 8),
    "sd":    (1, 8),
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
        pulses, offset = drum_pattern_lookup(self.handle, _DEFAULTS)
        base_vel = drum_vel_lookup(self.handle, _VELS, 70)
        if pulses == 0 or base_vel == 0:
            return

        base_vel = self.knob_int("base_vel", base_vel)
        macro = macro_knobs(self)
        groove = self.knobs.get("groove", "linear")
        if not isinstance(groove, str):
            groove = "linear"
        ghost = self.knob_float("ghost", 0.0)
        ghost_vel_ratio = self.knob_float("ghost_vel", 0.25)
        params = HitParams(
            base_vel=base_vel,
            intensity=self.knob_float("intensity", 1.0),
            vel_jitter=vel_jitter_for(self),  # vaporwave defaults to 4 → smoother dynamics
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
