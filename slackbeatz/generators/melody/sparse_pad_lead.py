"""``melody deep_techno`` — sparse, modal (dorian), 1–2 notes per bar."""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    apply_gate_jitter,
    apply_mistake,
    call_response_active,
    evolution_multiplier,
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
    mistakes_for,
    pair_for,
    passing_tones_for,
    phrase_lift_for,
    scale_for,
)
from slackbeatz.generators.registry import register_generator
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import scale_note


# Eligible scale degrees in dorian — leans on 3rd, 5th, 7th and 9th for
# Detroit modal hook. Four chord tones from the dorian scale arranged
# so the contour climbs (1, 3, 5, 7) — a classic "Strings of Life"
# silhouette. The motif then rotates per phrase, shifting the starting
# degree, so the same four pitches return at different positions and
# create the "looping but evolving" feel.
_HOOK_DEGREES = (1, 3, 5, 7)


@register_generator("melody", "sparse_pad_lead")
class MelodySparsePadLead(Generator):
    """``melody deep_techno`` — Detroit modal hook.

    A fixed four-note motif (scale degrees 1, 3, 5, 7) played as
    half-notes — one degree per half-bar, so the four notes span two
    bars. Every 8 bars the motif rotates: each repetition starts one
    degree later than the previous (1357 → 3571 → 5713 → 7135 → 1357).
    The result is a slow recurring hook that drifts pitch-wise across
    the song without ever being a literal one-bar loop — the
    signature of Detroit deep techno melody (Derrick May / Carl Craig
    "string" lines).
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
        pair = pair_for(self)
        macro = macro_knobs(self)
        direction = pick_evolution_direction(ctx.rng, macro["evolution"])
        scale = scale_for(self, ctx, fallback="dorian")
        # New (round 9): phrase lift + live mistakes.
        phrase_lift = phrase_lift_for(self)
        mistakes = mistakes_for(self)

        tonic, _ = parse_key(ctx.key)
        ticks_per_bar = ctx.ticks_per_bar
        # Half-note pacing: two notes per bar.
        cell_ticks = ctx.ppq * 2

        # Iterate the song in half-note cells. Each cell yields one
        # note of the rotating motif.
        n_cells = (ctx.bars * ticks_per_bar) // cell_ticks
        rotation = 0  # which degree the motif currently starts on
        for cell in range(n_cells):
            bar = (cell * cell_ticks) // ticks_per_bar
            # Rotate the motif every 4 hook-iterations (= every 8 bars
            # at 2 cells/bar × 4 hook-notes = 8 bars per rotation cycle).
            if cell > 0 and cell % 16 == 0:
                rotation = (rotation + 1) % 4

            if should_mute_bar(ctx.rng, macro["mute_prob"]):
                continue
            if not call_response_active(self.handle, pair, bar):
                continue

            # Which note of the motif is this? Walk through 4 hook
            # degrees per phrase, starting at `rotation`.
            idx_in_hook = cell % 4
            deg = _HOOK_DEGREES[(rotation + idx_in_hook) % 4]
            pitch = transposed_pitch(
                scale_note(deg, tonic, scale, 4 + octave_off),
                ctx.transpose_semitones,
            )
            pitch = maybe_passing_tone(pitch, passing_tones, ctx.rng)
            if not 0 <= pitch <= 127:
                continue

            evo_mult = evolution_multiplier(
                bar, ctx.bars, macro["evolution"], direction,
            )
            tick = cell * cell_ticks
            # Hold the note for most of the half-bar so the motif
            # "sustains" — the modal hook lives in the long tones.
            base_dur = max(1, int(cell_ticks * gate))
            dur = apply_gate_jitter(base_dur, gate_jitter, ctx.rng)
            jitter = ctx.rng.randint(-4, 4)
            phrase_bump = 8 if phrase_lift > 0 and bar % phrase_lift == 0 else 0
            vel = max(
                1,
                min(
                    127,
                    int(round(base_vel * intensity * evo_mult * ctx.tension))
                    + jitter + phrase_bump,
                ),
            )
            # Apply live mistake (very small probability, randomly
            # perturbs pitch / tick / velocity). Default off.
            pitch, tick, vel = apply_mistake(pitch, tick, vel, mistakes, ctx.rng)
            yield Note(
                tick=tick, duration=dur,
                channel=inst.channel, pitch=pitch, velocity=vel,
            )
