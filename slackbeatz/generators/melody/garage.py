"""``melody garage`` — vocal-style stab phrases.

Short, syncopated, mid-register phrases that emulate chopped vocal
samples. Pentatonic minor for the soulful R&B feel.
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


# Garage pent-minor "vocal hook" tones: tonic + perfect-4th + 5th +
# minor-7th. The hook gesture is a low-then-high upward leap on every
# off-beat 16th — the "ooh-AAH" gesture chopped vocalists do.
_HOOK_LOW = (0, 2)         # tonic or 4th
_HOOK_HIGH_OFFSETS = (7, 12)  # 5th up or octave up — the "stab" target


# Syncopated 16th positions per bar where the "low" stab fires (then
# the high stab follows one 16th later). These positions skip the
# downbeats — garage swings hard around the 4-on-the-floor kick.
_SYNC_POSITIONS = (3, 6, 9, 13)


@register_generator("melody", "garage")
class MelodyGarage(Generator):
    """``melody garage`` — 2-note vocal-stab hook on syncopated 16ths.

    UK garage / 2-step's signature melodic hook is a chopped vocal
    sample doing a low-to-high leap on a syncopated 16th (think Artful
    Dodger's "Re-Rewind", Sweet Female Attitude's "Flowers"). We
    emulate it with a pair of pent-minor stabs:

      * "low" stab on off-beat 16ths (positions 3, 6, 9, 13 of the bar)
      * "high" stab one 16th later, a 5th or octave above

    Each phrase picks which positions actually fire (2-of-4) for
    syncopation variety. The 5th-up vs octave-up leap is chosen per
    pair from the rng — mostly octave (the bigger "ahh!" gesture)
    with occasional 5ths for a tighter call-back feel.
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
        scale = scale_for(self, ctx, fallback="minor_pentatonic")

        mistakes = mistakes_for(self)

        tonic, _ = parse_key(ctx.key)
        step_ticks = ctx.ppq // 4   # 16th

        for bar in range(ctx.bars):
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            evo_mult = evolution_multiplier(
                bar, ctx.bars, macro["evolution"], direction,
            )
            bar_start = bar * ctx.ticks_per_bar

            # 2 of the 4 syncopated positions fire this bar — picked
            # deterministically from the rng so each bar has its own
            # 16th pattern but the hook gesture stays recognisable.
            firing = sorted(ctx.rng.sample(_SYNC_POSITIONS, 2))
            for low_pos in firing:
                low_deg = ctx.rng.choice(_HOOK_LOW)
                low_pitch = transposed_pitch(
                    scale_note(low_deg, tonic, scale, 4 + octave_off),
                    ctx.transpose_semitones,
                )
                if not 0 <= low_pitch <= 127:
                    continue
                # Up-leap interval: 80% octave, 20% perfect 5th.
                leap = 12 if ctx.rng.random() < 0.8 else 7
                high_pitch = low_pitch + leap
                if not 0 <= high_pitch <= 127:
                    high_pitch = low_pitch  # collapse to single note

                tick_low = bar_start + low_pos * step_ticks
                tick_high = bar_start + (low_pos + 1) * step_ticks
                if tick_high >= bar_start + ctx.ticks_per_bar:
                    # Pair would spill into next bar; just emit the
                    # low one.
                    pairs: list[tuple[int, int]] = [(tick_low, low_pitch)]
                else:
                    pairs = [(tick_low, low_pitch), (tick_high, high_pitch)]

                for i, (tick, pitch) in enumerate(pairs):
                    base_dur = max(1, int(step_ticks * gate))
                    dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
                    jitter = ctx.rng.randint(-4, 4)
                    # High stab slightly louder (the "ahh!" peak).
                    accent = 6 if i == 1 else 0
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
                        tick=tick, duration=dur,
                        channel=inst.channel, pitch=pitch, velocity=vel,
                    )
