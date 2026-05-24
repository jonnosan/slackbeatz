"""``melody drum_and_bass`` — sparse jazz-flavoured phrases.

Picks notes from the dorian scale, 2-3 notes per 4 bars, with longer
sustains. Channels the atmospheric "liquid DnB" feel.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_mistake,
    melody_phrase_bump,
    apply_gate_jitter,
    evolution_multiplier,
    maybe_passing_tone,
    pick_evolution_direction,
    should_mute_bar,
    transposed_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    mistakes_for,
    base_octave_for,
    base_vel_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    passing_tones_for,
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


# Liquid DnB minor-7th arpeggio (root, b3, 5, b7 of dorian) plus the
# 9th and 11th for occasional jazz colour. Ascending by default; the
# direction flips per phrase.
_ARP_TONES = (0, 2, 4, 6)        # b7th-chord arpeggio (root, 3, 5, 7)
_JAZZ_TONES = (8, 10)            # 9th + 11th for colour notes


@register_generator("melody", "atmos_lead")
class MelodyAtmosLead(Generator):
    """``melody drum_and_bass`` — liquid 7th-chord arpeggio.

    The signature melodic sound of liquid funk DnB (LTJ Bukem,
    Calibre, High Contrast): a 7th-chord arpeggio sketched out as
    8th notes in the second half of a 4-bar phrase, then 2.5 bars of
    breathing room. Each successive phrase reverses direction
    (up → down → up → down) so the arpeggio bounces.

    Beat layout per 4-bar window::

        bar  0 1 2 3
        kit  . . . .  R 3 5 7 9 11
        beat 1 2 3 4  ^ ^ ^ ^ ^  ^  ← 8th-note arpeggio across 1.5 bars
                       starts on beat 3 of bar 3
    """

    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        base_vel = base_vel_for(self)
        gate_jitter = gate_jitter_for(self)
        passing_tones = passing_tones_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="dorian")

        mistakes = mistakes_for(self)

        tonic, _ = parse_key(ctx.key)
        ticks_per_bar = ctx.ticks_per_bar
        eighth = ctx.ppq // 2

        # Phrase length: 4-bar groups when available, but for songs
        # shorter than 4 bars we scale the phrase to fit so the
        # arpeggio still fires somewhere in the song. For ctx.bars >= 4
        # the original "every 4 bars" pacing is preserved.
        phrase_len = min(4, ctx.bars) if ctx.bars > 0 else 4
        phrase_idx = 0
        for bar in range(0, ctx.bars, phrase_len):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                phrase_idx += 1
                continue
            # Build the arpeggio tone list — 4 chord tones, optionally
            # extended with one jazz colour tone (1-in-3 phrases).
            tones = list(_ARP_TONES)
            if ctx.rng.random() < 0.33:
                tones.append(ctx.rng.choice(_JAZZ_TONES))
            # Reverse direction on alternate phrases.
            if phrase_idx % 2 == 1:
                tones.reverse()

            # Arpeggio starts at the latter half of the phrase: beat 3
            # of bar (phrase_len // 2). Scales to phrase length so
            # 1/2/3-bar songs still see the arp fire.
            half_bar = phrase_len // 2
            start_tick = (bar + half_bar) * ticks_per_bar + 2 * ctx.ppq
            evo_mult = evolution_multiplier(
                bar, ctx.bars, macro["evolution"], direction,
            )
            for i, deg in enumerate(tones):
                tick = start_tick + i * eighth
                # Stop if we've spilled past the phrase boundary.
                if tick >= (bar + phrase_len) * ticks_per_bar:
                    break
                pitch = transposed_pitch(
                    scale_note(deg, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                pitch = maybe_passing_tone(pitch, passing_tones, ctx.rng)
                if not 0 <= pitch <= 127:
                    continue
                base_dur = max(1, int(eighth * gate))
                dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                jitter = ctx.rng.randint(-4, 4)
                # First + last notes of the arp ring out slightly
                # louder — the "phrase peaks" of the line.
                accent = 8 if i in (0, len(tones) - 1) else 0
                vel = max(
                    1,
                    min(
                        127,
                        int(round(base_vel * intensity * evo_mult * ctx.tension))
                        + jitter + melody_phrase_bump(bar, self) + accent,
                    ),
                )
                pitch, tick, vel = apply_mistake(pitch, tick, vel, mistakes, ctx.rng)
                yield Note(
                    tick=tick, duration=max(1, dur),
                    channel=inst.channel, pitch=pitch, velocity=vel,
                )
            phrase_idx += 1
