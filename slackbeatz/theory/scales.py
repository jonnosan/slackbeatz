"""Scales as lists of semitone offsets from the tonic.

Each entry is a 7-note diatonic or modal scale (or shorter for
pentatonics). Algorithms call :func:`scale_note` to turn a degree into
a MIDI pitch in a given octave.

Octave numbering follows MIDI convention: MIDI 60 = C4 (middle C), so
the formula is ``pitch_class + 12 * (octave + 1)``.
"""

from __future__ import annotations

# Semitone offsets from the tonic for each scale. Sorted ascending.
SCALES: dict[str, list[int]] = {
    "major":            [0, 2, 4, 5, 7, 9, 11],
    "minor":            [0, 2, 3, 5, 7, 8, 10],  # natural minor
    "dorian":           [0, 2, 3, 5, 7, 9, 10],
    "phrygian":         [0, 1, 3, 5, 7, 8, 10],
    "harmonic_minor":   [0, 2, 3, 5, 7, 8, 11],
    "minor_pentatonic": [0, 3, 5, 7, 10],
    "major_pentatonic": [0, 2, 4, 7, 9],
}


def midi_note(pitch_class: int, octave: int) -> int:
    """Return the MIDI note number for a pitch class at an octave.

    Pitch class ranges over 0..11 (C..B); octave 4 = middle-C octave.
    """
    note = pitch_class + 12 * (octave + 1)
    if not 0 <= note <= 127:
        raise ValueError(f"midi_note: pc={pitch_class} oct={octave} → {note} out of 0..127")
    return note


def scale_note(
    degree: int,
    tonic: int,
    scale_name: str,
    octave: int = 4,
) -> int:
    """MIDI pitch for a scale *degree* (0-indexed) of *scale_name* in
    *tonic*'s key, starting at *octave*.

    Degrees beyond the scale length wrap and bump the octave.
    """
    intervals = SCALES[scale_name]
    n = len(intervals)
    octave_off, deg = divmod(degree, n)
    return midi_note(tonic, octave + octave_off) + intervals[deg]


def scale_degree(midi: int, tonic: int, scale_name: str) -> int | None:
    """Inverse of :func:`scale_note` — return the scale degree (any
    octave) of a MIDI pitch, or ``None`` if it's not in the scale."""
    intervals = set(SCALES[scale_name])
    delta = (midi - tonic) % 12
    return delta if delta in intervals else None
