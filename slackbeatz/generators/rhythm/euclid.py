"""``rhythm euclid`` — single-drum Euclidean rhythm.

Branches on ``self.handle`` to pick a drum-conventional default
distribution: ``kick`` is 4-on-the-floor, ``snare`` / ``clap`` lands on
beats 2 & 4, ``hat`` / ``hats`` runs 8th notes with optional ``swing``.

Velocity is ``intensity * base_velocity ± rng-jitter``, so every bar has
small humanising variation but the underlying pattern is stable for the
seed.
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
    base_vel_for,
    macro_knobs,
    polyrhythm_for,
    vel_jitter_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


# Per-handle (pulses, offset) defaults on the 16-step bar.
# Beats are at steps 0/4/8/12; offset=4 ⇒ first hit on beat 2.
_DEFAULTS: dict[str, tuple[int, int]] = {
    "kick":  (4, 0),   # 4-on-the-floor
    "bd":    (4, 0),
    "snare": (2, 4),   # beats 2 & 4
    "sd":    (2, 4),
    "clap":  (2, 4),
    "hat":   (8, 0),   # 8th notes
    "hh":    (8, 0),
    "hats":  (8, 0),
    "ohat":  (1, 14),  # single open-hat on the last 16th of the bar
    "rim":   (5, 3),   # Arduino "extra riff" feel
}

_DEFAULT_VEL: dict[str, int] = {
    "kick":  110,
    "bd":    110,
    "snare": 100,
    "sd":    100,
    "clap":  100,
    "hat":   78,
    "hh":    78,
    "hats":  78,
    "ohat":  88,
    "rim":   95,
}


@register_generator("rhythm", "euclid")
class RhythmEuclid(Generator):
    """One drum voice, Euclidean distribution chosen by handle."""

    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.note is not None, (
            "rhythm gen needs a one-shot drum instrument"
        )
        name = self.handle.lower()
        pulses, offset = _DEFAULTS.get(name, (4, 0))
        # base_vel = handle-specific default unless overridden via the
        # `base_vel=N` knob.
        base_vel = self.knob_int("base_vel", _DEFAULT_VEL.get(name, 100))
        macro = macro_knobs(self)
        params = HitParams(
            base_vel=base_vel,
            intensity=self.knob_float("intensity", 1.0),
            vel_jitter=vel_jitter_for(self),
            humanize=self.knob_int("humanize", 0),
            drop_prob=self.knob_float("drop_prob", 0.0),
            accent=self.knob_int("accent", 0),
        )
        swing = self.knob_float("swing", 0.0)
        polyrhythm = polyrhythm_for(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        step_ticks = step_duration(ctx.ppq)
        swing_offset = int(step_ticks * swing * 0.5)
        dur = max(1, step_ticks // 2)
        # Issue #12: secondary euclid layer at lower velocity. If
        # polyrhythm=3, we overlay 3 evenly-spread pulses against the
        # main 4/16 (or whatever base) pattern — classic 3-against-4
        # cross-rhythm. Skipped silently when polyrhythm=0.
        if polyrhythm > 0:
            poly_pattern = euclid(polyrhythm, ctx.steps_per_bar, 0)
            poly_vel_scale = 0.65  # softer than primary
        else:
            poly_pattern = None

        # Issue #14: ctx.tension folds into the intensity_mult passed
        # to humanize_hit, so the part-level energy scalar applies to
        # every rhythm/drums hit through the existing velocity pipeline.
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
            # Polyrhythm overlay (issue #12): softer secondary layer.
            if poly_pattern is not None:
                for step, hit in enumerate(poly_pattern):
                    if not hit:
                        continue
                    tick = bar_start + step_to_ticks(step, ctx.ppq)
                    poly_vel = max(1, min(127, int(round(base_vel * params.intensity * evo_mult * poly_vel_scale))))
                    yield Note(
                        tick=tick, duration=dur,
                        channel=inst.channel, pitch=inst.note, velocity=poly_vel,
                    )
