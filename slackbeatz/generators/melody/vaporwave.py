"""``melody vaporwave`` — sparse sax-like phrases + slow mod-wheel vibrato.

One phrase per chord (so every 4 bars by default), each phrase is 2–3
notes from the dorian scale in the upper register. Notes are long and
overlap slightly into the next phrase — the smooth-jazz "noodle".

Emits a slow CC 1 (mod wheel) LFO across the part so a synth's vibrato
amount waxes and wanes through each phrase — the swelling vibrato of
a hand-played tenor sax line. Knob ``modwheel=N`` (0..127, default 80)
sets the LFO peak amplitude.
"""

from __future__ import annotations

import math
from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note
from slackbeatz.generators._shared import (
    ChordProgression,
    MotifMemory,
    apply_gate_jitter,
    call_response_active,
    evolution_multiplier,
    maybe_octave_jump,
    maybe_passing_tone,
    pick_evolution_direction,
    should_mute_bar,
    transposed_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    motif_memory_for,
    octave_jump_for,
    pair_for,
    passing_tones_for,
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


# Dorian degrees we draw from — colourful upper-extension notes.
# Tonic / 3rd / 5th / 6th / 7th / 9th = 0, 2, 4, 5, 6, 8.
_DEGREES = [0, 2, 4, 5, 6, 8]


@register_generator("melody", "vaporwave")
class MelodyVaporwave(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        modwheel = self.knob_int("modwheel", 80)  # 0 = no vibrato LFO
        # Optional per-phrase panning: pan=N (-64..63) sets the centre,
        # phrases wander ±10 around it. 64 = MIDI centre.
        pan_center = self.knob_int("pan", 64)
        gate_jitter = gate_jitter_for(self)
        octave_jump = octave_jump_for(self)
        passing_tones = passing_tones_for(self)
        pair = pair_for(self)
        memory = MotifMemory(motif_memory_for(self))
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="dorian")

        tonic, _ = parse_key(ctx.key)
        prog = ChordProgression("i-VII-VI-V", bars_per_chord=4)

        ticks_per_bar = ctx.ticks_per_bar
        ppq = ctx.ppq

        # CC 1 LFO across the whole part — one full cycle per chord (4 bars)
        # so the vibrato amount peaks once per chord. Emit at every quarter.
        if modwheel > 0:
            total_ticks = ctx.bars * ticks_per_bar
            cycle_ticks = prog.bars_per_chord * ticks_per_bar
            for q in range(ctx.bars * 4):
                tick = q * ppq
                if tick >= total_ticks:
                    break
                lfo = (math.sin(math.tau * tick / cycle_ticks) + 1.0) / 2.0
                yield CC(
                    tick=tick, channel=inst.channel, controller=1,
                    value=max(0, min(127, int(round(lfo * modwheel)))),
                )

        last_deg = -1
        bar = 0
        while bar < ctx.bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += prog.bars_per_chord
                continue
            if not call_response_active(self.handle, pair, bar):
                bar += prog.bars_per_chord
                continue
            chord_root_deg = prog.degree_at_bar(bar)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)

            # Pan wandering: each chord gets a different pan position.
            pan_value = max(0, min(127, pan_center + ctx.rng.randint(-10, 10)))
            yield CC(
                tick=bar * ticks_per_bar, channel=inst.channel,
                controller=10, value=pan_value,
            )

            # Descending chromatic lick — the smooth-jazz "noodle".
            # Start on the chord's 7th, walk down chromatically over
            # ~2 bars, land on the chord root, then 2 bars of breathing
            # room before the next phrase.
            #
            # Chromatic steps (in semitones) from the start note,
            # descending: 0, 1, 1, 2, 2 — i.e. 7th → b7 → 6 → 5 →
            # tracks the natural-minor descent with one chromatic
            # passing tone. Six notes total over 2 bars of half-bar
            # quarter-note pacing (~0.4 sec/note at 120 BPM).
            start_pitch = transposed_pitch(
                scale_note(chord_root_deg + 6, tonic, scale, 4 + octave_off),
                ctx.transpose_semitones,
            )
            # Steps below the start. Negative = pitch descends.
            descent_intervals = (0, -2, -3, -5, -7, -10)
            quarter_ticks = ppq
            chord_start = bar * ticks_per_bar
            for i, semis in enumerate(descent_intervals):
                pitch = start_pitch + semis
                pitch = maybe_octave_jump(pitch, octave_jump, ctx.rng)
                pitch = maybe_passing_tone(pitch, passing_tones, ctx.rng)
                if not 0 <= pitch <= 127:
                    continue
                tick = chord_start + i * quarter_ticks
                # Sustain into the next note for a legato feel —
                # roughly 80% of the inter-note gap.
                base_dur = max(1, int(quarter_ticks * gate))
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                jitter = ctx.rng.randint(-3, 3)
                # Final note (the landing on chord root) rings out a
                # little longer + softer — the "exhale".
                if i == len(descent_intervals) - 1:
                    dur = max(dur, quarter_ticks * 2)
                    jitter -= 4
                vel = max(
                    1,
                    min(
                        127,
                        int(round(base_vel * intensity * evo_mult * ctx.tension))
                        + jitter,
                    ),
                )
                last_deg = 6 + semis  # for any downstream memory use
                _ = memory  # touch to avoid unused-warning churn
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            bar += prog.bars_per_chord
