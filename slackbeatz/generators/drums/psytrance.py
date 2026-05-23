"""``drums psytrance`` — kick + offbeat hat + sparse clap.

Locked groove — no fills (psytrance keeps it hypnotic), but the clap
count per 4-bar group varies (1–3 claps) for subtle evolution.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import euclid, step_duration, step_to_ticks
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


_KICK = (4, 0, 118)
_HAT_OFFBEAT = (4, 2, 90)   # offbeat hat


@register_generator("drums", "psytrance")
class DrumsPsytrance(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        kit = self.kit
        assert kit is not None

        intensity = self.knob_float("intensity", 1.0)
        step_ticks = step_duration(ctx.ppq)
        dur = max(1, step_ticks // 2)

        kick_pat = euclid(_KICK[0], 16, _KICK[1])
        hat_pat = euclid(_HAT_OFFBEAT[0], 16, _HAT_OFFBEAT[1])

        # Per-4-bar-group: pick how many bars in the group get a clap.
        clap_bars_in_group = ctx.rng.randint(1, 3)

        for bar in range(ctx.bars):
            bar_start = bar * 4 * ctx.ppq
            for step in range(16):
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                if kick_pat[step]:
                    yield from _emit(kit.drum_notes.get("kick"), kit.channel,
                                      tick, dur, _KICK[2], intensity, ctx)
                if hat_pat[step]:
                    yield from _emit(kit.drum_notes.get("hat"), kit.channel,
                                      tick, dur, _HAT_OFFBEAT[2], intensity, ctx)
            # Re-roll clap-bar count at the start of each 4-bar group.
            if bar % 4 == 0:
                clap_bars_in_group = ctx.rng.randint(1, 3)
            # Add clap on beat 3 in `clap_bars_in_group` of every 4 bars.
            if (bar % 4) < clap_bars_in_group:
                clap_note = kit.drum_notes.get("clap")
                if clap_note is not None:
                    tick = bar_start + step_to_ticks(8, ctx.ppq)  # beat 3
                    yield from _emit(clap_note, kit.channel, tick, dur, 88,
                                      intensity, ctx)


def _emit(note, channel, tick, duration, base_vel, intensity, ctx):
    if note is None:
        return
    jitter = ctx.rng.randint(-6, 6)
    vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
    yield Note(
        tick=tick, duration=duration, channel=channel, pitch=note, velocity=vel,
    )
