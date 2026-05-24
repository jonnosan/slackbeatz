"""Per-(type, style) algorithm defaults.

Centralising the small "magic numbers" each algorithm class needs —
velocity baselines, octave offsets, gate ratios, sidechain depths,
default candy CC controllers. Each value is overridable from the DSL
via the corresponding knob on the ``gen`` line (e.g. ``base_vel=120``
to push a `bass psytrance` louder than the style default).

Adding a new style means adding a row to each of these tables (plus
writing the algorithm classes that read them). The acid style entries
illustrate the minimal data shape.

Lookup helpers below (``base_vel_for``, ``base_octave_for``, …) collapse
the "knob overrides table default" pattern into one call.
"""

from __future__ import annotations

from typing import Any

from slackbeatz.generators.base import Generator


# --------------------------------------------------------------------------
# Data tables — keyed by (type, style). Algorithms look up their own row.
# --------------------------------------------------------------------------

# Velocity baseline before per-hit jitter / intensity scaling.
STYLE_BASE_VEL: dict[tuple[str, str], int] = {
    # bass
    ("bass", "rolling"):       95,
    ("bass", "subdrone"):  80,
    ("bass", "gallop"):   105,
    ("bass", "mellow_pick"):    75,
    ("bass", "acid_303"):        105,
    ("bass", "sustain_drone"):   70,   # sustained drone, soft
    ("bass", "reese"): 100,  # punchy sub-bass
    ("bass", "two_step_sub"):       105,   # punchy sub
    ("bass", "acoustic_walk"):          78,   # warm walking bass, fingered upright feel

    # subbass — sits below the main bass voice on its own channel.
    # Slightly softer than the bass so it reinforces rather than
    # masks. Consolidated to per-algorithm defaults in #49; style
    # profiles override via GenSpec.knob_defaults to recover the
    # finer per-style nuance.
    ("subbass", "drone"): 75,   # sustained drone — gentle, sub-perceptual
    ("subbass", "pulse"): 95,   # pulsing hits — felt as a kick reinforcement

    # melody
    ("melody", "euclid_riff"):       90,
    ("melody", "sparse_pad_lead"):  75,
    ("melody", "psy_lead"):    88,
    ("melody", "lazy_sax"):    75,
    ("melody", "acid_stab"):         85,
    ("melody", "distant_lead"):   65,   # near-silent
    ("melody", "atmos_lead"): 78,
    ("melody", "vocal_chop"):        92,   # vocal-stab punch
    ("melody", "rhodes_phrase"):          70,   # soft Rhodes EP
    # chords
    ("chords", "triad_sustain"):       85,
    ("chords", "pad_drift"):  70,
    ("chords", "psy_swell"):    75,
    ("chords", "arp_walk"):    70,
    ("chords", "sustained_dyad"):         78,
    ("chords", "offbeat_stab"):   95,   # the chord stab is the centerpiece — punch
    ("chords", "atmos_pad"): 78,  # lush pads
    ("chords", "wurli_chop"):        90,  # punchy stabs
    ("chords", "rhodes_chord"):          78,  # warm Rhodes pad chords
}

# Octave offset (added to the style's natural register).
STYLE_BASE_OCTAVE: dict[tuple[str, str], int] = {
    ("bass", "rolling"):      -1,
    ("bass", "subdrone"): -1,
    ("bass", "gallop"):    0,    # A2 / E2 (82-110 Hz) — rolling gallop sits in midrange, audible on laptop speakers
    ("bass", "mellow_pick"):   -1,
    ("bass", "acid_303"):         0,   # TB-303 sits high for the lead-bass feel
    ("bass", "sustain_drone"): -1,
    ("bass", "reese"): -1,    # A1 (55 Hz) — classic DnB Reese register
    ("bass", "two_step_sub"):       -1,
    ("bass", "acoustic_walk"):         -1,    # A1 (55 Hz) — warm fingered bass

    # subbass sits an octave below the bass register — A0 / A1
    # territory. Below ~30 Hz human ears stop hearing pitch and only
    # feel pressure; that's the whole point of the layer.
    # Consolidated per-algorithm in #49; style profiles override via
    # GenSpec.knob_defaults when they need to nudge a sub up to A1
    # (e.g. when the main bass already covers A0).
    ("subbass", "drone"): -2,
    ("subbass", "pulse"): -1,   # pulse styles sit slightly higher so the kick room stays clear

    ("melody", "euclid_riff"):       0,
    ("melody", "sparse_pad_lead"):  0,
    ("melody", "psy_lead"):    1,
    ("melody", "lazy_sax"):    1,
    ("melody", "acid_stab"):         0,
    ("melody", "distant_lead"):   1,
    ("melody", "atmos_lead"): 1,
    ("melody", "vocal_chop"):        0,
    ("melody", "rhodes_phrase"):          0,    # Rhodes mid-register C4-C5


    ("chords", "triad_sustain"):       0,
    ("chords", "pad_drift"):  0,
    ("chords", "psy_swell"):    0,
    ("chords", "arp_walk"):    0,
    ("chords", "sustained_dyad"):         0,
    ("chords", "offbeat_stab"):   0,
    ("chords", "atmos_pad"): 0,
    ("chords", "wurli_chop"):       0,
    ("chords", "rhodes_chord"):         0,
}

# Note-length ratio (1.0 = full step length; lower = staccato).
STYLE_GATE: dict[tuple[str, str], float] = {
    ("bass", "rolling"):      0.85,
    ("bass", "subdrone"): 0.90,
    ("bass", "gallop"):   0.30,   # short pumps for the gallop
    ("bass", "mellow_pick"):   0.90,
    ("bass", "acid_303"):        0.55,   # mid — the 303 envelope is per-note
    ("bass", "sustain_drone"):  0.98,   # sustained drone, long
    ("bass", "reese"): 0.95,
    ("bass", "two_step_sub"):       0.55,
    ("bass", "acoustic_walk"):         0.85,   # sustained walking bass
    # subbass — drones run nearly tied (gate ≈ 1); pulse hits sit
    # mid-short so adjacent pulses read as distinct. Consolidated
    # per-algorithm in #49.
    ("subbass", "drone"): 0.95,
    ("subbass", "pulse"): 0.50,
    ("melody", "euclid_riff"):       0.60,
    ("melody", "sparse_pad_lead"):  0.95,
    ("melody", "psy_lead"):    0.50,
    ("melody", "lazy_sax"):    0.85,
    ("melody", "acid_stab"):         0.40,
    ("melody", "distant_lead"):   0.90,
    ("melody", "atmos_lead"): 0.75,
    ("melody", "vocal_chop"):       0.25,   # short stabs
    ("melody", "rhodes_phrase"):         0.85,   # long sustained Rhodes notes
    ("chords", "triad_sustain"):       0.95,
    ("chords", "pad_drift"):  0.98,
    ("chords", "psy_swell"):    0.90,
    ("chords", "arp_walk"):    0.96,
    ("chords", "sustained_dyad"):         0.30,   # short organ stabs in acid house
    ("chords", "offbeat_stab"):   0.18,   # signature short stab — punch then fade
    ("chords", "atmos_pad"): 0.92,  # sustained pad
    ("chords", "wurli_chop"):       0.30,   # short stab
    ("chords", "rhodes_chord"):         0.96,   # sustained Rhodes EP
}

# Sidechain ducking depth on bass / subbass gens — keyed by
# algorithm name (globally unique across gen types). 1.0 = off.
BASS_DUCK: dict[str, float] = {
    # bass algorithms (old style → new name): see #50.
    "rolling":         0.55,
    "subdrone":        0.70,
    "gallop":          0.45,
    "mellow_pick":     1.00,
    "acid_303":        0.50,
    "sustain_drone":   0.75,   # gentle duck — bass is more drone than punch
    "reese":           0.80,
    "two_step_sub":    0.55,   # noticeable pump
    "acoustic_walk":   1.00,   # no sidechain — lofi sits back
    # subbass algorithms (consolidated to two in #49).
    "drone":           0.95,   # drone subs don't need much pump
    "pulse":           0.45,   # pulse subs duck under each kick
}

# Velocity-jitter range (±N) for rhythm humanisation — keyed by
# rhythm algorithm name.
STYLE_VEL_JITTER: dict[str, int] = {
    "euclid_drums":      8,
    "four_floor_deep":   5,
    "gallop_kick":       6,
    "slow_kick":         4,
    "four_floor_house":  4,   # acid house is tight
    "four_floor_dub":    4,   # dub techno wants smooth dynamics
    "breakbeat":         7,
    "two_step":          6,
    "dusty_swing":       6,
}


# Per-(type, style) default scale name (issue #22). Pitched gens use
# this when no `scale=` override is set on the gen, part, or song.
# Modes are looked up by name in slackbeatz.theory.scales.SCALES.
STYLE_SCALE: dict[tuple[str, str], str] = {
    # Most styles use the standard minor; deep_techno + vaporwave + dub_techno
    # use dorian for modal flavour; psytrance uses phrygian.
    ("bass",   "euclid"):       "minor",
    ("bass",   "deep_techno"):  "dorian",
    ("bass",   "psytrance"):    "phrygian",
    ("bass",   "vaporwave"):    "minor",
    ("bass",   "acid"):         "minor",
    ("bass",   "dub_techno"):   "dorian",
    ("bass",   "drum_and_bass"): "dorian",
    ("bass",   "garage"):       "minor",
    ("bass",   "lofi"):         "dorian",
    # subbass scale — mirrors the matching bass style so root + fifth
    # land on the same notes as the main bass voice. Sub-bass that
    # plays only the root rarely needs the scale, but octave-jump /
    # fifth_prob knobs do reach for it.
    # subbass — root-note layer; scale matters only when a
    # progression= knob is set. Consolidated to minor as a neutral
    # default; per-style flavour (dorian / phrygian) flows through
    # the style profile's GenSpec.knob_defaults.
    ("subbass", "drone"): "minor",
    ("subbass", "pulse"): "minor",
    ("melody", "euclid_riff"):       "minor",
    ("melody", "sparse_pad_lead"):  "dorian",
    ("melody", "psy_lead"):    "phrygian",
    ("melody", "lazy_sax"):    "dorian",
    ("melody", "acid_stab"):         "minor",
    ("melody", "distant_lead"):   "dorian",
    ("melody", "atmos_lead"): "dorian",
    ("melody", "vocal_chop"):       "minor_pentatonic",
    ("melody", "rhodes_phrase"):         "minor_pentatonic",
    ("chords", "triad_sustain"):       "minor",
    ("chords", "pad_drift"):  "minor",  # chord roots are scale degrees, not modes
    ("chords", "psy_swell"):    "phrygian",
    ("chords", "arp_walk"):    "minor",
    ("chords", "sustained_dyad"):         "minor",
    ("chords", "offbeat_stab"):   "dorian",
    ("chords", "atmos_pad"): "dorian",
    ("chords", "wurli_chop"):       "minor",
    ("chords", "rhodes_chord"):         "minor",
}


# --------------------------------------------------------------------------
# Lookup helpers — collapse "knob > table default > fallback" into one call.
# --------------------------------------------------------------------------

def base_vel_for(gen: Generator, fallback: int = 90) -> int:
    """Resolve the velocity baseline. Knob ``base_vel=N`` wins; else look
    up ``(type, style)`` in the table; else fall back to *fallback*."""
    knob = gen.knobs.get("base_vel")
    if isinstance(knob, int):
        return knob
    return STYLE_BASE_VEL.get((gen.type_, gen.style), fallback)


def base_octave_for(gen: Generator, fallback: int = 0) -> int:
    knob = gen.knobs.get("base_octave")
    if isinstance(knob, int):
        return knob
    # Legacy `octave=N` knob still wins if explicitly set.
    legacy = gen.knobs.get("octave")
    if isinstance(legacy, int):
        return legacy
    return STYLE_BASE_OCTAVE.get((gen.type_, gen.style), fallback)


def gate_for(gen: Generator, fallback: float = 0.85) -> float:
    knob = gen.knobs.get("gate")
    if isinstance(knob, (int, float)):
        return float(knob)
    return STYLE_GATE.get((gen.type_, gen.style), fallback)


def duck_for(gen: Generator, fallback: float = 1.0) -> float:
    knob = gen.knobs.get("duck")
    if isinstance(knob, (int, float)):
        return float(knob)
    return BASS_DUCK.get(gen.style, fallback)


def vel_jitter_for(gen: Generator, fallback: int = 6) -> int:
    return STYLE_VEL_JITTER.get(gen.style, fallback)


def gate_jitter_for(gen: Generator) -> float:
    """Read the gate_jitter knob (issue #1). Defaults to 0 (no jitter)."""
    v = gen.knobs.get("gate_jitter", 0.0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def octave_jump_for(gen: Generator) -> float:
    """Read the octave_jump knob (issue #3). Defaults to 0."""
    v = gen.knobs.get("octave_jump", 0.0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def motif_memory_for(gen: Generator) -> int:
    """Read the motif_memory knob (issue #11). Defaults to 0."""
    v = gen.knobs.get("motif_memory", 0)
    return int(v) if isinstance(v, (int, float)) else 0


def passing_tones_for(gen: Generator) -> float:
    """Issue #4 — read the passing_tones knob. Defaults to 0."""
    v = gen.knobs.get("passing_tones", 0.0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def polyrhythm_for(gen: Generator) -> int:
    """Issue #12 — read the polyrhythm knob. Defaults to 0 (off)."""
    v = gen.knobs.get("polyrhythm", 0)
    return int(v) if isinstance(v, (int, float)) else 0


def voice_lead_for(gen: Generator) -> bool:
    """Issue #6 — read the voice_lead knob. Defaults to False."""
    v = gen.knobs.get("voice_lead", 0)
    if isinstance(v, bool):
        return v
    return bool(v) if isinstance(v, (int, float)) else False


def pair_for(gen: Generator) -> str | None:
    """Issue #13 — read the pair= knob (call-and-response partner)."""
    v = gen.knobs.get("pair")
    return v if isinstance(v, str) else None


# --------------------------------------------------------------------------
# Chord-progression knobs — shared by every ``chords`` generator.
# --------------------------------------------------------------------------

def progression_for(
    gen: Generator,
    *,
    default_name: str,
    default_bars: int,
):
    """Build a :class:`ChordProgression` honouring per-gen overrides.

    Knobs:

    * ``progression=NAME`` — pick a progression from
      :data:`slackbeatz.generators._shared.PROGRESSIONS`. Unknown
      names fall back to the style's default rather than erroring.
    * ``bars_per_chord=N`` — override the cadence at which the
      progression advances. Clamped to 1..32 to keep chord changes
      audible.

    Both default to whatever the chord generator passed in (i.e. the
    style's natural choice).
    """
    from slackbeatz.generators._shared import PROGRESSIONS, ChordProgression

    name = gen.knobs.get("progression")
    if not isinstance(name, str) or name not in PROGRESSIONS:
        name = default_name
    bars = gen.knobs.get("bars_per_chord")
    if not isinstance(bars, int) or bars < 1:
        bars = default_bars
    bars = max(1, min(32, bars))
    return ChordProgression(name=name, bars_per_chord=bars)


def voicing_for(gen: Generator, fallback: str) -> str:
    """Read the ``voicing=`` knob with the style's natural voicing
    as fallback. Caller passes the style's hardcoded voicing (e.g.
    ``"seventh"`` for deep_techno's min7, ``"triad"`` for euclid)."""
    v = gen.knobs.get("voicing")
    if isinstance(v, str):
        from slackbeatz.generators._shared import VOICINGS
        if v in VOICINGS:
            return v
    return fallback


def inversion_for(gen: Generator, fallback: int = 0) -> int:
    """Read the ``inversion=`` knob — 0 = root position, 1 = first
    inversion, etc. Clamped to 0..3 (no real voicing has more than
    four tones)."""
    v = gen.knobs.get("inversion")
    if isinstance(v, (int, float)):
        return max(0, min(3, int(v)))
    return fallback


# --------------------------------------------------------------------------
# Bass-specific knobs — chord-following, walking notes, pickup
# anticipations, chord-tone variety.
# --------------------------------------------------------------------------

def bass_progression_for(
    gen: Generator,
    *,
    default_name: str | None = None,
    default_bars: int = 4,
):
    """Resolve a chord progression for a bass gen — distinct from
    :func:`progression_for` because most bass styles default to "no
    progression" (just play the part's tonic). Returns a
    :class:`ChordProgression` if either:

    * the gen line sets ``progression=NAME``, or
    * *default_name* was passed (the style has its own walking
      progression like vaporwave's ``i-VII-VI-V``).

    Returns ``None`` if neither is set — caller plays the tonic.
    """
    from slackbeatz.generators._shared import PROGRESSIONS, ChordProgression

    name = gen.knobs.get("progression")
    if not isinstance(name, str) or name not in PROGRESSIONS:
        name = default_name
    if name is None:
        return None
    bars = gen.knobs.get("bars_per_chord")
    if not isinstance(bars, int) or bars < 1:
        bars = default_bars
    bars = max(1, min(32, bars))
    return ChordProgression(name=name, bars_per_chord=bars)


def walking_for(gen: Generator) -> float:
    """``walking=N`` — probability (0..1) of inserting a chromatic
    step-up note approaching each chord change. Jazz / funk walking
    bass behaviour: as the chord is about to change to a higher root,
    walk up to it from below in single semitones."""
    v = gen.knobs.get("walking", 0.0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def pickup_for(gen: Generator) -> float:
    """``pickup=N`` — probability (0..1) of inserting an 8th-note
    anticipation before a downbeat. Adds groove by hinting the next
    chord root half a beat early."""
    v = gen.knobs.get("pickup", 0.0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def fifth_prob_for(gen: Generator) -> float:
    """``fifth_prob=N`` — probability (0..1) of playing the chord 5th
    instead of root on any given bass note. 0.3 gives noticeable
    movement without losing the root-anchor; 0.5 sounds like a
    1-5-1-5 bassline; 1.0 plays only fifths."""
    v = gen.knobs.get("fifth_prob", 0.0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def third_prob_for(gen: Generator) -> float:
    """``third_prob=N`` — probability (0..1) of playing the chord 3rd.
    Use sparingly; thirds make the bass feel like a melody. Common
    values: 0.1 (occasional colour), 0.3 (jazz / funk)."""
    v = gen.knobs.get("third_prob", 0.0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def phrase_lift_for(gen: Generator) -> int:
    """``phrase_lift=N`` — boost velocity by +8 on the first bar of
    every N-bar phrase. 0 = off; 4/8/16 are typical phrase lengths."""
    v = gen.knobs.get("phrase_lift", 0)
    return int(v) if isinstance(v, (int, float)) else 0


def tension_dyn_for(gen: Generator) -> float:
    """``tension_dyn=N`` — chord-tension-aware velocity boost (0..1).
    Higher values cause notes to be louder on the V chord (dominant)
    and softer on the i chord (tonic), matching the harmonic-function
    rise-and-fall of common-practice music. Only meaningful for gens
    that follow a chord progression (chords / bass with progression=)."""
    v = gen.knobs.get("tension_dyn", 0.0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def mistakes_for(gen: Generator) -> float:
    """``mistakes=N`` — probability (0..0.1) of a per-note 'live
    mistake': pitch off by a semitone, timing off by ±10 ticks, or
    velocity off by ±15. Adds humanity without sounding broken at
    small values. Keep ≤ 0.05 unless you want a drunk-pianist effect."""
    v = gen.knobs.get("mistakes", 0.0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def drop_intensity_for(gen: Generator) -> float:
    """``drop_intensity=N`` — automated drop-sweep intensity (0..1).
    When the *next* part is a drop, emits coordinated CC sweeps
    across the current part's final 4 bars (filter cutoff opens,
    reverb send increases, volume rises) so the drop feels like an
    arrival. Default 0 = no automation."""
    v = gen.knobs.get("drop_intensity", 0.0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def stutter_for(gen: Generator) -> float:
    """``stutter=N`` — probability (0..1) of stutter retrigger on the
    last 16th of the last bar before a drop section. Retrigger emits
    4 × 32nd notes at decaying velocity. DJ-effect for free."""
    v = gen.knobs.get("stutter", 0.0)
    return float(v) if isinstance(v, (int, float)) else 0.0


def scale_for(gen, ctx, fallback: str = "minor") -> str:
    """Resolve which scale this gen should draw from (issue #22).

    Priority (most specific first):

    1. ``scale=<name>`` knob on the gen line.
    2. ``ctx.scale_override`` — set by the scheduler from the part's
       ``scale=`` knob, then the song-level ``scale <name>``.
    3. :data:`STYLE_SCALE` entry for the gen's ``(type, style)``.
    4. *fallback* (default ``"minor"``).
    """
    knob = gen.knobs.get("scale")
    if isinstance(knob, str):
        return knob
    if ctx.scale_override:
        return ctx.scale_override
    return STYLE_SCALE.get((gen.type_, gen.style), fallback)


# --------------------------------------------------------------------------
# Convenience: bundle the macro / mute / drift knob reads in one place.
# Algorithm classes hit these once at the start of generate().
# --------------------------------------------------------------------------

def macro_knobs(gen: Generator) -> dict[str, Any]:
    """Read the macro-level chance knobs in one go.

    Returns a dict with ``density_drift``, ``mute_prob``, ``evolution``,
    each defaulting to 0.0 when not set on the gen.
    """
    return {
        "density_drift": float(gen.knobs.get("density_drift", 0.0) or 0.0),
        "mute_prob":     float(gen.knobs.get("mute_prob",     0.0) or 0.0),
        "evolution":     float(gen.knobs.get("evolution",     0.0) or 0.0),
    }
