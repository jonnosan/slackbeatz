"""``drums euclid`` — coordinated multi-drum kit with 4-bar fills.

One algorithm emits kick + snare + hat (closed) + clap + open-hat
events, using the kit's drum-name map for MIDI notes. On the last bar
of each 4-bar group, runs a **fill** that perturbs pulse counts upward
and swaps snare/hat roles — the cheap-but-effective fill carried
forward from the Arduino prototype. Fill intensity ramps up further at
``build → drop`` role transitions.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    HitParams,
    drift_pulses,
    euclid,
    evolution_multiplier,
    fill_perturb,
    humanize_hit,
    is_fill_bar,
    pick_evolution_direction,
    should_mute_bar,
    step_duration,
    step_to_ticks,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import macro_knobs, vel_jitter_for
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


# (pulses, offset, base velocity) per drum role.
_KICK   = (4, 0, 110)
_SNARE  = (2, 4, 100)
_CLAP   = (2, 4,  95)
_HAT    = (8, 0,  78)
_OHAT   = (1, 14, 88)


@register_generator("drums", "euclid")
class DrumsEuclid(Generator):
    """Full Euclidean kit. Looks up notes from ``self.kit.drum_notes``."""

    def generate(self, ctx: PartContext) -> Iterator[Event]:
        kit = self.kit
        assert kit is not None, "drums gen needs a kit"

        intensity = self.knob_float("intensity", 1.0)
        humanize = self.knob_int("humanize", 0)
        drop_prob = self.knob_float("drop_prob", 0.0)
        accent = self.knob_int("accent", 0)
        swing = self.knob_float("swing", 0.0)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        step_ticks = step_duration(ctx.ppq)
        swing_offset = int(step_ticks * swing * 0.5)
        dur = max(1, step_ticks // 2)

        def _drum_params(base_vel: int) -> HitParams:
            return HitParams(
                base_vel=base_vel, intensity=intensity,
                vel_jitter=vel_jitter_for(self),
                humanize=humanize, drop_prob=drop_prob, accent=accent,
            )

        # Big-fill flag — last bar of the part if heading into a drop.
        big_fill = ctx.next_role == "drop"

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            bar_start = bar * 4 * ctx.ppq
            is_fill = is_fill_bar(bar, group=4)
            is_last_bar = bar == ctx.bars - 1

            # Default patterns with optional density drift applied
            # independently to each voice.
            drift = macro["density_drift"]
            kick_pat = euclid(drift_pulses(_KICK[0], drift, ctx.rng), 16, _KICK[1])
            snare_pat = euclid(drift_pulses(_SNARE[0], drift, ctx.rng), 16, _SNARE[1])
            clap_pat = euclid(drift_pulses(_CLAP[0], drift, ctx.rng), 16, _CLAP[1])
            hat_pat = euclid(drift_pulses(_HAT[0], drift, ctx.rng), 16, _HAT[1])
            ohat_pat = euclid(drift_pulses(_OHAT[0], drift, ctx.rng), 16, _OHAT[1])

            # 4-bar fill: re-roll snare + hat upward, swap them, add open hat.
            if is_fill:
                snare_pulses = fill_perturb(_SNARE[0], ctx.rng, bump=3)
                hat_pulses = fill_perturb(_HAT[0], ctx.rng, bump=4)
                snare_pat = euclid(hat_pulses, 16, 2)   # swap roles
                hat_pat = euclid(snare_pulses, 16, 4)
                ohat_pat = euclid(2, 16, 12)             # ohat on the back half

            # Big fill into a drop: pile on snare + open hat all over.
            if is_last_bar and big_fill:
                snare_pat = euclid(fill_perturb(8, ctx.rng, bump=4), 16, 2)
                ohat_pat = euclid(3, 16, 10)

            for step in range(16):
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                if step % 2 == 1:
                    tick += swing_offset

                if kick_pat[step]:
                    yield from _emit(kit.drum_notes.get("kick"), kit.channel,
                                      tick, dur, _drum_params(_KICK[2]), step, ctx, evo_mult)
                if snare_pat[step]:
                    yield from _emit(kit.drum_notes.get("snare"), kit.channel,
                                      tick, dur, _drum_params(_SNARE[2]), step, ctx, evo_mult)
                if clap_pat[step]:
                    yield from _emit(kit.drum_notes.get("clap"), kit.channel,
                                      tick, dur, _drum_params(_CLAP[2]), step, ctx, evo_mult)
                if hat_pat[step]:
                    yield from _emit(kit.drum_notes.get("hat"), kit.channel,
                                      tick, dur, _drum_params(_HAT[2]), step, ctx, evo_mult)
                if ohat_pat[step]:
                    yield from _emit(kit.drum_notes.get("ohat"), kit.channel,
                                      tick, dur, _drum_params(_OHAT[2]), step, ctx, evo_mult)


def _emit(
    note: int | None,
    channel: int,
    tick: int,
    duration: int,
    params: HitParams,
    step: int,
    ctx: PartContext,
    intensity_mult: float = 1.0,
):
    """Yield a humanised Note. Skips silently if the kit doesn't define
    the drum or the drop_prob roll dropped it."""
    if note is None:
        return
    shaped = humanize_hit(params, ctx.rng, step, tick, intensity_mult=intensity_mult)
    if shaped is None:
        return
    vel, tick = shaped
    yield Note(
        tick=tick, duration=duration, channel=channel, pitch=note, velocity=vel
    )
