"""``candy euclid`` — risers / sweeps as CC ramps.

If the part's role is ``build`` (or ``next_role == 'drop'``), emits a
CC 74 (filter cutoff) ramp from low to high over the last 4 bars,
followed by a single noise-burst note on the downbeat of the part's
end. Otherwise silent — the candy gen is a transition-only generator.

Also emits parallel ramps on CC 71 (resonance) and CC 11 (expression)
so the build feels louder + screamier, not just brighter.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import macro_knobs
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note


# Issue #20: include transition / fill roles so candy gen swoops over
# short transitional parts too.
_BUILD_ROLES = {"build", "buildup", "transition", "fill"}


@register_generator("candy", "euclid_riser")
class CandyEuclidRiser(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        if inst is None:
            return
        # Only act on build-shaped roles (or anything heading into a drop).
        is_build = ctx.role in _BUILD_ROLES or ctx.next_role == "drop"
        if not is_build:
            return
        # Per-part-instance mute: drop out of this build entirely with
        # mute_prob chance (so not every build sweeps).
        macro = macro_knobs(self)
        if macro["mute_prob"] > 0 and ctx.rng.random() < macro["mute_prob"]:
            return

        intensity = self.knob_float("intensity", 1.0)
        density = self.knob_float("density", 0.5)
        controller = self.knob_int("cc", 74)

        ticks_per_bar = ctx.ticks_per_bar
        total_ticks = ctx.bars * ticks_per_bar
        # Ramp over the last min(4, bars) bars.
        ramp_bars = min(4, ctx.bars)
        ramp_start = total_ticks - ramp_bars * ticks_per_bar
        steps = max(8, int(32 * density))  # how many CC events in the ramp

        resonance_knob = self.knob_int("resonance", 90)  # 0 disables CC 71 sweep
        for i in range(steps):
            frac = i / (steps - 1) if steps > 1 else 1.0
            tick = ramp_start + int((total_ticks - ramp_start) * frac)
            cutoff = int(round(20 + 100 * frac * intensity))
            yield CC(
                tick=tick,
                channel=inst.channel,
                controller=controller,
                value=max(0, min(127, cutoff)),
            )
            # CC 11 expression: crescendo over the same span.
            expression = int(round(60 + 67 * frac * intensity))
            yield CC(
                tick=tick, channel=inst.channel, controller=11,
                value=max(0, min(127, expression)),
            )
            # CC 71 resonance: climbs alongside cutoff for the squelchier
            # filter-screech feel as we approach the drop.
            if resonance_knob > 0:
                resonance = int(round(30 + (resonance_knob - 30) * frac * intensity))
                yield CC(
                    tick=tick, channel=inst.channel, controller=71,
                    value=max(0, min(127, resonance)),
                )

        # Noise burst note on the downbeat of the *next* part — but we
        # can only emit within this part, so put it on the last tick.
        if inst.is_pitched:
            tonic, _ = parse_key(ctx.key)
            pitch = midi_note(tonic, 5)
        else:
            assert inst.note is not None
            pitch = inst.note
        burst_tick = max(0, total_ticks - ctx.ppq // 4)
        yield Note(
            tick=burst_tick, duration=ctx.ppq // 2,
            channel=inst.channel, pitch=pitch, velocity=110,
        )
