"""Shared algorithm primitives.

* :func:`euclid` — Bjorklund-style even pulse distribution, ported in
  spirit from the Arduino prototype's ``distribute_notes``. The bread-
  and-butter primitive for techno-style 4-on-the-floor / off-beat hat /
  rolling-snare patterns.
* :func:`bar_to_ticks` / :func:`step_to_ticks` — small helpers to convert
  between the 16-step grid every algorithm uses and the engine's PPQ
  ticks.
* :class:`ChordProgression` — picks a chord-root degree for each bar of
  a part. Captures the Arduino "Track Chords" idea so a melody gen can
  follow the same progression a chord gen is playing.
* :func:`fill_perturb` — re-rolls drum pulse counts upward by a small
  amount, used as the 4-bar fill behaviour of the ``drums euclid`` /
  ``drums deep_techno`` algorithms.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from slackbeatz.engine.event import CC
    from slackbeatz.model.context import PartContext

# Every algorithm uses a 16-step grid per bar (the Arduino convention).
STEPS_PER_BAR = 16


# --------------------------------------------------------------------------
# Per-hit shaping (humanize / drop / accent / velocity jitter)
#
# These helpers are the *current* consumption point for the universal
# Feel knob set declared in :mod:`slackbeatz.generators.feel`. A future
# phase will hoist this logic into
# :func:`slackbeatz.engine.feel_apply.apply_feel` (post-emit, scheduler-
# level) so generators stop opting in by calling the helpers and the
# Feel set is guaranteed to apply uniformly across every algorithm. See
# the module docstring of :mod:`slackbeatz.engine.feel_apply` for the
# tradeoffs of the hoist (byte-identical break + corpus regen).
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class HitParams:
    """Bundle of per-hit chance knobs all rhythm/drums gens read.

    These are uniform across styles so every rhythm/drums voice can wear
    the same set of small humanising knobs in the DSL.
    """

    base_vel: int = 100
    intensity: float = 1.0
    vel_jitter: int = 8         # ±N velocity points (random)
    humanize: int = 0           # ±N tick offset per hit
    drop_prob: float = 0.0      # chance to drop a hit entirely
    accent: int = 0             # every accent-th step gets +12 velocity


def humanize_hit(
    params: HitParams,
    rng: random.Random,
    step: int,
    tick: int,
    *,
    intensity_mult: float = 1.0,
) -> tuple[int, int] | None:
    """Apply the chance knobs to one rhythmic hit.

    Returns ``(velocity, tick)`` or ``None`` if the hit was dropped by
    ``drop_prob``. Algorithms call this once per pattern position they
    intend to emit a note at.

    ``intensity_mult`` is an extra multiplier (typically the per-bar
    ``evolution`` ramp) layered on top of ``params.intensity``.
    """
    if params.drop_prob > 0 and rng.random() < params.drop_prob:
        return None
    vel = int(round(params.base_vel * params.intensity * intensity_mult))
    if params.vel_jitter > 0:
        vel += rng.randint(-params.vel_jitter, params.vel_jitter)
    if params.accent > 0 and step % params.accent == 0:
        vel += 12
    vel = max(1, min(127, vel))
    new_tick = tick
    if params.humanize > 0:
        new_tick = max(0, tick + rng.randint(-params.humanize, params.humanize))
    return (vel, new_tick)


# --------------------------------------------------------------------------
# Pattern + macro chance (issues #2, #8, #9)
# --------------------------------------------------------------------------

def drift_pulses(base: int, drift: float, rng: random.Random) -> int:
    """Per-bar Euclidean pulse-count perturbation.

    ``drift=0`` returns *base* unchanged. ``drift=0.5`` means roughly
    half of bars roll ±1 around the base; ``drift=1`` always perturbs.
    Result is clamped to ``[0, STEPS_PER_BAR]`` so an extreme drift
    doesn't degenerate a 4/16 kick into all-pulses.
    """
    if drift <= 0:
        return base
    if rng.random() >= drift:
        return base
    return max(0, min(STEPS_PER_BAR, base + rng.choice([-1, 1])))


def should_mute_bar(rng: random.Random, mute_prob: float) -> bool:
    """Roll the per-bar gen-drop chance. ``True`` ⇒ skip this bar."""
    return mute_prob > 0 and rng.random() < mute_prob


def evolution_multiplier(
    bar: int,
    total_bars: int,
    evolution: float,
    direction: int,
) -> float:
    """Linear ramp across a part for an ``evolution`` energy curve.

    Maps bar position ``[0, total_bars-1]`` onto a multiplier in
    ``[1 - evolution, 1 + evolution]``. ``direction=1`` ramps up across
    the part; ``-1`` ramps down; ``0`` (or ``evolution=0``) returns
    ``1.0``. Callers typically pick the direction once per part-instance
    via ``ctx.rng.choice([-1, 1])`` and reuse it for every bar.
    """
    if evolution <= 0 or direction == 0 or total_bars <= 1:
        return 1.0
    frac = bar / (total_bars - 1)
    return 1.0 + direction * evolution * (2 * frac - 1)


def pick_evolution_direction(rng: random.Random, evolution: float) -> int:
    """Returns +1 or -1 per part-instance, or 0 if evolution is disabled."""
    if evolution <= 0:
        return 0
    return rng.choice([-1, 1])


def apply_gate_jitter(base_dur: int, jitter: float, rng: random.Random) -> int:
    """Issue #1 — apply ±``jitter`` random variation to a note duration.

    ``jitter=0`` returns *base_dur* unchanged. ``jitter=0.3`` means each
    note rolls a duration in roughly ``[base_dur*0.7, base_dur*1.3]``.
    Result is clamped to at least 1 tick.
    """
    if jitter <= 0:
        return base_dur
    factor = 1.0 + rng.uniform(-jitter, jitter)
    return max(1, int(round(base_dur * factor)))


def transposed_pitch(pitch: int, ctx_transpose: int) -> int:
    """Apply the part-instance transposition to a single MIDI pitch.

    Issue #10. Clamps the result to ``[0, 127]`` — out-of-range notes
    are pushed back into range by octaves rather than dropped, so a
    high-register lead transposed +7 doesn't disappear.
    """
    if ctx_transpose == 0:
        return pitch
    result = pitch + ctx_transpose
    while result > 127:
        result -= 12
    while result < 0:
        result += 12
    return result


def maybe_octave_jump(
    pitch: int,
    octave_jump: float,
    rng: random.Random,
) -> int:
    """Issue #3 — with probability *octave_jump*, shift *pitch* by ±12.

    Result is clamped back into ``[0, 127]`` by further octave shifts
    in the appropriate direction.
    """
    if octave_jump <= 0 or rng.random() >= octave_jump:
        return pitch
    delta = 12 * rng.choice([-1, 1])
    result = pitch + delta
    while result > 127:
        result -= 12
    while result < 0:
        result += 12
    return result


# Issue #20: roles that should trigger fill / transition behaviour in
# drums and candy gens regardless of bar position.
TRANSITION_ROLES: frozenset[str] = frozenset({"transition", "fill"})


def is_transition_part(ctx) -> bool:
    """True if this part is a 1-2 bar transition fill (issue #20)."""
    return ctx.role in TRANSITION_ROLES


def maybe_passing_tone(pitch: int, passing_tones: float, rng: random.Random) -> int:
    """Issue #4 — with probability *passing_tones*, replace *pitch*
    with a chromatic neighbour (±1 semitone).

    Result is clamped to ``[0, 127]`` by reversing direction if needed.
    """
    if passing_tones <= 0 or rng.random() >= passing_tones:
        return pitch
    direction = rng.choice([-1, 1])
    new_pitch = pitch + direction
    if new_pitch < 0 or new_pitch > 127:
        new_pitch = pitch - direction
    return new_pitch


def call_response_active(
    self_handle: str,
    pair_handle: str | None,
    bar: int,
    window_bars: int = 2,
) -> bool:
    """Issue #13 — for two gens sharing a call-and-response pair, decide
    whether *this* gen plays in the current bar.

    Convention: both gens set ``pair=other_handle`` on their gen line.
    The alphabetically-first handle plays the even windows; the other
    plays the odd. ``window_bars`` defaults to 2 (the classic call-and-
    response cadence).

    If ``pair_handle`` is ``None``, returns True (no pairing in play).
    """
    if not pair_handle:
        return True
    sorted_handles = sorted([self_handle, pair_handle])
    am_first = (self_handle == sorted_handles[0])
    window = bar // window_bars
    even_window = (window % 2 == 0)
    return even_window if am_first else (not even_window)


def voice_lead(
    prev_pitches: list[int],
    next_chord_pitches: list[int],
) -> list[int]:
    """Issue #6 — return ``next_chord_pitches`` re-voiced so each note
    is the nearest octave equivalent to the corresponding *prev_pitches*.

    For each previous pitch, search the next chord's note set (extended
    with ±1 and ±2 octave shifts) and pick the closest. Result preserves
    the relative order of the prev voicing.

    On the first chord (no previous pitches), returns the unchanged
    *next_chord_pitches* — voice leading has nothing to lead from.
    """
    if not prev_pitches:
        return list(next_chord_pitches)
    # Build the candidate pool: each chord tone at octave shifts -24..+24.
    pool: list[int] = []
    for p in next_chord_pitches:
        for shift in (-24, -12, 0, 12, 24):
            candidate = p + shift
            if 0 <= candidate <= 127:
                pool.append(candidate)
    voiced: list[int] = []
    for pp in prev_pitches:
        nearest = min(pool, key=lambda c: abs(c - pp))
        voiced.append(nearest)
    return voiced


class MotifMemory:
    """Issue #11 — sliding-window degree memory for melody gens.

    Stores the last ``N`` scale degrees the gen played. ``pick_next()``
    rolls a coin weighted by the memory depth: with high probability
    when ``N`` is large, returns a degree from history; otherwise asks
    the caller's ``fresh_pick`` callable for a brand-new degree.

    Memory size 0 disables the mechanism — :meth:`pick_next` always
    delegates to ``fresh_pick``.
    """

    def __init__(self, size: int) -> None:
        self.size = max(0, size)
        self._history: list[int] = []

    def pick_next(self, rng: random.Random, fresh_pick) -> int:
        """Pick the next degree. ``fresh_pick(rng) -> int`` is the
        zero-memory fallback."""
        # Re-use probability scales with memory size: size=4 → 40%,
        # size=8 → 80%, capped at 90%.
        if self.size > 0 and self._history:
            reuse_prob = min(0.9, self.size * 0.1)
            if rng.random() < reuse_prob:
                deg = rng.choice(self._history)
                self._record(deg)
                return deg
        deg = fresh_pick(rng)
        self._record(deg)
        return deg

    def _record(self, deg: int) -> None:
        if self.size <= 0:
            return
        self._history.append(deg)
        if len(self._history) > self.size:
            self._history.pop(0)


# --------------------------------------------------------------------------
# Sidechain ducking envelope (kick-on-each-beat assumption)
# --------------------------------------------------------------------------

def sidechain_envelope(tick_in_bar: int, ppq: int, duck: float = 0.5) -> float:
    """Velocity multiplier in ``[duck, 1.0]`` for a tick inside a bar.

    Assumes 4-on-the-floor kicks land on every quarter beat (the
    overwhelming default in techno-derived styles). At each beat the
    multiplier is ``duck`` (so ``duck=0.5`` means "halve velocity on
    the downbeat"); it ramps linearly back to ``1.0`` by the midpoint
    of the beat. ``duck=1.0`` disables the envelope.
    """
    if duck >= 1.0:
        return 1.0
    pos = tick_in_bar % ppq
    half_beat = ppq // 2
    if pos >= half_beat or half_beat == 0:
        return 1.0
    return duck + (1.0 - duck) * (pos / half_beat)


def euclid(pulses: int, steps: int = STEPS_PER_BAR, offset: int = 0) -> list[bool]:
    """Linear Bjorklund-style Euclidean rhythm.

    Returns a list of *steps* booleans with *pulses* ``True`` values
    distributed as evenly as possible. The first pulse is rotated to
    position 0 (so ``euclid(4, 16)`` gives 4-on-the-floor without
    needing a manual offset), then *offset* shifts the result right.

    >>> euclid(4, 16)
    [True, False, False, False, True, False, False, False, True, False, False, False, True, False, False, False]
    >>> sum(euclid(7, 16))
    7
    >>> euclid(0, 16) == [False] * 16
    True
    >>> euclid(16, 16) == [True] * 16
    True
    """
    if pulses <= 0:
        return [False] * steps
    if pulses >= steps:
        return [True] * steps
    pattern: list[bool] = []
    bucket = 0
    for _ in range(steps):
        bucket += pulses
        if bucket >= steps:
            bucket -= steps
            pattern.append(True)
        else:
            pattern.append(False)
    # Rotate so the first True lands at position 0.
    first = pattern.index(True)
    pattern = pattern[first:] + pattern[:first]
    if offset:
        offset %= steps
        pattern = pattern[-offset:] + pattern[:-offset]
    return pattern


# --------------------------------------------------------------------------
# Tick / step conversions
# --------------------------------------------------------------------------

def step_to_ticks(step: int, ppq: int, steps_per_bar: int = STEPS_PER_BAR) -> int:
    """Convert a 0..15 step index in a bar to a tick offset within that bar."""
    ticks_per_bar = 4 * ppq  # 4/4 only in v1
    return step * ticks_per_bar // steps_per_bar


def bar_to_ticks(bar: int, ppq: int) -> int:
    """Tick offset to the *start* of *bar* (0-indexed) within a part."""
    return bar * 4 * ppq


def step_duration(ppq: int, steps_per_bar: int = STEPS_PER_BAR) -> int:
    """Ticks per 16th-note step at the given PPQ."""
    return 4 * ppq // steps_per_bar


# --------------------------------------------------------------------------
# Chord progressions
# --------------------------------------------------------------------------

# Chord progressions expressed as scale degrees (0-indexed). i = 0, ii = 1,
# … vii = 6. Names use lowercase for minor, uppercase for major, matching
# the convention of relative-roman-numeral notation in minor keys.
PROGRESSIONS: dict[str, list[int]] = {
    # The Arduino default: minor i, VI (relative major), ii, IV
    "i-VI-ii-IV": [0, 5, 1, 3],
    # Deep techno: slow modal swap
    "i-iv":       [0, 3],
    # Psytrance: skeletal modal back-and-forth
    "i-v":        [0, 4],
    # Vaporwave: classic descending minor — bass walks 1, b7, b6, 5
    "i-VII-VI-V": [0, 6, 5, 4],
    # Jazz / standards: the ii-V-I cadence (degrees in a major key)
    "ii-V-I":     [1, 4, 0],
    # The pop progression (I, V, vi, IV) — most-used in modern music
    "I-V-vi-IV":  [0, 4, 5, 3],
    # 12-bar blues — 12 chords spanning 12 bars (one per bar). Used
    # with bars_per_chord=1 for the canonical feel.
    "12-bar":     [0, 0, 0, 0, 3, 3, 0, 0, 4, 3, 0, 0],
    # Andalusian cadence (different voicing of i-VII-VI-V; widely used
    # in flamenco / metal). Kept distinct so users can pick by name.
    "andalusian": [0, 6, 5, 4],
}


@dataclass(frozen=True)
class ChordProgression:
    """Names a progression + how many bars each chord lasts.

    ``degree_at_bar(b)`` returns the scale degree of the chord active in
    bar ``b``, wrapping around the progression's length.
    """

    name: str
    bars_per_chord: int = 4

    def __post_init__(self) -> None:
        if self.name not in PROGRESSIONS:
            raise ValueError(
                f"unknown chord progression {self.name!r} "
                f"(known: {sorted(PROGRESSIONS)})"
            )

    @property
    def degrees(self) -> list[int]:
        return PROGRESSIONS[self.name]

    def degree_at_bar(self, bar: int) -> int:
        slot = (bar // self.bars_per_chord) % len(self.degrees)
        return self.degrees[slot]


# --------------------------------------------------------------------------
# Chord voicings
# --------------------------------------------------------------------------

# Named voicings, expressed as scale-degree offsets from the chord root.
# 0 = chord root in scale, 1 = next-scale-step up (= 2nd of the chord),
# 2 = next-scale-step (= 3rd), etc. Because we pull degrees from the
# part's scale (minor / dorian / phrygian / etc.), the same offset
# produces mode-appropriate intervals automatically — `triad` over a
# dorian chord root will use the dorian 3rd, which is minor; over a
# major-key chord root it'd be major.
VOICINGS: dict[str, tuple[int, ...]] = {
    "triad":   (0, 2, 4),         # 1-3-5 — the basic triad
    "seventh": (0, 2, 4, 6),      # 1-3-5-7 — m7 / maj7 depending on scale
    "ninth":   (0, 2, 4, 6, 8),   # 1-3-5-7-9 — extended
    "sus2":    (0, 1, 4),         # 1-2-5 — suspended-2nd (open, dreamy)
    "sus4":    (0, 3, 4),         # 1-4-5 — suspended-4th (tense)
    "shell":   (0, 6),            # 1-7 — jazz "shell" voicing (no 3rd)
    "power":   (0, 4),            # 1-5 — rock power chord
    "open":    (0, 4, 7),         # 1-5-1(oct) — quartal / Mc McLaughlin
}


def _resolve_voicing_offsets(voicing: str) -> tuple[int, ...]:
    """Look up *voicing* in :data:`VOICINGS`, falling back to ``triad``
    for unknown names rather than raising — keeps a typo in a DSL knob
    from killing playback."""
    return VOICINGS.get(voicing, VOICINGS["triad"])


def build_chord(
    chord_root_deg: int,
    *,
    tonic: int,
    scale: str,
    base_octave: int,
    voicing: str = "triad",
    inversion: int = 0,
    transpose: int = 0,
) -> list[int]:
    """Build the MIDI pitches of one chord per the given voicing.

    Parameters
    ----------
    chord_root_deg:
        Scale degree of the chord root (0 = i, 5 = vi, etc.).
    tonic, scale:
        Used by ``scale_note`` to translate scale degrees into MIDI
        pitches. *scale* is the part's scale name (``minor``,
        ``dorian``, ``phrygian``, ...).
    base_octave:
        Octave the chord root sits in. Inversions raise individual
        chord tones an octave above this.
    voicing:
        Name from :data:`VOICINGS` (``triad`` / ``seventh`` / ``ninth``
        / ``sus2`` / ``sus4`` / ``shell`` / ``power`` / ``open``).
        Unknown names fall back to ``triad``.
    inversion:
        Number of chord tones to lift an octave (0 = root position).
        Clamped to ``len(voicing) - 1``.
    transpose:
        Per-arrangement semitone offset (forwarded to
        :func:`transposed_pitch`).

    Returns
    -------
    List of MIDI pitches in ascending order, filtered to 0..127.
    """
    # Local imports keep _shared.py independent of theory at module-
    # load time (theory imports back).
    from slackbeatz.theory.scales import scale_note

    offsets = list(_resolve_voicing_offsets(voicing))
    inversion = max(0, min(inversion, len(offsets) - 1))

    pitches: list[int] = []
    for i, off in enumerate(offsets):
        # Lift the first ``inversion`` tones up one octave.
        oct_bump = 1 if i < inversion else 0
        pitch = scale_note(chord_root_deg + off, tonic, scale, base_octave + oct_bump)
        pitch = transposed_pitch(pitch, transpose)
        if 0 <= pitch <= 127:
            pitches.append(pitch)
    return sorted(pitches)


# --------------------------------------------------------------------------
# Groove templates — per-step tick offsets that give a bar its "feel".
# --------------------------------------------------------------------------

# Each entry maps a step index (0..15 in a 4/4 16-step bar) to a tick
# offset relative to the literal grid position. At PPQ=480, a 16th
# note is 120 ticks; an offset of 60 = halfway between two 16ths.
# Negative values rush ahead; positive values lay back.
#
# "linear" — no offset, hits land exactly on the grid (slackbeatz's
#            historical default).
# "shuffle" — triplet swing: every odd 16th pushes ~1/2 of a 16th
#             later, producing the dotted-feel of jazz / blues /
#             early house.
# "dilla" — subtle pushes on beats 2 and 4 only; the J Dilla / MPC
#           "drunken" feel that drags slightly behind the beat where
#           the snare lives but stays tight elsewhere.
# "trap16" — late offbeat 16ths in pairs (steps 2+3 and 10+11), the
#            modern trap / drill micro-timing where double-time hats
#            pull behind the beat.
# "behind" — only beats 2 and 4 (the backbeat) pull late, giving a
#            laid-back rock / soul groove with everything else tight.
# "rush" — everything pushed marginally early; the "frantic" punk /
#          hardcore feel.
GROOVES: dict[str, tuple[int, ...]] = {
    "linear":  (0,) * 16,
    "shuffle": (0, 60, 0, 60, 0, 60, 0, 60, 0, 60, 0, 60, 0, 60, 0, 60),
    "dilla":   (0, 0, 0, 0, 15, 0, 0, 0, 0, 0, 0, 0, 12, 0, 0, 0),
    "trap16":  (0, 0, 30, 30, 0, 0, 30, 30, 0, 0, 30, 30, 0, 0, 30, 30),
    "behind":  (0, 0, 0, 0, 20, 20, 0, 0, 0, 0, 0, 0, 20, 20, 0, 0),
    "rush":    (-5,) * 16,
}


def groove_offset(groove_name: str, step: int) -> int:
    """Return the tick offset for *step* under the named groove. Falls
    back to ``linear`` (no offset) for unknown names — keeps a typo in
    a DSL knob from killing playback."""
    table = GROOVES.get(groove_name, GROOVES["linear"])
    if 0 <= step < len(table):
        return table[step]
    return 0


# --------------------------------------------------------------------------
# Musical-context modulators: tension-aware velocity + phrase lift + live
# humanity. Reused by chord / bass / melody gens.
# --------------------------------------------------------------------------

# Per-scale-degree tension factor for tension_dyn. 0.0 = tonic (most
# settled, no boost), 1.0 = dominant (V, the most tense). Pre-dominant
# (IV / ii) and other intermediate chords sit in between. The shape
# tracks the standard function-harmony arc: i → IV/iv → V → i.
_DEGREE_TENSION: dict[int, float] = {
    0: 0.0,   # i — home, tonic
    1: 0.5,   # ii — pre-dominant
    2: 0.4,   # iii — secondary
    3: 0.6,   # IV/iv — pre-dominant
    4: 1.0,   # V — dominant, most tense
    5: 0.3,   # vi/VI — relative
    6: 0.7,   # vii — leading-tone (resolves up)
}


def degree_tension(degree: int) -> float:
    """Look up the tension factor for a chord-root scale degree.
    Values in 0..1; 0 = settled, 1 = maximum harmonic tension."""
    return _DEGREE_TENSION.get(degree % 7, 0.0)


def tension_velocity_boost(degree: int, tension_dyn: float, base_vel: int) -> int:
    """Return a velocity *delta* (signed) appropriate for the chord
    function. With ``tension_dyn=0`` always returns 0; with =1 the V
    chord boosts by up to ~25% of base_vel, the tonic stays flat.

    Caller adds the delta to its computed velocity then clamps to
    1..127.
    """
    if tension_dyn <= 0:
        return 0
    factor = degree_tension(degree) * tension_dyn
    # Max boost roughly +25% at full tension_dyn × dominant chord.
    return int(round(factor * base_vel * 0.25))


_DRUM_NAMES = ("kick", "snare", "clap", "hats", "hat", "hh", "ohat", "rim", "bd", "sd")


def drum_pattern_lookup(handle: str, defaults: dict) -> tuple[int, int]:
    """Resolve a (pulses, offset) tuple for a drum gen's handle.

    Multi-style songs need unique handles per gen (you can't declare
    two gens both called ``kick``), so handles like ``kick_eu`` or
    ``snare_lofi`` get used. Bare-name lookup fails on those, so we
    fall back to a substring match: any handle that *contains* a known
    drum name uses that drum's pattern.

    Exact match still wins. Substring matches are tried in
    longest-name-first order so ``ohat`` matches before ``hat`` and
    ``snare`` before ``sd``.

    Falls back to ``(4, 0)`` (4-on-floor) if nothing matches —
    matches the historical behaviour.
    """
    name = handle.lower()
    if name in defaults:
        return defaults[name]
    # Longest-first ordering so e.g. "snare_eu" doesn't match the
    # 2-char "sd" alias before the 5-char "snare".
    for known in sorted(_DRUM_NAMES, key=len, reverse=True):
        if known in name:
            return defaults.get(known, (4, 0))
    return (4, 0)


def drum_vel_lookup(handle: str, vels: dict, fallback: int = 100) -> int:
    """Same substring-fallback logic for _DEFAULT_VEL tables."""
    name = handle.lower()
    if name in vels:
        return vels[name]
    for known in sorted(_DRUM_NAMES, key=len, reverse=True):
        if known in name:
            return vels.get(known, fallback)
    return fallback


def chord_velocity_mods(
    bar: int,
    chord_root_deg: int,
    base_vel: int,
    gen,
) -> int:
    """Combined velocity delta from phrase_lift + tension_dyn knobs on a
    chord gen. Returns 0 if neither knob is set. Caller adds to its
    computed velocity then clamps to 1..127."""
    from slackbeatz.generators.defaults import (
        phrase_lift_for, tension_dyn_for,
    )
    phrase_lift = phrase_lift_for(gen)
    tension_dyn = tension_dyn_for(gen)
    delta = 0
    if phrase_lift > 0 and bar % phrase_lift == 0:
        delta += 8
    if tension_dyn > 0:
        delta += tension_velocity_boost(chord_root_deg, tension_dyn, base_vel)
    return delta


def maybe_emit_drop_sweep(ctx, channel: int, gen):
    """Yield drop-sweep CC events if drop_intensity > 0 and the next
    part has role=drop. Convenience wrapper so each chord gen needs
    only one ``yield from`` at the end of generate()."""
    from slackbeatz.generators.defaults import drop_intensity_for

    drop_intensity = drop_intensity_for(gen)
    if drop_intensity > 0 and ctx.next_role == "drop":
        yield from drop_sweep_events(ctx, channel, drop_intensity)


def melody_phrase_bump(bar: int, gen) -> int:
    """Velocity delta for melody gens — just the phrase_lift bump
    (mistakes is applied separately via apply_mistake on the final
    pitch/tick/vel tuple). Returns 0 when phrase_lift is unset."""
    from slackbeatz.generators.defaults import phrase_lift_for

    phrase_lift = phrase_lift_for(gen)
    if phrase_lift > 0 and bar % phrase_lift == 0:
        return 8
    return 0


def drop_sweep_events(
    ctx,
    channel: int,
    drop_intensity: float,
    *,
    bars_of_sweep: int = 4,
):
    """Yield a coordinated CC ramp across the last *bars_of_sweep*
    bars of the current part — used when the next part is a drop.

    Three CCs are ramped together for the "arrival" feel:

    * CC 74 (filter cutoff): 60 → up to 127 (opens the filter)
    * CC 91 (reverb send):   40 → 40 + 40*drop_intensity (wetter)
    * CC 7  (volume):        100 → 127 (louder)

    16 CC events spread evenly across the sweep window. Caller is
    responsible for only calling this when ``ctx.next_role`` indicates
    a drop is coming and ``drop_intensity > 0``.
    """
    from slackbeatz.engine.event import CC

    total_ticks = ctx.bars * ctx.ticks_per_bar
    sweep_ticks = bars_of_sweep * ctx.ticks_per_bar
    sweep_start = max(0, total_ticks - sweep_ticks)

    n_events = 16
    for i in range(n_events):
        frac = i / max(1, n_events - 1)
        tick = sweep_start + int(sweep_ticks * frac)
        cc74 = int(60 + (50 + 17 * drop_intensity) * frac)
        cc91 = int(40 + 40 * drop_intensity * frac)
        cc7 = int(100 + 27 * frac)
        yield CC(tick=tick, channel=channel, controller=74, value=max(0, min(127, cc74)))
        yield CC(tick=tick, channel=channel, controller=91, value=max(0, min(127, cc91)))
        yield CC(tick=tick, channel=channel, controller=7, value=max(0, min(127, cc7)))


def apply_mistake(
    pitch: int,
    tick: int,
    velocity: int,
    mistakes: float,
    rng,
) -> tuple[int, int, int]:
    """One in a few hundred notes, a real player flubs slightly. This
    helper rolls *mistakes* (a probability 0..0.1 — keep it small) and
    randomly perturbs one of pitch / tick / velocity. Returns a possibly-
    modified (pitch, tick, velocity) tuple."""
    if mistakes <= 0 or rng.random() >= mistakes:
        return pitch, tick, velocity
    # Pick which dimension to mess with.
    which = rng.choice(("pitch", "tick", "velocity"))
    if which == "pitch":
        pitch = max(0, min(127, pitch + rng.choice((-1, 1))))
    elif which == "tick":
        tick = max(0, tick + rng.randint(-10, 10))
    else:
        velocity = max(1, min(127, velocity + rng.choice((-15, 15))))
    return pitch, tick, velocity


# --------------------------------------------------------------------------
# Walking-bass helper
# --------------------------------------------------------------------------

def walking_step_pitch(current_root: int, next_root: int) -> int:
    """Pick a single chromatic step approaching *next_root* from
    *current_root*. Used to insert one walking note halfway through a
    chord whose successor chord has a different root.

    * If next_root is higher: walks up — return next_root - 1.
    * If next_root is lower: walks down — return next_root + 1.
    * If next_root == current_root: stays on current_root (no walk).

    Both pitches are MIDI note numbers. Caller is responsible for
    placing this note rhythmically (typically on the last beat /
    8th / 16th of the chord before the change).
    """
    if next_root > current_root:
        return next_root - 1
    if next_root < current_root:
        return next_root + 1
    return current_root


# --------------------------------------------------------------------------
# Drum fills
# --------------------------------------------------------------------------

def fill_perturb(
    base_pulses: int,
    rng: random.Random,
    bump: int = 2,
    cap: int = STEPS_PER_BAR,
) -> int:
    """Return a pulse count for a fill bar: *base_pulses* plus a small
    upward perturbation.

    ``cap`` clamps the result so a 16-step pattern doesn't degenerate to
    all-pulses.
    """
    return min(cap, base_pulses + rng.randint(1, max(1, bump)))


def is_fill_bar(bar: int, group: int = 4) -> bool:
    """Is *bar* (0-indexed) the last bar of a *group*-bar group?"""
    return (bar % group) == (group - 1)


_BUILD_ROLES = frozenset({"build", "buildup", "transition", "fill"})


def is_build_part(ctx: "PartContext") -> bool:
    """True if this part should swell into the next — either its role
    is build-shaped, transitional (issue #20), or it sits directly
    before a drop."""
    return ctx.role in _BUILD_ROLES or ctx.next_role == "drop"


def expression_ramp(
    ctx: "PartContext",
    channel: int,
    *,
    start: int = 70,
    end: int = 127,
    events_per_bar: int = 4,
    bars_to_ramp: int | None = None,
) -> Iterator["CC"]:
    """Yield CC 11 (Expression) events ramping ``start`` → ``end`` over
    the last ``bars_to_ramp`` bars (defaults to *all* bars of the part).

    Algorithms call this only when :func:`is_build_part` returns True,
    so the expression curve sells the build → drop transition. The
    final value persists into the next part (no snap-back).
    """
    # Local import to avoid the cycle the TYPE_CHECKING guard documents.
    from slackbeatz.engine.event import CC

    ticks_per_bar = 4 * ctx.ppq
    total_ticks = ctx.bars * ticks_per_bar
    ramp_bars = min(ctx.bars, bars_to_ramp) if bars_to_ramp else ctx.bars
    ramp_start = total_ticks - ramp_bars * ticks_per_bar
    n = max(2, ramp_bars * events_per_bar)
    span = total_ticks - ramp_start
    step = max(1, span // (n - 1))
    for i in range(n):
        tick = ramp_start + i * step
        if tick >= total_ticks:
            tick = total_ticks - 1
        frac = i / (n - 1)
        value = int(round(start + (end - start) * frac))
        yield CC(
            tick=max(0, tick), channel=channel, controller=11,
            value=max(0, min(127, value)),
        )
