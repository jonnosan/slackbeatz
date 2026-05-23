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
    ("bass", "euclid"):       95,
    ("bass", "deep_techno"):  80,
    ("bass", "psytrance"):   105,
    ("bass", "vaporwave"):    75,
    ("bass", "acid"):        105,
    ("bass", "dub_techno"):   70,   # sustained drone, soft
    ("bass", "drum_and_bass"): 100,  # punchy sub-bass
    ("bass", "garage"):       105,   # punchy sub

    # melody
    ("melody", "euclid"):       90,
    ("melody", "deep_techno"):  75,
    ("melody", "psytrance"):    88,
    ("melody", "vaporwave"):    75,
    ("melody", "acid"):         85,
    ("melody", "dub_techno"):   65,   # near-silent
    ("melody", "drum_and_bass"): 78,
    ("melody", "garage"):        92,   # vocal-stab punch
    # chords
    ("chords", "euclid"):       85,
    ("chords", "deep_techno"):  70,
    ("chords", "psytrance"):    75,
    ("chords", "vaporwave"):    70,
    ("chords", "acid"):         78,
    ("chords", "dub_techno"):   95,   # the chord stab is the centerpiece — punch
    ("chords", "drum_and_bass"): 78,  # lush pads
    ("chords", "garage"):        90,  # punchy stabs
}

# Octave offset (added to the style's natural register).
STYLE_BASE_OCTAVE: dict[tuple[str, str], int] = {
    ("bass", "euclid"):      -1,
    ("bass", "deep_techno"): -1,
    ("bass", "psytrance"):    0,    # A2 / E2 (82-110 Hz) — rolling gallop sits in midrange, audible on laptop speakers
    ("bass", "vaporwave"):   -1,
    ("bass", "acid"):         0,   # TB-303 sits high for the lead-bass feel
    ("bass", "dub_techno"): -1,
    ("bass", "drum_and_bass"): -1,    # A1 (55 Hz) — classic DnB Reese register
    ("bass", "garage"):       -1,
    ("melody", "euclid"):       0,
    ("melody", "deep_techno"):  0,
    ("melody", "psytrance"):    1,
    ("melody", "vaporwave"):    1,
    ("melody", "acid"):         0,
    ("melody", "dub_techno"):   1,
    ("melody", "drum_and_bass"): 1,
    ("melody", "garage"):        0,
    ("chords", "euclid"):       0,
    ("chords", "deep_techno"):  0,
    ("chords", "psytrance"):    0,
    ("chords", "vaporwave"):    0,
    ("chords", "acid"):         0,
    ("chords", "dub_techno"):   0,
    ("chords", "drum_and_bass"): 0,
    ("chords", "garage"):       0,
}

# Note-length ratio (1.0 = full step length; lower = staccato).
STYLE_GATE: dict[tuple[str, str], float] = {
    ("bass", "euclid"):      0.85,
    ("bass", "deep_techno"): 0.90,
    ("bass", "psytrance"):   0.30,   # short pumps for the gallop
    ("bass", "vaporwave"):   0.90,
    ("bass", "acid"):        0.55,   # mid — the 303 envelope is per-note
    ("bass", "dub_techno"):  0.98,   # sustained drone, long
    ("bass", "drum_and_bass"): 0.95,
    ("bass", "garage"):       0.55,
    ("melody", "euclid"):       0.60,
    ("melody", "deep_techno"):  0.95,
    ("melody", "psytrance"):    0.50,
    ("melody", "vaporwave"):    0.85,
    ("melody", "acid"):         0.40,
    ("melody", "dub_techno"):   0.90,
    ("melody", "drum_and_bass"): 0.75,
    ("melody", "garage"):       0.25,   # short stabs
    ("chords", "euclid"):       0.95,
    ("chords", "deep_techno"):  0.98,
    ("chords", "psytrance"):    0.90,
    ("chords", "vaporwave"):    0.96,
    ("chords", "acid"):         0.30,   # short organ stabs in acid house
    ("chords", "dub_techno"):   0.18,   # signature short stab — punch then fade
    ("chords", "drum_and_bass"): 0.92,  # sustained pad
    ("chords", "garage"):       0.30,   # short stab
}

# Sidechain ducking depth on bass gens. 1.0 = off.
BASS_DUCK: dict[str, float] = {
    "euclid":      0.55,
    "deep_techno": 0.70,
    "psytrance":   0.45,
    "vaporwave":   1.00,
    "acid":        0.50,
    "dub_techno":  0.75,   # gentle duck — bass is more drone than punch
    "drum_and_bass": 0.80,
    "garage":        0.55,  # noticeable pump
}

# Per-style velocity jitter range (±N) for rhythm/drums humanisation.
STYLE_VEL_JITTER: dict[str, int] = {
    "euclid":      8,
    "deep_techno": 5,
    "psytrance":   6,
    "vaporwave":   4,
    "acid":        4,   # acid is tight
    "dub_techno":  4,   # dub techno wants smooth dynamics
    "drum_and_bass": 7,
    "garage":      6,
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
    ("melody", "euclid"):       "minor",
    ("melody", "deep_techno"):  "dorian",
    ("melody", "psytrance"):    "phrygian",
    ("melody", "vaporwave"):    "dorian",
    ("melody", "acid"):         "minor",
    ("melody", "dub_techno"):   "dorian",
    ("melody", "drum_and_bass"): "dorian",
    ("melody", "garage"):       "minor_pentatonic",
    ("chords", "euclid"):       "minor",
    ("chords", "deep_techno"):  "minor",  # chord roots are scale degrees, not modes
    ("chords", "psytrance"):    "phrygian",
    ("chords", "vaporwave"):    "minor",
    ("chords", "acid"):         "minor",
    ("chords", "dub_techno"):   "dorian",
    ("chords", "drum_and_bass"): "dorian",
    ("chords", "garage"):       "minor",
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
