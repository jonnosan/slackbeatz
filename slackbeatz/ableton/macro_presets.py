"""Per-(role, style) macro presets for the AbletonOSC push.

Each entry holds a ``base`` vector of 8 macro values plus an optional
``variance`` dict that lets some macros jitter per-song so multiple
tracks in the same style sound distinct without losing the style
signature. Macros NOT in variance stay fixed at the base — that's
how the style stays recognisable across songs (e.g. acid always
keeps high resonance + short attack; only cutoff / drive / FX vary).

All values are 0..1. The user's rack wiring decides what 0 and 1
actually mean for each underlying parameter — the contract is the
macro NAME (cutoff / resonance / etc), not the absolute value
range, see :data:`slackbeatz.ableton.MACRO_NAMES`.

Coverage today: acid + deep_techno. Other styles fall through to
:data:`DEFAULT_PRESET` until the user adds entries.

Per-song variance is moderate (~25-30% of the 0..1 range) — clearly
distinct per song, but the style signature dominates.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import MACRO_NAMES


@dataclass(frozen=True)
class MacroPreset:
    """Base macro values + per-macro variance for one (role, style)."""

    # Indexed by MACRO_NAMES order. Length must be 8.
    base: tuple[float, float, float, float, float, float, float, float]
    # Per-macro variance — only macros in this dict jitter per song.
    # Value is the +/- range from base (e.g. 0.15 = ±15% around base).
    variance: dict[str, float] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Style: acid (Phuture / Aphex Twin TB-303-inspired)
# Bass + lead get the 303 squelch character. Pad/candy/sub stay subtle
# so the bass IS the song (matches the composer's acid style choices).
# ----------------------------------------------------------------------
_ACID = {
    # role: MacroPreset(base[cutoff, reso, atk, rel, drive, fx, char, glide])
    "bass":  MacroPreset(
        base=(0.55, 0.85, 0.02, 0.20, 0.65, 0.25, 0.40, 0.50),
        variance={"cutoff": 0.25, "drive": 0.20, "fx": 0.20, "release": 0.10},
    ),
    "lead":  MacroPreset(
        base=(0.70, 0.70, 0.05, 0.30, 0.50, 0.35, 0.45, 0.30),
        variance={"cutoff": 0.20, "drive": 0.20, "fx": 0.25, "character": 0.20},
    ),
    "pad":   MacroPreset(
        base=(0.40, 0.30, 0.45, 0.65, 0.25, 0.55, 0.50, 0.10),
        variance={"cutoff": 0.20, "fx": 0.20, "character": 0.15},
    ),
    "candy": MacroPreset(
        base=(0.75, 0.50, 0.10, 0.40, 0.40, 0.60, 0.55, 0.30),
        variance={"cutoff": 0.20, "fx": 0.25, "character": 0.25},
    ),
    "sub":   MacroPreset(
        base=(0.30, 0.10, 0.08, 0.55, 0.30, 0.10, 0.20, 0.00),
        variance={"cutoff": 0.15, "drive": 0.15, "release": 0.15},
    ),
}


# ----------------------------------------------------------------------
# Style: deep_techno (Robert Hood / Basic Channel / Maurizio)
# Restrained — low cutoffs, smooth envelopes, generous dub FX.
# Modal melodic gestures (sparse lead) over a sub-anchored bass.
# ----------------------------------------------------------------------
_DEEP_TECHNO = {
    "bass":  MacroPreset(
        base=(0.40, 0.20, 0.10, 0.45, 0.30, 0.35, 0.30, 0.10),
        variance={"cutoff": 0.20, "fx": 0.20, "release": 0.15},
    ),
    "lead":  MacroPreset(
        base=(0.55, 0.30, 0.20, 0.55, 0.25, 0.55, 0.40, 0.15),
        variance={"cutoff": 0.20, "fx": 0.25, "release": 0.15, "character": 0.15},
    ),
    "pad":   MacroPreset(
        base=(0.35, 0.20, 0.60, 0.75, 0.20, 0.70, 0.50, 0.05),
        variance={"cutoff": 0.15, "fx": 0.20, "release": 0.15},
    ),
    "candy": MacroPreset(
        base=(0.50, 0.25, 0.40, 0.50, 0.20, 0.65, 0.45, 0.20),
        variance={"cutoff": 0.20, "fx": 0.25, "character": 0.20},
    ),
    "sub":   MacroPreset(
        base=(0.25, 0.05, 0.15, 0.70, 0.20, 0.15, 0.20, 0.00),
        variance={"cutoff": 0.15, "release": 0.15},
    ),
}


# Per-style preset tables, keyed by SB style name.
_PRESETS_BY_STYLE: dict[str, dict[str, MacroPreset]] = {
    "acid":        _ACID,
    "deep_techno": _DEEP_TECHNO,
}


# Catch-all when the song's style isn't in the registry yet.
# Neutral mid values — sounds OK on most racks; user can iterate
# the registry as they encounter new styles.
DEFAULT_PRESET = MacroPreset(
    base=(0.50, 0.30, 0.20, 0.40, 0.30, 0.30, 0.50, 0.20),
    variance={"cutoff": 0.20, "fx": 0.15, "character": 0.15},
)


def preset_for(role: str, style: str | None) -> MacroPreset | None:
    """Return the preset for *(role, style)*, or ``None`` if not covered.

    ``None`` style returns ``None`` so the caller can decide whether to
    fall back to :data:`DEFAULT_PRESET` or skip the role entirely.
    """
    if style is None:
        return None
    table = _PRESETS_BY_STYLE.get(style)
    if table is None:
        return None
    return table.get(role)


def known_styles() -> tuple[str, ...]:
    """Styles that have at least one role defined."""
    return tuple(sorted(_PRESETS_BY_STYLE))


def apply_variance(
    preset: MacroPreset, *, song_seed: int, role: str,
) -> tuple[float, ...]:
    """Apply per-song variance to *preset.base* using a deterministic seed.

    Each macro in ``preset.variance`` jitters by ±variance[macro] around
    the base value. Macros not in variance stay fixed. The same
    ``(song_seed, role)`` always produces the same vector — re-pressing
    the Setup button for the same song reproduces the same sound.

    Returns a length-8 tuple of 0..1 floats matching :data:`MACRO_NAMES`
    order.
    """
    # Hash the seed+role+macro_name so each macro gets its own
    # deterministic offset in [-1, +1] before scaling by variance.
    out: list[float] = []
    for i, name in enumerate(MACRO_NAMES):
        v = preset.base[i]
        amount = preset.variance.get(name)
        if amount is not None and amount > 0:
            h = hashlib.sha256(
                f"{song_seed}|{role}|{name}".encode("utf-8")
            ).digest()
            # First 8 bytes → unsigned int → normalised to [-1, +1].
            raw = int.from_bytes(h[:8], "big")
            normalised = (raw / (1 << 64)) * 2.0 - 1.0
            v = v + normalised * amount
        out.append(max(0.0, min(1.0, v)))
    return tuple(out)
