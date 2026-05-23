"""``rhythm lofi`` — lazy lo-fi hip-hop drums (75-85 BPM, J Dilla feel).

The defining lofi drum feel:

* Kick on beat 1 (sometimes ghost on beat 3) — sparse, not 4-on-floor
* Snare on beats 2 & 4 BUT pushed slightly late — the MPC "drunken"
  feel that J Dilla pioneered. Achieved via groove="dilla" by default.
* Hats: shuffled 16ths with the swing of a tape-warped beat. Default
  groove pushes odd 16ths late (shuffle).
* Ghost notes between hits add the "dusty" feel of sampled breaks.

Tempo intentionally low (compose_from_text assigns ~80 BPM for lofi).
The whole point is "music to study to" — lazy, contemplative, never
energetic.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    HitParams,
    drift_pulses,
    euclid,
    evolution_multiplier,
    groove_offset,
    humanize_hit,
    pick_evolution_direction,
    should_mute_bar,
    step_duration,
    step_to_ticks,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_vel_for,
    macro_knobs,
    vel_jitter_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext


# Lofi DNA: kick on beat 1, snare 2+4, busy shuffled hats.
_DEFAULTS: dict[str, tuple[int, int]] = {
    "kick":  (1, 0),     # beat 1 only — sparse, lazy
    "bd":    (1, 0),
    "snare": (2, 4),     # beats 2 & 4 — rock backbeat, but swung
    "sd":    (2, 4),
    "clap":  (2, 4),
    "hat":   (8, 0),     # 8th notes with default shuffle
    "hh":    (8, 0),
    "hats":  (8, 0),
    "ohat":  (1, 14),
    "rim":   (4, 2),
}

_DEFAULT_VEL: dict[str, int] = {
    "kick":  92,  "bd": 92,
    "snare": 75,  "sd": 75,
    "clap":  68,
    "hat":   55,  "hh": 55, "hats": 55,
    "ohat":  62,
    "rim":   60,
}


@register_generator("rhythm", "lofi")
class RhythmLofi(Generator):
    """One drum voice with the dusty lofi feel — default groove=shuffle
    so the swing is already engaged for every gen line without users
    needing to add ``groove=shuffle`` manually."""

    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.note is not None

        name = self.handle.lower()
        pulses, offset = _DEFAULTS.get(name, (4, 0))
        base_vel = self.knob_int("base_vel", _DEFAULT_VEL.get(name, 80))
        macro = macro_knobs(self)
        params = HitParams(
            base_vel=base_vel,
            intensity=self.knob_float("intensity", 1.0),
            vel_jitter=vel_jitter_for(self),
            humanize=self.knob_int("humanize", 3),  # default jitter — feels human
            drop_prob=self.knob_float("drop_prob", 0.0),
            accent=self.knob_int("accent", 0),
        )
        # Lofi defaults: shuffle groove for the swung 16ths, light
        # ghost-note density. User can override to "linear" or "dilla".
        groove = self.knobs.get("groove", "shuffle")
        if not isinstance(groove, str):
            groove = "shuffle"
        ghost = self.knob_float("ghost", 0.1 if name in ("snare", "sd") else 0.0)
        ghost_vel_ratio = self.knob_float("ghost_vel", 0.3)

        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        step_ticks = step_duration(ctx.ppq)
        dur = max(1, step_ticks // 2)

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            bar_pulses = drift_pulses(pulses, macro["density_drift"], ctx.rng)
            pattern = euclid(bar_pulses, ctx.steps_per_bar, offset)
            evo_mult = (
                evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
                * ctx.tension
            )
            bar_start = bar * ctx.ticks_per_bar
            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq) + groove_offset(groove, step)
                shaped = humanize_hit(params, ctx.rng, step, tick, intensity_mult=evo_mult)
                if shaped is None:
                    continue
                vel, tick = shaped
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=inst.note, velocity=vel,
                )
            # Ghost notes on syncopated 16ths between main hits.
            if ghost > 0:
                for step in range(ctx.steps_per_bar):
                    if pattern[step] or step % 2 == 0:
                        continue
                    if ctx.rng.random() >= ghost:
                        continue
                    tick = bar_start + step_to_ticks(step, ctx.ppq) + groove_offset(groove, step)
                    ghost_vel = max(
                        1,
                        min(127, int(round(base_vel * params.intensity * evo_mult * ghost_vel_ratio))),
                    )
                    yield Note(
                        tick=tick, duration=dur,
                        channel=inst.channel, pitch=inst.note, velocity=ghost_vel,
                    )
