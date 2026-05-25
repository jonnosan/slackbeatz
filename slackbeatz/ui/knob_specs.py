"""Knob metadata for the drilldown's smart-controller dispatch.

The drilldown shows one row per knob. We want the right widget for
the knob's value space:

* **Bool** → ``Checkbutton`` (voice_lead, emit_clock-style on/off).
* **Enum** → ``Combobox`` with the allowed choices (voicing,
  progression, fill_style, direction, ...).
* **Numeric range** → ``Scale`` slider with low/high/step (swing,
  gate, density, walking, ...).
* **Unknown** → falls back to a text-entry dialog (existing
  ``_open_knob_editor`` path) — the safety net for knobs no
  generator surfaces in :data:`KNOB_SPECS` yet.

Each spec is a :class:`KnobSpec` tuple. The drilldown maps the
gen's ``type_`` + the knob name to the right spec; type-specific
overrides (e.g. ``progression`` choices that differ between bass
and chords) win over the global default.

Specs live here rather than next to each generator because the GUI
needs ONE place to look — generator modules don't currently export
machine-readable knob metadata. Adding new entries: just append to
:data:`KNOB_SPECS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class KnobSpec:
    """Describes one knob's value space for UI dispatch.

    * ``kind`` — ``"bool"`` / ``"enum"`` / ``"float"`` / ``"int"``.
    * ``low`` / ``high`` / ``step`` — required for float/int.
    * ``choices`` — required for enum.
    * ``default`` — fallback when no override + no song-level value
      is set. Match the generator's read-side fallback so the UI
      shows the same value the engine would use.
    """

    kind: str
    low: float | int | None = None
    high: float | int | None = None
    step: float | int | None = None
    choices: Sequence[str] | None = None
    default: object = None


# Named progressions accepted by ``bass_progression_for`` (defaults.py).
_PROGRESSIONS = (
    "(none)", "i-iv", "i-v", "i-VII-VI-V", "i-VI-ii-IV",
    "I-V-vi-IV", "12-bar", "andalusian",
)
_VOICINGS = (
    "triad", "seventh", "sus2", "sus4", "open", "power", "ninth", "shell",
)
_FILL_STYLES = ("snare_roll", "tom_roll", "kick_double", "silence")
_HAT_VARIANTS = ("(default)", "open", "pedal")
_GROOVES = ("(none)", "shuffle", "dilla", "trap16", "swing16")
_SCALES = (
    "(none)", "major", "minor", "dorian", "phrygian",
    "minor_pentatonic", "major_pentatonic", "blues",
    "harmonic_minor", "lydian", "mixolydian",
)
_FILL_FX_TYPES = ("(none)", "delay", "reverb", "chorus", "phaser")
_DIRECTIONS = ("up", "down", "updown", "random")
_RATES = ("8", "12", "16")  # candy/traditional_arp step rates


# Global knob specs — keyed by knob name. Type-specific overrides
# below win when the gen.type_ matches. Keep alphabetised.
KNOB_SPECS: dict[str, KnobSpec] = {
    # Per-event probabilities / floats 0..1.
    "accent":          KnobSpec("int",   low=0, high=16, step=1, default=0),
    "arp_period":      KnobSpec("int",   low=0, high=16, step=1, default=2),
    "arp_prob":        KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "bars_per_chord":  KnobSpec("int",   low=1, high=16, step=1, default=4),
    "base_octave":     KnobSpec("int",   low=-3, high=3, step=1, default=0),
    "base_vel":        KnobSpec("int",   low=1, high=127, step=1, default=90),
    "bend":            KnobSpec("int",   low=0, high=200, step=10, default=80),
    "burble_prob":     KnobSpec("float", low=0.0, high=1.0, step=0.02, default=0.0),
    "cycle":           KnobSpec("int",   low=0, high=16, step=1, default=2),
    "density":         KnobSpec("float", low=0.0, high=1.0, step=0.05, default=1.0),
    "density_drift":   KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "direction":       KnobSpec("enum",  choices=_DIRECTIONS, default="up"),
    "drop_intensity":  KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "drop_prob":       KnobSpec("float", low=0.0, high=1.0, step=0.02, default=0.0),
    "duck":            KnobSpec("float", low=0.0, high=1.0, step=0.05, default=1.0),
    "evolution":       KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "fifth_prob":      KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.25),
    "fill_every":      KnobSpec("int",   low=0, high=16, step=1, default=4),
    "fill_style":      KnobSpec("enum",  choices=_FILL_STYLES, default="snare_roll"),
    "gate":            KnobSpec("float", low=0.0, high=1.5, step=0.05, default=0.85),
    "gate_jitter":     KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "ghost":           KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "ghost_vel":       KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.3),
    "groove":          KnobSpec("enum",  choices=_GROOVES, default="(none)"),
    "hat_variant":     KnobSpec("enum",  choices=_HAT_VARIANTS, default="(default)"),
    "humanize":        KnobSpec("int",   low=0, high=20, step=1, default=0),
    "intensity":       KnobSpec("float", low=0.0, high=1.0, step=0.05, default=1.0),
    "interval":        KnobSpec("int",   low=-12, high=12, step=1, default=0),
    "inversion":       KnobSpec("int",   low=0, high=3, step=1, default=0),
    "kick_env":        KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "modwheel":        KnobSpec("int",   low=0, high=127, step=1, default=0),
    "motif_memory":    KnobSpec("int",   low=0, high=16, step=1, default=0),
    "mistakes":        KnobSpec("float", low=0.0, high=1.0, step=0.02, default=0.0),
    "mute_prob":       KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "octave":          KnobSpec("int",   low=-3, high=3, step=1, default=0),
    "octave_jump":     KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "octave_range":    KnobSpec("int",   low=1, high=3, step=1, default=1),
    "pan":             KnobSpec("int",   low=0, high=127, step=1, default=64),
    "passing_tones":   KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "phrase_lift":     KnobSpec("int",   low=0, high=40, step=1, default=0),
    "pickup":          KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "polyrhythm":      KnobSpec("int",   low=0, high=16, step=1, default=0),
    "progression":     KnobSpec("enum",  choices=_PROGRESSIONS, default="(none)"),
    "pulses":          KnobSpec("int",   low=1, high=32, step=1, default=5),
    "rate":            KnobSpec("enum",  choices=_RATES, default="16"),
    "resonance":       KnobSpec("int",   low=0, high=127, step=1, default=64),
    "reverb":          KnobSpec("int",   low=0, high=127, step=1, default=64),
    "scale":           KnobSpec("enum",  choices=_SCALES, default="(none)"),
    "slide_prob":      KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "steps":           KnobSpec("int",   low=1, high=32, step=1, default=16),
    "stutter":         KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "swing":           KnobSpec("float", low=0.0, high=0.5, step=0.02, default=0.0),
    "tension_dyn":     KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "third_prob":      KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
    "velocity":        KnobSpec("int",   low=1, high=127, step=1, default=100),
    "voice_lead":      KnobSpec("bool",  default=False),
    "voicing":         KnobSpec("enum",  choices=_VOICINGS, default="triad"),
    "walking":         KnobSpec("float", low=0.0, high=1.0, step=0.05, default=0.0),
}


def get_knob_spec(knob_name: str, gen_type: str | None = None) -> KnobSpec | None:
    """Return the spec for *knob_name*, or None if unknown.

    *gen_type* is reserved for future per-type spec overrides
    (e.g. ``progression`` choices differ between melody + chords)
    — today the global table wins regardless.
    """
    return KNOB_SPECS.get(knob_name)
