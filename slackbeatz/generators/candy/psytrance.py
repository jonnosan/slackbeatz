"""``candy psytrance`` — long acidic filter sweeps into drops.

Ramps CC 74 from low to high over up to 8 bars approaching a part with
``next_role == "drop"`` (or while inside a ``build`` part), then snaps
back to low at the drop's downbeat (emitted as the final tick).
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import CC, Event
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


_BUILD_ROLES = {"build", "buildup"}


@register_generator("candy", "psytrance")
class CandyPsytrance(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        if inst is None:
            return
        is_build = ctx.role in _BUILD_ROLES or ctx.next_role == "drop"
        if not is_build:
            return

        intensity = self.knob_float("intensity", 1.0)
        density = self.knob_float("density", 0.7)
        cc_num = self.knob_int("cc", 74)

        ticks_per_bar = 4 * ctx.ppq
        total_ticks = ctx.bars * ticks_per_bar
        # Ramp over the *whole* part (or up to 8 bars).
        ramp_bars = min(8, ctx.bars)
        ramp_start = total_ticks - ramp_bars * ticks_per_bar

        # CC events per beat — psytrance sweeps are tight.
        events_per_beat = 4
        step_ticks = ctx.ppq // events_per_beat
        n_events = (total_ticks - ramp_start) // step_ticks

        for i in range(int(n_events)):
            tick = ramp_start + i * step_ticks
            frac = i / max(1, n_events - 1)
            cutoff = int(round(10 + 110 * frac * intensity * density))
            yield CC(
                tick=tick, channel=inst.channel, controller=cc_num,
                value=max(0, min(127, cutoff)),
            )
            # CC 11 expression climbs alongside the filter sweep so the
            # build feels louder, not just brighter. Goes 50 → 127.
            expression = int(round(50 + 77 * frac * intensity))
            yield CC(
                tick=tick, channel=inst.channel, controller=11,
                value=max(0, min(127, expression)),
            )
        # Snap CC 74 (filter) back to low at the very end so the drop
        # starts with a clean transient; expression stays at full so
        # the next part hits hard.
        if total_ticks > 0:
            yield CC(
                tick=total_ticks - 1, channel=inst.channel,
                controller=cc_num, value=10,
            )
