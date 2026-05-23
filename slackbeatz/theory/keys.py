"""Key string parsing — ``"Am"`` / ``"C"`` / ``"F#m"`` → ``(tonic, scale)``.

Convention used in the DSL:

* Trailing ``"m"`` means natural minor (``Am`` = A minor).
* No suffix means major (``C`` = C major).
* Sharps / flats use ``#`` / ``b`` (``F#m``, ``Bbm``, ``C#``).

For modal styles (deep_techno uses dorian, psytrance uses phrygian),
algorithms ignore the parsed scale and pick their own — the key string
still gives them the tonic.
"""

from __future__ import annotations

NOTE_TO_PITCH_CLASS: dict[str, int] = {
    "C": 0, "C#": 1, "Db": 1,
    "D": 2, "D#": 3, "Eb": 3,
    "E": 4,
    "F": 5, "F#": 6, "Gb": 6,
    "G": 7, "G#": 8, "Ab": 8,
    "A": 9, "A#": 10, "Bb": 10,
    "B": 11,
}


class KeyError_(ValueError):
    """Raised when a key string can't be parsed."""


def parse_key(key_str: str) -> tuple[int, str]:
    """Return ``(tonic_pitch_class, scale_name)`` for a DSL key string.

    ``scale_name`` is always either ``"minor"`` or ``"major"``; modal
    algorithms read the tonic and pick their own mode.
    """
    if not key_str:
        raise KeyError_("empty key string")
    if key_str.endswith("m") and key_str != "Em":
        # Em parses as 'E' + 'm' for E minor; same for any other "_m"
        tonic_str = key_str[:-1]
        scale = "minor"
    elif key_str.endswith("m"):
        tonic_str = key_str[:-1]
        scale = "minor"
    else:
        tonic_str = key_str
        scale = "major"
    if tonic_str not in NOTE_TO_PITCH_CLASS:
        raise KeyError_(
            f"unknown key tonic {tonic_str!r} in {key_str!r} "
            f"(known: {sorted(NOTE_TO_PITCH_CLASS)})"
        )
    return NOTE_TO_PITCH_CLASS[tonic_str], scale
