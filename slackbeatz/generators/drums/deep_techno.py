"""``drums deep_techno`` — coordinated deep-techno kit.

Quarter-note kick (low velocity), quarter-note closed hat, occasional
beat-4 clap, *no* fills (deep techno doesn't dramatise transitions —
groove stays locked). Build→drop transition adds an ``ohat`` pickup
on the last 16th of the build's final bar.
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


_KICK = (4, 0, 85)
_HAT = (4, 0, 70)


@register_generator("drums", "deep_techno")
class DrumsDeepTechno(Generator):
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
            hat_pat = euclid(drift_pulses(_HAT[0], drift, ctx.rng), 16, _HAT[1])
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction) * ctx.tension
            bar_start = bar * ctx.ticks_per_bar
            for step in range(ctx.steps_per_bar):
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                if kick_pat[step]:
                    yield from _emit(kit.drum_notes.get("kick"), kit.channel,
                                      tick, dur, _drum_params(_KICK[2]), step, ctx, evo_mult)
                if hat_pat[step]:
                    yield from _emit(kit.drum_notes.get("hat"), kit.channel,
                                      tick, dur, _drum_params(_HAT[2]), step, ctx, evo_mult)
            # Occasional clap on beat 4 (step 12).
            if ctx.rng.random() < 0.3:
                clap_note = kit.drum_notes.get("clap")
                if clap_note is not None:
                    tick = bar_start + step_to_ticks(12, ctx.ppq)
                    yield from _emit(clap_note, kit.channel, tick, dur,
                                      _drum_params(72), 12, ctx, evo_mult)

        # Pickup ohat into a drop, on the last 16th of the part.
        if ctx.next_role == "drop":
            ohat_note = kit.drum_notes.get("ohat")
            if ohat_note is not None:
                total = ctx.bars * ctx.ticks_per_bar
                tick = total - step_ticks
                yield Note(
                    tick=tick, duration=step_ticks,
                    channel=kit.channel, pitch=ohat_note, velocity=92,
                )


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
