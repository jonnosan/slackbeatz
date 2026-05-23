"""``drums vaporwave`` — coordinated lazy kit.

Half-time kick (beats 1 & 3), snare on 2 & 4, quarter-note closed hat,
no fills. The vibe is "shopping mall in 1992" — drums sit underneath
the chords rather than driving the song.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import euclid, step_duration, step_to_ticks
from slackbeatz.generators.base import Generator
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
        step_ticks = step_duration(ctx.ppq)
        dur = max(1, step_ticks // 2)

        kick_pat = euclid(_KICK[0], 16, _KICK[1])
        snare_pat = euclid(_SNARE[0], 16, _SNARE[1])
        hat_pat = euclid(_HAT[0], 16, _HAT[1])

        for bar in range(ctx.bars):
            bar_start = bar * 4 * ctx.ppq
            for step in range(16):
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                if kick_pat[step]:
                    yield from _emit(kit.drum_notes.get("kick"), kit.channel,
                                      tick, dur, _KICK[2], intensity, ctx)
                if snare_pat[step]:
                    yield from _emit(kit.drum_notes.get("snare"), kit.channel,
                                      tick, dur, _SNARE[2], intensity, ctx)
                if hat_pat[step]:
                    yield from _emit(kit.drum_notes.get("hat"), kit.channel,
                                      tick, dur, _HAT[2], intensity, ctx)


def _emit(note, channel, tick, duration, base_vel, intensity, ctx):
    if note is None:
        return
    jitter = ctx.rng.randint(-4, 4)
    vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
    yield Note(
        tick=tick, duration=duration, channel=channel, pitch=note, velocity=vel,
    )
