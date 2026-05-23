"""``candy lofi`` — vinyl-crackle simulation + slow filter modulation.

Two-layer texture:

* Very slow CC 74 (filter cutoff) LFO across the whole part — gives
  the Rhodes-EP-being-recorded-to-tape feel where the filter slowly
  breathes.
* "Vinyl crackle" — short bursts of soft hits on a high-pitched note,
  randomly scattered through the part. Imitates the surface noise of
  a sampled vinyl record. Without a real noise patch this approximates
  via the FX 1 (Rain) program (GM 96).
"""

from __future__ import annotations

import math
from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import macro_knobs
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


@register_generator("candy", "lofi")
class CandyLofi(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        if inst is None:
            return
        macro = macro_knobs(self)
        if macro["mute_prob"] > 0 and ctx.rng.random() < macro["mute_prob"]:
            return

        intensity = self.knob_float("intensity", 1.0)
        crackle = self.knob_float("density", 0.4)  # density knob aliased
        cc_num = self.knob_int("cc", 74)
        # Long LFO cycle — slow tape-warble breathing.
        cycle_bars = self.knob_int("cycle", 12)

        ticks_per_bar = ctx.ticks_per_bar
        total_ticks = ctx.bars * ticks_per_bar
        events_per_bar = 8  # CC every 8th note — smoother than every 16th
        step_ticks = ticks_per_bar // events_per_bar
        cycle_ticks = cycle_bars * ticks_per_bar

        # CC LFO sweep — narrow range (40-100) so the filter never
        # closes fully (that'd kill the lofi pad) or opens fully (too
        # bright).
        phase = ctx.rng.random() * math.tau
        n = ctx.bars * events_per_bar
        for i in range(n):
            tick = i * step_ticks
            if tick >= total_ticks:
                break
            theta = phase + math.tau * tick / cycle_ticks
            lfo = (math.sin(theta) + 1.0) / 2.0
            value = int(round(40 + lfo * 60 * intensity))
            yield CC(
                tick=tick, channel=inst.channel, controller=cc_num,
                value=max(0, min(127, value)),
            )

        # Vinyl crackle: short, soft hits on a high-pitched note. The
        # ``crackle`` (= density knob) knob controls how frequent.
        if crackle > 0 and inst.note is not None:
            pitch = inst.note
        elif crackle > 0:
            # Pitched inst — use a high register so the crackle is a
            # tinny "tick" rather than a tone.
            pitch = 96  # C7 — very thin, percussive

        if crackle > 0:
            # Vinyl crackle is CONSTANT background texture in real lofi
            # tracks — not the once-every-few-bars sparseness we had.
            # Roll per 32nd-note position at probability = crackle/2,
            # so the default 0.4 density gives ~20% of 32nds = a few
            # ticks per bar = audible-but-not-foreground vinyl noise.
            sixteenth = ctx.ppq // 4
            n_thirtysecond = total_ticks // (sixteenth // 2)
            for k in range(n_thirtysecond):
                if ctx.rng.random() >= crackle * 0.5:
                    continue
                tick = k * (sixteenth // 2)
                if tick >= total_ticks:
                    break
                vel = ctx.rng.randint(8, 25)  # very quiet — just texture
                yield Note(
                    tick=tick, duration=10,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
