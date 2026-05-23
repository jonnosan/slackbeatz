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
    is_transition_part,
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


# Issue #18: bank of 4 fill patterns. Each entry is a dict of
# drum-role → (pulses, offset) for that voice during the fill bar.
# Drums not in a fill dict revert to their base pattern. The rng picks
# one from the bank for each fill bar — adds variety vs. the previous
# single-formula fill.
_FILL_BANK = (
    # Fill 0: snare-roll variant — busy snare + open hat back-half.
    {"snare": (5, 2), "hat": (10, 0), "ohat": (2, 12)},
    # Fill 1: hat-driven build — heavy hats + sparse snare.
    {"snare": (3, 4), "hat": (14, 0), "ohat": (1, 14)},
    # Fill 2: snare-ramp — escalating snare into the next bar.
    {"snare": (8, 0), "hat":  (4, 8), "ohat": (3, 10)},
    # Fill 3: open-hat dominant — sparse snare, lots of ohat.
    {"snare": (2, 4), "hat":  (6, 4), "ohat": (5,  6)},
)


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
        # Issue #20: if this part is a transition / fill, every bar
        # should sound like a fill bar.
        is_transition = is_transition_part(ctx)

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction) * ctx.tension
            bar_start = bar * 4 * ctx.ppq
            is_fill = is_transition or is_fill_bar(bar, group=4)
            is_last_bar = bar == ctx.bars - 1

            # Default patterns with optional density drift applied
            # independently to each voice.
            drift = macro["density_drift"]
            kick_pat = euclid(drift_pulses(_KICK[0], drift, ctx.rng), 16, _KICK[1])
            snare_pat = euclid(drift_pulses(_SNARE[0], drift, ctx.rng), 16, _SNARE[1])
            clap_pat = euclid(drift_pulses(_CLAP[0], drift, ctx.rng), 16, _CLAP[1])
            hat_pat = euclid(drift_pulses(_HAT[0], drift, ctx.rng), 16, _HAT[1])
            ohat_pat = euclid(drift_pulses(_OHAT[0], drift, ctx.rng), 16, _OHAT[1])

            # 4-bar fill (issue #18): pick from the bank of pre-defined
            # fills rather than the single swap-and-perturb formula.
            # Each fill bar rolls a fresh choice so a 32-bar part hears
            # 8 different fills.
            if is_fill:
                fill = ctx.rng.choice(_FILL_BANK)
                for role, (pulses, offset) in fill.items():
                    pat = euclid(pulses, 16, offset)
                    if role == "snare":
                        snare_pat = pat
                    elif role == "hat":
                        hat_pat = pat
                    elif role == "ohat":
                        ohat_pat = pat
                    elif role == "clap":
                        clap_pat = pat

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
