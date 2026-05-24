"""Shared helpers for ``subbass`` style modules.

The eight styles split into two broad shapes:

* **Drone** — one long sustained note covering N bars, optionally
  alternating root / fifth. Used by deep_techno, dub_techno,
  vaporwave, lofi, drum_and_bass, acid.
* **Pulsing** — fixed step pattern within each bar (e.g. quarter
  notes, euclidean). Used by psytrance, garage, euclid.

This module hosts a couple of small helpers both shapes need: pitch
resolution (root + fifth in the right octave with chord-progression
following), and the velocity / sidechain envelope shaping that
matches the matching bass style.
"""

from __future__ import annotations

from typing import Iterator

from slackbeatz.engine.event import Event, Note
from slackbeatz.generators._shared import (
    evolution_multiplier,
    pick_evolution_direction,
    should_mute_bar,
    sidechain_envelope,
    transposed_pitch,
)
from slackbeatz.generators.defaults import (
    bass_progression_for,
    base_octave_for,
    base_vel_for,
    duck_for,
    fifth_prob_for,
    gate_for,
    macro_knobs,
    scale_for,
)
from slackbeatz.model.context import PartContext
from slackbeatz.theory.keys import parse_key
from slackbeatz.theory.scales import midi_note, scale_note


def root_at_bar(gen, ctx: PartContext, bar: int) -> int:
    """Return the MIDI pitch the sub should play at *bar*.

    Honours an optional ``progression=NAME`` knob so the sub follows
    chord changes; otherwise plays the song-key tonic at the style's
    natural sub register (set via STYLE_BASE_OCTAVE +
    ``base_octave=`` knob)."""
    octave_off = base_octave_for(gen)
    base_octave = 2 + octave_off
    tonic, _ = parse_key(ctx.key)
    prog = bass_progression_for(gen)
    if prog is not None:
        scale = scale_for(gen, ctx, fallback="minor")
        chord_deg = prog.degree_at_bar(bar)
        pitch = scale_note(chord_deg, tonic, scale, base_octave)
    else:
        pitch = midi_note(tonic, base_octave)
    return transposed_pitch(pitch, ctx.transpose_semitones)


def maybe_fifth(gen, ctx: PartContext, root_pitch: int) -> int:
    """Apply the ``fifth_prob`` knob to optionally raise *root_pitch*
    to its perfect fifth. Returns the root unchanged if the roll
    misses (or fifth_prob is zero / unset)."""
    fifth_prob = fifth_prob_for(gen)
    if fifth_prob > 0 and ctx.rng.random() < fifth_prob:
        return min(127, root_pitch + 7)
    return root_pitch


def emit_sub_note(
    gen,
    ctx: PartContext,
    *,
    tick: int,
    duration: int,
    pitch: int,
    bar: int,
    direction: int,
) -> Note:
    """Construct one sub-bass :class:`Note` with the right velocity
    + sidechain shaping for *gen*'s style. Centralised so every style
    module uses identical dynamics logic."""
    inst = gen.instrument
    assert inst is not None
    intensity = gen.knob_float("intensity", 1.0)
    base_vel = base_vel_for(gen)
    duck = duck_for(gen)
    macro = macro_knobs(gen)
    evo_mult = evolution_multiplier(bar, ctx.bars, macro["evolution"], direction)

    jitter = ctx.rng.randint(-3, 3)  # sub wants steadier dynamics than bass
    vel_base = int(round(base_vel * intensity * evo_mult * ctx.tension)) + jitter
    env = sidechain_envelope(tick % ctx.ticks_per_bar, ctx.ppq, duck=duck)
    vel = max(1, min(127, int(round(vel_base * env))))
    return Note(
        tick=tick, duration=max(1, duration),
        channel=inst.channel, pitch=pitch, velocity=vel,
    )


def drone_generate(
    gen, ctx: PartContext, *, bars_per_note: int, alternate_fifth: bool = False,
) -> Iterator[Event]:
    """Emit one sustained sub note every *bars_per_note* bars.

    *alternate_fifth* swings between root and fifth on alternate cells
    (used by deep_techno for harmonic interest across long drones).
    Honours ``mute_prob`` per cell + the ``fifth_prob`` knob for
    occasional random fifth substitutions.
    """
    gate = gate_for(gen)
    macro = macro_knobs(gen)
    direction = pick_evolution_direction(ctx.rng, macro["evolution"])
    ticks_per_bar = ctx.ticks_per_bar
    cell_ticks = bars_per_note * ticks_per_bar
    full_dur = max(1, int(cell_ticks * gate))

    cell_idx = 0
    bar = 0
    while bar < ctx.bars:
        if should_mute_bar(ctx.rng, macro["mute_prob"]):
            bar += bars_per_note
            cell_idx += 1
            continue
        root = root_at_bar(gen, ctx, bar)
        pitch = root
        if alternate_fifth and (cell_idx % 2 == 1):
            pitch = min(127, root + 7)
        pitch = maybe_fifth(gen, ctx, pitch)

        # Trim the note if the cell would overrun the part end.
        remaining = (ctx.bars - bar) * ticks_per_bar
        dur = min(full_dur, max(1, remaining - 1))
        yield emit_sub_note(
            gen, ctx,
            tick=bar * ticks_per_bar,
            duration=dur,
            pitch=pitch,
            bar=bar,
            direction=direction,
        )
        bar += bars_per_note
        cell_idx += 1


def pulse_generate(
    gen, ctx: PartContext, *, steps: list[int], step_dur_frac: float = 0.9,
) -> Iterator[Event]:
    """Emit a sub-bass hit at each of *steps* within every bar.

    *step_dur_frac* is the per-step gate ratio (0.9 = note lasts 90%
    of the inter-step interval). Steps are 16ths of a bar — passing
    e.g. ``[0, 4, 8, 12]`` gives quarter-note pulses.
    """
    from slackbeatz.generators._shared import step_duration, step_to_ticks
    gate = gate_for(gen)
    macro = macro_knobs(gen)
    direction = pick_evolution_direction(ctx.rng, macro["evolution"])
    step_ticks = step_duration(ctx.ppq)
    base_dur = max(1, int(step_ticks * gate * step_dur_frac))
    ticks_per_bar = ctx.ticks_per_bar

    for bar in range(ctx.bars):
        if should_mute_bar(ctx.rng, macro["mute_prob"]):
            continue
        root = root_at_bar(gen, ctx, bar)
        bar_start = bar * ticks_per_bar
        for step in steps:
            tick = bar_start + step_to_ticks(step, ctx.ppq)
            pitch = maybe_fifth(gen, ctx, root)
            yield emit_sub_note(
                gen, ctx,
                tick=tick,
                duration=base_dur,
                pitch=pitch,
                bar=bar,
                direction=direction,
            )
