"""``drums vaporwave`` — coordinated lazy kit.

Half-time kick (beats 1 & 3), snare on 2 & 4, quarter-note closed hat,
no fills. The vibe is "shopping mall in 1992" — drums sit underneath
the chords rather than driving the song.
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


_KICK  = (2, 0, 90)
_SNARE = (2, 4, 85)
_HAT   = (4, 0, 65)


@register_generator("drums", "vaporwave")
class DrumsVaporwave(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        kit = self.kit
        assert kit is not None

        intensity = self.knob_float("intensity", 1.0)
        humanize = self.knob_int("humanize", 0)
        drop_prob = self.knob_float("drop_prob", 0.0)
        accent = self.knob_int("accent", 0)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        step_ticks = step_duration(ctx.ppq)
        dur = max(1, step_ticks // 2)

        def _drum_params(base_vel: int) -> HitParams:
            return HitParams(
                base_vel=base_vel, intensity=intensity,
                vel_jitter=vel_jitter_for(self),
                humanize=humanize, drop_prob=drop_prob, accent=accent,
            )

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            drift = macro["density_drift"]
            kick_pat = euclid(drift_pulses(_KICK[0], drift, ctx.rng), 16, _KICK[1])
            snare_pat = euclid(drift_pulses(_SNARE[0], drift, ctx.rng), 16, _SNARE[1])
            hat_pat = euclid(drift_pulses(_HAT[0], drift, ctx.rng), 16, _HAT[1])
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction) * ctx.tension
            bar_start = bar * ctx.ticks_per_bar
            for step in range(ctx.steps_per_bar):
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                if kick_pat[step]:
                    yield from _emit(kit.drum_notes.get("kick"), kit.channel,
                                      tick, dur, _drum_params(_KICK[2]), step, ctx, evo_mult)
                if snare_pat[step]:
                    yield from _emit(kit.drum_notes.get("snare"), kit.channel,
                                      tick, dur, _drum_params(_SNARE[2]), step, ctx, evo_mult)
                if hat_pat[step]:
                    yield from _emit(kit.drum_notes.get("hat"), kit.channel,
                                      tick, dur, _drum_params(_HAT[2]), step, ctx, evo_mult)


def _emit(note, channel, tick, duration, params: HitParams, step: int, ctx,
          intensity_mult: float = 1.0):
    if note is None:
        return
    shaped = humanize_hit(params, ctx.rng, step, tick, intensity_mult=intensity_mult)
    if shaped is None:
        return
    vel, tick = shaped
    yield Note(
        tick=tick, duration=duration, channel=channel, pitch=note, velocity=vel,
    )
