"""``chords vaporwave`` — lush 9th voicings over the i-VII-VI-V descent.

The signature vaporwave chord move: minor-tonic → b7 major → b6 major
→ 5 minor, with each chord voiced as root + 3rd + 5th + 9th (the 2nd
scale degree played an octave up). Sustains the full 4 bars of each
chord at a high gate so they bleed into each other like a Rhodes
electric piano with the sustain pedal down.

Emits CC 91 (reverb send) at the start of each chord to enforce a
deep-reverb tail (``reverb=N`` knob, default 100 of 127 — vaporwave
runs wet).

Issue #16: every other chord (i.e. once every 8 bars) breaks into a
slow rising arpeggio instead of holding — the periodic "Rhodes
sweep" move that's an established trope in vaporwave. Distinct from
the random ``arp_prob`` knob (which fires unpredictably); this one
is a deterministic pulse you can rely on. Disable with
``arp_period=0``; or set ``arp_period=4`` to arpeggiate every chord,
etc.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note
from slackbeatz.generators._shared import (
    ChordProgression,
    apply_gate_jitter,
    evolution_multiplier,
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
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


# Add-9 voicing: root, 3rd, 5th, 9th (the 2nd, one octave higher).
# Expressed as scale-degree offsets from the chord root.
_ADD9 = (0, 2, 4, 8)


@register_generator("chords", "vaporwave")
class ChordsVaporwave(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        arp_prob = self.knob_float("arp_prob", 0.0)
        # Issue #16: deterministic arpeggio every Nth chord (default 2 ⇒
        # every other chord at 4 bars each = once per 8 bars). 0 disables.
        arp_period = self.knob_int("arp_period", 2)
        reverb = self.knob_int("reverb", 100)  # CC 91 send level
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="minor")

        tonic, _ = parse_key(ctx.key)
        prog = ChordProgression("i-VII-VI-V", bars_per_chord=4)

        ticks_per_bar = 4 * ctx.ppq
        chord_ticks = prog.bars_per_chord * ticks_per_bar
        base_dur = max(1, int(chord_ticks * gate))

        # One-shot reverb-send setup at tick 0 — stays for the whole part.
        if reverb > 0:
            yield CC(
                tick=0, channel=inst.channel, controller=91,
                value=max(0, min(127, reverb)),
            )

        bar = 0
        chord_index = 0
        while bar < ctx.bars:
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += prog.bars_per_chord
                chord_index += 1
                continue
            chord_root = prog.degree_at_bar(bar)
            tick = bar * ticks_per_bar
            jitter = ctx.rng.randint(-3, 3)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            vel = max(1, min(127, int(round(base_vel * intensity * evo_mult)) + jitter))

            chord_pitches = [
                transposed_pitch(
                    scale_note(chord_root + off, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                for off in _ADD9
            ]
            chord_pitches = [p for p in chord_pitches if 0 <= p <= 127]
            remaining = (ctx.bars - bar) * ticks_per_bar

            # Issue #16: arpeggio fires deterministically every arp_period
            # chords (and skips chord_index 0 so the part doesn't open
            # with one), OR randomly via arp_prob.
            periodic_arp = (
                arp_period > 0
                and chord_index > 0
                and chord_index % arp_period == 0
            )
            random_arp = arp_prob > 0 and ctx.rng.random() < arp_prob
            if (periodic_arp or random_arp) and chord_pitches:
                # Slow arpeggio (8ths) for vaporwave's Rhodes-piano feel.
                step_ticks = ctx.ppq // 2
                n_steps = max(1, min(chord_ticks, remaining) // step_ticks)
                arp_dur = max(1, int(step_ticks * 1.5))   # overlap for legato
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
            chord_index += 1
