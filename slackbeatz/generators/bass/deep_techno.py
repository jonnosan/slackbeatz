"""``bass deep_techno`` — sustained half-notes, root and fifth.

Slow, long-gated, low-register. Alternates root and fifth every two
bars for harmonic interest without losing the dubby static feel.

Issue #17: optional kick-triggered filter envelope. With
``kick_env=N`` (0..1) the gen emits a CC 74 ramp that drops on each
quarter beat (where the kick lands) and recovers by the next, giving
the bass that dub-techno "breathing" feel without needing a real
sidechain'd LFO.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import CC, Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    evolution_multiplier,
    maybe_octave_jump,
    pick_evolution_direction,
    should_mute_bar,
    sidechain_envelope,
    transposed_pitch,
)
from slackbeatz.generators.base import Generator
from slackbeatz.generators.defaults import (
    base_octave_for,
    base_vel_for,
    duck_for,
    gate_for,
    gate_jitter_for,
    macro_knobs,
    octave_jump_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note


@register_generator("bass", "deep_techno")
class BassDeepTechno(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = base_octave_for(self)
        intensity = self.knob_float("intensity", 1.0)
        gate = gate_for(self)
        # Deep techno wants a *light* sidechain on the long sustained
        # notes — just enough to feel the kick under the bass.
        duck = duck_for(self)
        base_vel = base_vel_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])

        gate_jitter = gate_jitter_for(self)
        octave_jump = octave_jump_for(self)
        # Issue #17: kick-triggered filter envelope on CC 74. 0 = off.
        # 0.5 = moderate dip on each kick; 1.0 = full dip 20→120.
        kick_env = self.knob_float("kick_env", 0.0)

        tonic, _ = parse_key(ctx.key)
        root_raw = midi_note(tonic, 2 + octave_off)
        root = transposed_pitch(root_raw, ctx.transpose_semitones)
        fifth = transposed_pitch(root_raw + 7, ctx.transpose_semitones)

        ticks_per_bar = 4 * ctx.ppq
        # Two-bar cell: root for 2 bars, fifth for 2 bars.
        cell_ticks = 2 * ticks_per_bar
        dur = max(1, int(cell_ticks * gate))

        # Issue #17: emit a CC 74 envelope per beat across the whole part.
        # On each quarter beat, drop CC 74 to a low value (the "ducked"
        # filter) and ramp linearly back to the top by the next beat.
        # Four CC events per beat = 16 per bar. The ramp depth scales
        # with kick_env: 0 disables, 1 = full 20→120 swing.
        if kick_env > 0:
            low = int(round(120 - 100 * kick_env))   # kick_env=1 → low=20
            high = 120
            events_per_beat = 4
            step_ticks = ctx.ppq // events_per_beat
            n_beats = ctx.bars * 4
            for beat in range(n_beats):
                beat_tick = beat * ctx.ppq
                for i in range(events_per_beat):
                    tick = beat_tick + i * step_ticks
                    frac = i / max(1, events_per_beat - 1)
                    value = int(round(low + (high - low) * frac))
                    yield CC(
                        tick=tick, channel=inst.channel,
                        controller=74, value=max(0, min(127, value)),
                    )

        bar = 0
        while bar < ctx.bars:
            # mute_prob applies per 2-bar cell here (each note covers 2 bars).
            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                bar += 2
                continue
            tick = bar * ticks_per_bar
            cell_idx = (bar // 2) % 2
            pitch = root if cell_idx == 0 else fifth
            jitter = ctx.rng.randint(-4, 4)
            evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)
            vel_base = int(round(base_vel * intensity * evo_mult)) + jitter
            env = sidechain_envelope(tick % ticks_per_bar, ctx.ppq, duck=duck)
            vel = max(1, min(127, int(round(vel_base * env))))
            # Clamp duration to part end + apply gate jitter.
            remaining = (ctx.bars - bar) * ticks_per_bar
            note_dur = apply_gate_jitter(min(dur, remaining - 1), gate_jitter, ctx.rng)
            final_pitch = maybe_octave_jump(pitch, octave_jump, ctx.rng)
            yield Note(
                tick=tick, duration=max(1, note_dur),
                channel=inst.channel, pitch=final_pitch, velocity=vel,
            )
            bar += 2
