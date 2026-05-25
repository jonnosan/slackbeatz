"""``melody acid_lead`` — sequenced lead that interleaves with the 303.

Designed to share the spotlight with ``bass acid_303``. The 303 bass
plays continuously on 16th notes anchoring root / 3rd / octave; the
lead drops sparse melodic notes in the *gaps* — usually on the
"and-of-beat" offbeats (steps 2, 6, 10, 14) and the "e" / "a"
sixteenths (3, 7, 11, 15) — so the two voices weave around each
other instead of fighting for the same beats.

Plays one octave above the bass (register 4 vs bass register 2) so
they occupy different frequency bands.

Pentatonic minor scale by default — fits over the 303's modal vamp
without dissonance. Per-bar rotation gives the line shape without
becoming a literal loop.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    apply_mistake,
    evolution_multiplier,
    melody_phrase_bump,
    pick_evolution_direction,
    should_mute_bar,
    step_duration,
    step_to_ticks,
    transposed_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    mistakes_for,
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


# 16-step sequencer pattern. Each entry is either ``None`` (silence)
# or a pentatonic scale degree (0=root, 1=2nd, 2=min3, 3=5th, 4=min7).
# Notes land on offbeats / off-eighths so they don't clash with the
# bass's downbeats (steps 0, 4, 8, 12).
_BAR_A: tuple[int | None, ...] = (
    None, None, 0,    None,   # beat 1
    None, None, 3,    None,   # beat 2
    None, None, 2,    None,   # beat 3
    None, 4,    None, 3,      # beat 4 — extra fill on "e" + "a"
)
_BAR_B: tuple[int | None, ...] = (
    None, None, 4,    3,      # beat 1
    None, None, 2,    None,   # beat 2
    None, None, 0,    None,   # beat 3
    None, None, 3,    None,   # beat 4
)
_PATTERN = (_BAR_A, _BAR_B)


@register_generator("melody", "acid_lead")
class MelodyAcidLead(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        if intensity <= 0:
            return
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        # Acid leads stay modal — pentatonic minor by default keeps
        # the melody safely consonant against the 303's root/3rd vamp.
        scale = scale_for(self, ctx, fallback="minor_pentatonic")
        mistakes = mistakes_for(self)

        tonic, _ = parse_key(ctx.key)
        step_ticks = step_duration(ctx.ppq)
        # Short gate by default — the lead is punctuating, not pad-y.
        # ~2 sixteenths long lets the filter envelope per note read
        # without smearing across the next event.
        base_dur = max(1, int(2 * step_ticks * gate))

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            evo_mult = evolution_multiplier(
                bar, ctx.bars, macro["evolution"], direction,
            )
            # Alternate bar A / bar B across the part. Adds variety
            # without losing the call-and-response shape.
            pattern = _PATTERN[bar % len(_PATTERN)]
            bar_start = bar * ctx.ticks_per_bar
            for step, deg in enumerate(pattern):
                if deg is None:
                    continue
                pitch = transposed_pitch(
                    scale_note(deg, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                if not 0 <= pitch <= 127:
                    continue
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                jitter = ctx.rng.randint(-4, 4)
                vel = max(1, min(127, int(round(
                    base_vel * intensity * evo_mult * ctx.tension,
                )) + jitter + melody_phrase_bump(bar, self)))
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                pitch, tick, vel = apply_mistake(
                    pitch, tick, vel, mistakes, ctx.rng,
                )
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
