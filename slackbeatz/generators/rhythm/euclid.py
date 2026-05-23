"""``rhythm euclid`` — single-drum Euclidean rhythm.

Branches on ``self.handle`` to pick a drum-conventional default
distribution: ``kick`` is 4-on-the-floor, ``snare`` / ``clap`` lands on
beats 2 & 4, ``hat`` / ``hats`` runs 8th notes with optional ``swing``.

Velocity is ``intensity * base_velocity ± rng-jitter``, so every bar has
small humanising variation but the underlying pattern is stable for the
seed.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import euclid, step_duration, step_to_ticks
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


# Per-handle (pulses, offset) defaults on the 16-step bar.
# Beats are at steps 0/4/8/12; offset=4 ⇒ first hit on beat 2.
_DEFAULTS: dict[str, tuple[int, int]] = {
    "kick":  (4, 0),   # 4-on-the-floor
    "bd":    (4, 0),
    "snare": (2, 4),   # beats 2 & 4
    "sd":    (2, 4),
    "clap":  (2, 4),
    "hat":   (8, 0),   # 8th notes
    "hh":    (8, 0),
    "hats":  (8, 0),
    "ohat":  (1, 14),  # single open-hat on the last 16th of the bar
    "rim":   (5, 3),   # Arduino "extra riff" feel
}

_DEFAULT_VEL: dict[str, int] = {
    "kick":  110,
    "bd":    110,
    "snare": 100,
    "sd":    100,
    "clap":  100,
    "hat":   78,
    "hh":    78,
    "hats":  78,
    "ohat":  88,
    "rim":   95,
}


@register_generator("rhythm", "euclid")
class RhythmEuclid(Generator):
    """One drum voice, Euclidean distribution chosen by handle."""

    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.note is not None, (
            "rhythm gen needs a one-shot drum instrument"
        )
        name = self.handle.lower()
        pulses, offset = _DEFAULTS.get(name, (4, 0))
        base_vel = _DEFAULT_VEL.get(name, 100)
        intensity = self.knob_float("intensity", 1.0)
        swing = self.knob_float("swing", 0.0)

        pattern = euclid(pulses, 16, offset)
        step_ticks = step_duration(ctx.ppq)
        # Swing pushes offbeat steps later by a fraction of a step.
        swing_offset = int(step_ticks * swing * 0.5)
        # Short staccato hit for drum voices.
        dur = max(1, step_ticks // 2)

        for bar in range(ctx.bars):
            bar_start = bar * 4 * ctx.ppq
            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                if step % 2 == 1:
                    tick += swing_offset
                # ±8 velocity jitter for human feel.
                jitter = ctx.rng.randint(-8, 8)
                vel = max(1, min(127, int(round(base_vel * intensity)) + jitter))
                yield Note(
                    tick=tick,
                    duration=dur,
                    channel=inst.channel,
                    pitch=inst.note,
                    velocity=vel,
                )
