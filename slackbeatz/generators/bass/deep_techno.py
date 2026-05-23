"""``bass deep_techno`` — sustained half-notes, root and fifth.

Slow, long-gated, low-register. Alternates root and fifth every two
bars for harmonic interest without losing the dubby static feel.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import sidechain_envelope
from slackbeatz.generators.base import Generator
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note


@register_generator("bass", "deep_techno")
class BassDeepTechno(Generator):
    def generate(self, ctx: PartContext) -> Iterator[Event]:
        inst = self.instrument
        assert inst is not None and inst.is_pitched

        octave_off = self.knob_int("octave", -1)
        intensity = self.knob_float("intensity", 1.0)
        gate = self.knob_float("gate", 0.9)
        # Deep techno wants a *light* sidechain on the long sustained
        # notes — just enough to feel the kick under the bass.
        duck = self.knob_float("duck", 0.7)
        base_vel = 80

        tonic, _ = parse_key(ctx.key)
        root = midi_note(tonic, 2 + octave_off)
        fifth = root + 7

        ticks_per_bar = 4 * ctx.ppq
        # Two-bar cell: root for 2 bars, fifth for 2 bars.
        cell_ticks = 2 * ticks_per_bar
        dur = max(1, int(cell_ticks * gate))

        bar = 0
        while bar < ctx.bars:
            tick = bar * ticks_per_bar
            cell_idx = (bar // 2) % 2
            pitch = root if cell_idx == 0 else fifth
            jitter = ctx.rng.randint(-4, 4)
            vel_base = int(round(base_vel * intensity)) + jitter
            # Long sustained notes start on a downbeat (tick_in_bar=0),
            # so sidechain ducks them at note-on. The synth's envelope
            # smooths the rest — this is the right pumping feel here
            # without needing finer-grained per-tick CC modulation.
            env = sidechain_envelope(tick % ticks_per_bar, ctx.ppq, duck=duck)
            vel = max(1, min(127, int(round(vel_base * env))))
            # Clamp duration to part end.
            remaining = (ctx.bars - bar) * ticks_per_bar
            yield Note(
                tick=tick, duration=min(dur, remaining - 1),
                channel=inst.channel, pitch=pitch, velocity=vel,
            )
            bar += 2
