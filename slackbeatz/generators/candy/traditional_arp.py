"""``candy traditional_arp`` — classic up/down chord-tone arpeggiator.

The other half of warm_analogue's "candy" voice rotation (see
``compose._per_song_algorithm_picks``): a traditional synth-pop /
new-wave arpeggio that walks chord tones up/down at a constant
rate, vs ``sh101_top``'s euclidean-clocked SH-101 trigger pattern.

Both fill the same top-arp slot above the lead, but produce
distinct musical characters:

* ``sh101_top`` — uneven euclidean rhythm, fixed pitch sequence
  that rotates against the trigger pattern.
* ``traditional_arp`` — even rhythm, walks chord tones in a chosen
  direction, optionally spanning multiple octaves.

Hash-picked 50/50 per song so warm_analogue tracks alternate between
the two flavours of analogue top-line.

Knobs:
* ``progression`` / ``bars_per_chord`` — chord progression to walk.
* ``voicing`` — chord shape (default ``triad`` = root+3rd+5th;
  ``seventh`` adds the 7th; etc.).
* ``direction`` — ``up``/``down``/``updown``/``random``.
* ``rate`` — arpeggio steps per bar (default 16 = 16ths).
* ``octave_range`` — how many octaves the arp spans (default 1).
* ``gate`` — note length as fraction of step (default 0.5).
* ``octave`` / ``base_octave`` — base register offset.
* ``base_vel``, ``intensity``, ``evolution``.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
    step_duration,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    bass_progression_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    scale_for,
    voicing_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


_VOICING_OFFSETS = {
    "triad": (0, 2, 4),         # root, 3rd, 5th
    "seventh": (0, 2, 4, 6),
    "sus2": (0, 1, 4),
    "sus4": (0, 3, 4),
    "open": (0, 4, 7),
    "power": (0, 4),
}


@register_generator("candy", "traditional_arp")
class CandyTraditionalArp(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        intensity = self.knob_float("intensity", 1.0)
        if intensity <= 0:
            return

        octave_off = base_octave_for(self)
        gate = gate_for(self, fallback=0.5)
        gate_jitter = gate_jitter_for(self)
        base_vel = base_vel_for(self)
        macro = macro_knobs(self)
        direction_str = str(self.knobs.get("direction", "up")).lower()
        rate = self.knob_int("rate", 16)
        rate = max(1, min(32, rate))
        octave_range = self.knob_int("octave_range", 1)
        octave_range = max(1, min(3, octave_range))
        voicing = voicing_for(self, fallback="triad")
        offsets = _VOICING_OFFSETS.get(voicing, _VOICING_OFFSETS["triad"])

        # Expand chord tones across the requested octave span. e.g.
        # triad + octave_range=2 → [0,2,4, 7,9,11] (the 7 = 0+7 semis
        # = next-octave root, but in scale-degree terms it's deg+7).
        all_degrees: list[int] = []
        for oct_n in range(octave_range):
            for off in offsets:
                all_degrees.append(off + oct_n * 7)  # 7 = degrees per octave

        progression = bass_progression_for(self)
        if progression is None:
            # Without a chord progression this gen has no harmonic
            # frame — quietly stay silent rather than play random
            # tonic arps.
            return

        tonic, _ = parse_key(ctx.key)
        scale = scale_for(self, ctx, fallback="minor")
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        step_ticks = max(1, int(ctx.ticks_per_bar / rate))
        base_dur = max(1, int(step_ticks * gate))

        # Pitch pointer is GLOBAL across the part so direction
        # changes feel continuous bar-to-bar.
        idx = 0
        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                idx += rate  # keep counter advancing
                continue
            evo_mult = evolution_multiplier(
                bar, ctx.bars, macro["evolution"], direction,
            )
            chord_root_deg = progression.degree_at_bar(bar)
            bar_start = bar * ctx.ticks_per_bar

            for step in range(rate):
                n = len(all_degrees)
                if direction_str == "down":
                    pos = (n - 1) - (idx % n)
                elif direction_str == "updown":
                    period = max(1, (n - 1) * 2) if n > 1 else 1
                    cyc = idx % period
                    pos = cyc if cyc < n else (period - cyc)
                elif direction_str == "random":
                    pos = ctx.rng.randrange(n)
                else:  # 'up' / fallback
                    pos = idx % n
                idx += 1

                deg = all_degrees[pos]
                effective_deg = chord_root_deg + deg
                pitch = scale_note(effective_deg, tonic, scale, 4 + octave_off)
                pitch += ctx.transpose_semitones
                if not 0 <= pitch <= 127:
                    continue

                tick = bar_start + step * step_ticks
                jitter = ctx.rng.randint(-4, 4)
                vel = max(1, min(127, int(round(
                    base_vel * intensity * evo_mult * ctx.tension,
                )) + jitter))
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                yield Note(
                    tick=tick, duration=dur,
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
