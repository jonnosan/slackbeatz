"""``chords dub_techno`` — the signature off-beat chord stab.

The defining sound of dub techno (Basic Channel, Rhythm & Sound,
Maurizio): a short, reverb-drenched chord stab on the "and" of every
beat (steps 2, 6, 10, 14 in the 16-step bar) — the "chk-chk-chk-chk"
that rides over the kick. Short gate so each stab punches and fades.

Heavy reverb send (CC 91, default 110) and chorus send (CC 93, default
60) emitted at the start of each part. The CC values stick for the
whole part — set high once, leave alone.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note
from slackbeatz.generators._shared import (
    chord_velocity_mods,
    maybe_emit_drop_sweep,
    apply_gate_jitter,
    build_chord,
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
    step_duration,
    step_to_ticks,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    gate_for,
    gate_jitter_for,
    inversion_for,
    macro_knobs,
    progression_for,
    scale_for,
    voicing_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key

# Off-beat 8ths: the second 16th of each beat.
_OFFBEAT_STEPS = (2, 6, 10, 14)


@register_generator("chords", "offbeat_stab")
class ChordsOffbeatStab(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        reverb = self.knob_int("reverb", 110)  # CC 91 — heavy by default
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="dorian")

        tonic, _ = parse_key(ctx.key)
        prog = progression_for(self, default_name="i-iv", default_bars=8)
        voicing = voicing_for(self, fallback="triad")
        inversion = inversion_for(self)
        step_ticks = step_duration(ctx.ppq)
        # Short stab: gate × 2-step duration ≈ 1/8-note worth of sound.
        base_dur = max(1, int(step_ticks * 2 * gate))

        # One-shot reverb send at part start — dub techno runs WET.
        if reverb > 0:
            yield CC(
                tick=0, channel=inst.channel, controller=91,
                value=max(0, min(127, reverb)),
            )

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            chord_root = prog.degree_at_bar(bar)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            bar_start = bar * ctx.ticks_per_bar
            chord_pitches = build_chord(
                chord_root, tonic=tonic, scale=scale,
                base_octave=4 + octave_off,
                voicing=voicing, inversion=inversion,
                transpose=ctx.transpose_semitones,
            )
            for step in _OFFBEAT_STEPS:
                tick = bar_start + step_to_ticks(step, ctx.ppq)
                jitter = ctx.rng.randint(-4, 4)
                vel = max(1, min(127, int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter + chord_velocity_mods(bar, chord_root, base_vel, self)))
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                for pitch in chord_pitches:
                    yield Note(
                        tick=tick, duration=dur,
                        channel=inst.channel, pitch=pitch, velocity=vel,
                    )
        yield from maybe_emit_drop_sweep(ctx, inst.channel, self)
