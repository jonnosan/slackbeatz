"""``chords psytrance`` — sparse sus2 voicings, modal i-v progression.

Sus2 = root + 2nd + 5th. No 3rd, so the modal flavour (phrygian b2)
stays open rather than locking the listener into major-vs-minor. One
chord per 4 bars.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    chord_velocity_mods,
    maybe_emit_drop_sweep,
    apply_gate_jitter,
    build_chord,
    evolution_multiplier,
    expression_ramp,
    is_build_part,
    pick_evolution_direction,
    should_mute_bar,
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


@register_generator("chords", "psy_swell")
class ChordsPsySwell(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        arp_prob = self.knob_float("arp_prob", 0.0)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="phrygian")

        tonic, _ = parse_key(ctx.key)
        prog = progression_for(self, default_name="i-v", default_bars=4)
        voicing = voicing_for(self, fallback="sus2")
        inversion = inversion_for(self)

        ticks_per_bar = ctx.ticks_per_bar
        chord_ticks = prog.bars_per_chord * ticks_per_bar
        base_dur = max(1, int(chord_ticks * gate))

        bar = 0
        while bar < ctx.bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += prog.bars_per_chord
                continue
            chord_root = prog.degree_at_bar(bar)
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-3, 3)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            vel = max(1, min(127, int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter + chord_velocity_mods(bar, chord_root, base_vel, self)))

            chord_pitches = build_chord(
                chord_root, tonic=tonic, scale=scale,
                base_octave=4 + octave_off,
                voicing=voicing, inversion=inversion,
                transpose=ctx.transpose_semitones,
            )
            remaining = (ctx.bars - bar) * ticks_per_bar

            if arp_prob > 0 and ctx.rng.random() < arp_prob and chord_pitches:
                step_ticks = ctx.ppq // 4   # 16ths — psytrance loves busy
                n_steps = max(1, min(chord_ticks, remaining) // step_ticks)
                arp_dur = max(1, int(step_ticks * 0.7))
                for i in range(n_steps):
                    arp_tick = tick + i * step_ticks
                    pitch = chord_pitches[i % len(chord_pitches)]
                    yield Note(
                        tick=arp_tick, duration=apply_gate_jitter(arp_dur, gate_jitter, ctx.rng),
                        channel=inst.channel, pitch=pitch, velocity=vel,
                    )
            else:
                for pitch in chord_pitches:
                    dur = apply_gate_jitter(min(base_dur, remaining - 1), gate_jitter, ctx.rng)
                    yield Note(
                        tick=tick, duration=max(1, dur),
                        channel=inst.channel, pitch=pitch, velocity=vel,
                    )
            bar += prog.bars_per_chord

        # Psytrance build → drop: maximum swell — full ramp to 127.
        if is_build_part(ctx):
            yield from expression_ramp(ctx, inst.channel, start=70, end=127)
        yield from maybe_emit_drop_sweep(ctx, inst.channel, self)
