"""Default drum-name → MIDI-note maps for the bundled kit presets.

Modern hardware drum machines (TR-8, TR-8S, Drumbrute, software emulations
in Ableton / Logic / Reason …) overwhelmingly follow General MIDI's
percussion mapping. The `808` and `909` presets in v1 are deliberately the
same map as `gm` — when users have device-specific quirks they're best
expressed as per-kit overrides in their setup file, not baked into a
preset name. The presets exist as named anchors so songs can say
``preset=909`` and the resolver picks up the GM-derived defaults.

Drum names recognised by every preset:

    kick snare clap ltom hat phat mtom ohat htom crash ride

A custom kit can introduce its own names by overriding them in the
setup; algorithms ignore unknown names rather than failing, so an
arbitrary kit map of (name → note) works fine for non-techno styles.
"""

from __future__ import annotations

# General MIDI Percussion (channel 10) baseline.
GM: dict[str, int] = {
    "kick":  36,  # Bass Drum 1
    "snare": 38,  # Acoustic Snare
    "clap":  39,  # Hand Clap
    "ltom":  41,  # Low Floor Tom
    "hat":   42,  # Closed Hi-Hat
    "phat":  44,  # Pedal Hi-Hat
    "mtom":  45,  # Low Tom
    "ohat":  46,  # Open Hi-Hat
    "htom":  48,  # High Tom
    "crash": 49,  # Crash Cymbal 1
    "ride":  51,  # Ride Cymbal 1
}

# v1 aliases — same map. Refined per-device through kit overrides if needed.
TR808: dict[str, int] = dict(GM)
TR909: dict[str, int] = dict(GM)


PRESETS: dict[str, dict[str, int]] = {
    "gm":  GM,
    "808": TR808,
    "909": TR909,
}


def preset_map(name: str) -> dict[str, int]:
    """Return a fresh copy of the preset's drum-name → note map.

    Raises :class:`KeyError` if *name* isn't a known preset.
    """
    return dict(PRESETS[name])
