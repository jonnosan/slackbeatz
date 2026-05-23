"""``drums deep_techno`` — coordinated deep-techno kit.

Quarter-note kick (low velocity), quarter-note closed hat, occasional
beat-4 clap, *no* fills (deep techno doesn't dramatise transitions —
groove stays locked). Build→drop transition adds an ``ohat`` pickup
on the last 16th of the build's final bar.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import euclid, step_duration, step_to_ticks
from slackbeatz.generators.base import Generator
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
        step_ticks = step_duration(ctx.ppq)
        dur = max(1, step_ticks // 2)

        kick_pat = euclid(_KICK[0], 16, _KICK[1])
        hat_pat = euclid(_HAT[0], 16, _HAT[1])

        for bar in range(ctx.bars):
            bar_start = bar * 4 * ctx.ppq
            for step in range(16):
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                if kick_pat[step]:
                    yield from _emit(kit.drum_notes.get("kick"), kit.channel,
                                      tick, dur, _KICK[2], intensity, ctx)
                if hat_pat[step]:
                    yield from _emit(kit.drum_notes.get("hat"), kit.channel,
                                      tick, dur, _HAT[2], intensity, ctx)
            # Occasional clap on beat 4 (step 12).
            if ctx.rng.random() < 0.3:
                clap_note = kit.drum_notes.get("clap")
                if clap_note is not None:
                    tick = bar_start + step_to_ticks(12, ctx.ppq)
                    yield from _emit(clap_note, kit.channel, tick, dur, 72,
                                      intensity, ctx)

        # Pickup ohat into a drop, on the last 16th of the part.
        if ctx.next_role == "drop":
            ohat_note = kit.drum_notes.get("ohat")
            if ohat_note is not None:
                total = ctx.bars * 4 * ctx.ppq
                tick = total - step_ticks
                yield Note(
                    tick=tick, duration=step_ticks,
                    channel=kit.channel, pitch=ohat_note, velocity=92,
                )


def _emit(note, channel, tick, duration, base_vel, intensity, ctx):
    if note is None:
        return
    jitter = ctx.rng.randint(-5, 5)
    vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
    yield Note(
        tick=tick, duration=duration, channel=channel, pitch=note, velocity=vel,
    )
