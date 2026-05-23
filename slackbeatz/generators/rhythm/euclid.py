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
from slackbeatz.generators._shared import (
    drum_pattern_lookup,
    drum_vel_lookup,
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
    polyrhythm_for,
    stutter_for,
    vel_jitter_for,
)
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
        pulses, offset = drum_pattern_lookup(self.handle, _DEFAULTS)
        # base_vel = handle-specific default unless overridden via the
        # `base_vel=N` knob.
        base_vel = self.knob_int("base_vel", drum_vel_lookup(self.handle, _DEFAULT_VEL, 100))
        macro = macro_knobs(self)
        params = HitParams(
            base_vel=base_vel,
            intensity=self.knob_float("intensity", 1.0),
            vel_jitter=vel_jitter_for(self),
            humanize=self.knob_int("humanize", 0),
            drop_prob=self.knob_float("drop_prob", 0.0),
            accent=self.knob_int("accent", 0),
        )
        swing = self.knob_float("swing", 0.0)
        polyrhythm = polyrhythm_for(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        # New knobs (round 9).
        groove = self.knobs.get("groove", "linear")
        if not isinstance(groove, str):
            groove = "linear"
        ghost = self.knob_float("ghost", 0.0)
        ghost_vel_ratio = self.knob_float("ghost_vel", 0.25)
        # Hat-only knob: probability of substituting an open / pedal
        # hat sound on any given hit. Only meaningful when the gen's
        # GM note is the closed hi-hat (42).
        hat_variant = self.knob_float("hat_variant", 0.0)
        is_hats = name in ("hat", "hh", "hats")

        # Fill knobs: override the historical "every 4th bar" fill cadence.
        # fill_every=N : insert a fill on bar N-1, 2*N-1, ... (defaults to 4)
        # fill_style=NAME : tom_roll / snare_roll / kick_double / silence
        fill_every = self.knob_int("fill_every", 4)
        fill_style = self.knobs.get("fill_style")
        if not isinstance(fill_style, str):
            fill_style = ""

        # Phrase-aware variation (round 9). The "phrase" downbeat (bar
        # phrase_lift, 2*phrase_lift, …) gets a small velocity bump so
        # the listener feels the start of each 4/8/16-bar phrase.
        phrase_lift = self.knob_int("phrase_lift", 0)
        # Stutter on section transitions: 4 × 32nd-note retrigger of
        # this drum's note in the last 16th of the last bar, fires only
        # when the next part is a drop AND the random roll succeeds.
        stutter = stutter_for(self)

        step_ticks = step_duration(ctx.ppq)
        swing_offset = int(step_ticks * swing * 0.5)
        dur = max(1, step_ticks // 2)
        # Issue #12: secondary euclid layer at lower velocity. If
        # polyrhythm=3, we overlay 3 evenly-spread pulses against the
        # main 4/16 (or whatever base) pattern — classic 3-against-4
        # cross-rhythm. Skipped silently when polyrhythm=0.
        if polyrhythm > 0:
            poly_pattern = euclid(polyrhythm, ctx.steps_per_bar, 0)
            poly_vel_scale = 0.65  # softer than primary
        else:
            poly_pattern = None

        # Issue #14: ctx.tension folds into the intensity_mult passed
        # to humanize_hit, so the part-level energy scalar applies to
        # every rhythm/drums hit through the existing velocity pipeline.
        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            bar_pulses = drift_pulses(pulses, macro["density_drift"], ctx.rng)
            # Fill-style overlay: every fill_every'th bar, swap the
            # standard pattern for a fill shape. The historical Arduino
            # behaviour was bar-3-of-4 fills, exposed here as a knob.
            is_fill_bar = (
                fill_every > 0
                and bar > 0
                and ((bar + 1) % fill_every) == 0
            )
            if is_fill_bar and fill_style:
                pattern = _fill_pattern(name, fill_style, ctx.steps_per_bar)
            else:
                pattern = euclid(bar_pulses, ctx.steps_per_bar, offset)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction) * ctx.tension
            # Phrase lift: bar 0 of each phrase gets a small velocity
            # bump so phrase boundaries are audible.
            phrase_bump = 0
            if phrase_lift > 0 and bar % phrase_lift == 0:
                phrase_bump = 8
            bar_start = bar * ctx.ticks_per_bar
            for step, hit in enumerate(pattern):
                if not hit:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                if step % 2 == 1:
                    tick += swing_offset
                # Apply groove-template tick offset on top of swing.
                tick += groove_offset(groove, step)
                shaped = humanize_hit(params, ctx.rng, step, tick, intensity_mult=evo_mult)
                if shaped is None:
                    continue
                vel, tick = shaped
                vel = max(1, min(127, vel + phrase_bump))
                # Hi-hat variation: occasionally substitute open/pedal
                # for the gen's main note. Only meaningful on hats gens
                # where the inst note is the closed hi-hat (42).
                pitch = inst.note
                if is_hats and hat_variant > 0 and ctx.rng.random() < hat_variant:
                    # 70% chance open hi-hat (+4 GM offset), 30% pedal (+2)
                    pitch += 4 if ctx.rng.random() < 0.7 else 2
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            # Ghost notes: quiet hits on syncopated 16ths that don't
            # already have a main hit. Probability per non-hit step is
            # the ``ghost`` knob; velocity is base_vel * ghost_vel_ratio.
            if ghost > 0:
                # Steps to consider: off-beats (steps 1, 3, 5, ...
                # i.e. the syncopated 16ths between the main hits).
                for step in range(ctx.steps_per_bar):
                    if pattern[step]:
                        continue
                    if step % 2 == 0:
                        continue  # only on syncopated 16ths
                    if ctx.rng.random() >= ghost:
                        continue
                    tick = bar_start + step_to_ticks(step, ctx.ppq) + groove_offset(groove, step)
                    ghost_vel = max(
                        1,
                        min(
                            127,
                            int(round(base_vel * params.intensity * evo_mult * ghost_vel_ratio)),
                        ),
                    )
                    yield Note(
                        tick=tick, duration=dur,
                        channel=inst.channel, pitch=inst.note, velocity=ghost_vel,
                    )
            # Polyrhythm overlay (issue #12): softer secondary layer.
            if poly_pattern is not None:
                for step, hit in enumerate(poly_pattern):
                    if not hit:
                        continue
                    tick = bar_start + step_to_ticks(step, ctx.ppq)
                    poly_vel = max(1, min(127, int(round(base_vel * params.intensity * evo_mult * poly_vel_scale))))
                    yield Note(
                        tick=tick, duration=dur,
                        channel=inst.channel, pitch=inst.note, velocity=poly_vel,
                    )

        # Stutter retrigger before a drop section: 4 × 32nd notes
        # of the gen's drum, decaying velocity, on the last 16th of
        # the last bar. Only fires when ctx.next_role == "drop" and
        # the stutter knob roll succeeds.
        if stutter > 0 and ctx.next_role == "drop" and ctx.rng.random() < stutter:
            last_bar_start = (ctx.bars - 1) * ctx.ticks_per_bar
            last_16th_start = last_bar_start + (ctx.steps_per_bar - 1) * step_ticks
            thirty_second = step_ticks // 2
            for i in range(4):
                tick = last_16th_start + i * thirty_second
                # Decaying velocity for the retrigger run.
                vel = max(1, min(127, base_vel - 5 * i))
                yield Note(
                    tick=tick, duration=max(1, thirty_second - 4),
                    channel=inst.channel, pitch=inst.note, velocity=vel,
                )


def _fill_pattern(name: str, style: str, steps_per_bar: int) -> list[bool]:
    """Return a 16-step boolean pattern for the fill bar.

    Drum-name-aware: ``snare_roll`` only fires extra hits for ``snare``;
    ``tom_roll`` for ``tom`` / ``rim``; ``kick_double`` doubles up the
    kick. Other drums use their normal pattern during the fill so the
    fill doesn't accidentally double its own voice.
    """
    pattern = [False] * steps_per_bar
    if style == "snare_roll" and name in ("snare", "sd", "clap"):
        # Sixteenth-note roll on the last beat (steps 12-15).
        for s in (12, 13, 14, 15):
            pattern[s] = True
    elif style == "tom_roll" and name in ("rim", "tom", "ltom", "mtom", "htom"):
        # Descending roll across the whole bar.
        for s in (0, 4, 8, 10, 12, 13, 14, 15):
            pattern[s] = True
    elif style == "kick_double" and name in ("kick", "bd"):
        # Kick on every 8th (8 hits per bar instead of 4).
        for s in range(0, steps_per_bar, 2):
            pattern[s] = True
    elif style == "silence":
        # Drop out entirely on the fill bar — DJ-style breakdown.
        return pattern
    else:
        # Default: same 4-on-the-floor / backbeat pattern; the fill
        # came from a different voice. Avoid empty fallback.
        if name in ("kick", "bd"):
            for s in (0, 4, 8, 12):
                pattern[s] = True
        elif name in ("snare", "sd", "clap"):
            pattern[4] = True
            pattern[12] = True
        elif name in ("hat", "hh", "hats"):
            for s in range(0, steps_per_bar, 2):
                pattern[s] = True
    return pattern
