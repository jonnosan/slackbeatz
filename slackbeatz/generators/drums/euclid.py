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
    euclid,
    fill_perturb,
    is_fill_bar,
    step_duration,
    step_to_ticks,
)
from slackbeatz.generators.base import Generator
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
        swing = self.knob_float("swing", 0.0)
        step_ticks = step_duration(ctx.ppq)
        swing_offset = int(step_ticks * swing * 0.5)
        dur = max(1, step_ticks // 2)

        # Big-fill flag — last bar of the part if heading into a drop.
        big_fill = ctx.next_role == "drop"

        for bar in range(ctx.bars):
            bar_start = bar * 4 * ctx.ppq
            is_fill = is_fill_bar(bar, group=4)
            is_last_bar = bar == ctx.bars - 1

            # Default patterns.
            kick_pat = euclid(_KICK[0], 16, _KICK[1])
            snare_pat = euclid(_SNARE[0], 16, _SNARE[1])
            clap_pat = euclid(_CLAP[0], 16, _CLAP[1])
            hat_pat = euclid(_HAT[0], 16, _HAT[1])
            ohat_pat = euclid(_OHAT[0], 16, _OHAT[1])

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
                                      tick, dur, _KICK[2], intensity, ctx)
                if snare_pat[step]:
                    yield from _emit(kit.drum_notes.get("snare"), kit.channel,
                                      tick, dur, _SNARE[2], intensity, ctx)
                if clap_pat[step]:
                    yield from _emit(kit.drum_notes.get("clap"), kit.channel,
                                      tick, dur, _CLAP[2], intensity, ctx)
                if hat_pat[step]:
                    yield from _emit(kit.drum_notes.get("hat"), kit.channel,
                                      tick, dur, _HAT[2], intensity, ctx)
                if ohat_pat[step]:
                    yield from _emit(kit.drum_notes.get("ohat"), kit.channel,
                                      tick, dur, _OHAT[2], intensity, ctx)


def _emit(
    note: int | None,
    channel: int,
    tick: int,
    duration: int,
    base_vel: int,
    intensity: float,
    ctx: PartContext,
):
    """Yield a humanised Note. Skips silently if the kit doesn't define the drum."""
    if note is None:
        return
    jitter = ctx.rng.randint(-8, 8)
    vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
    yield Note(
        tick=tick, duration=duration, channel=channel, pitch=note, velocity=vel
    )
