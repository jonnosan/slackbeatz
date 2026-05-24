"""``subbass`` gen type — root-note reinforcement layer on channel 6.

Sub-bass plays at the bottom of the mix on its own channel with a
distinct patch (Surge XT's ``Basses/Sub 1.fxp`` by default), giving
each track a deep foundation that doesn't fight the main bass voice
for spectral space. The pattern varies per style — sparse drone in
ambient styles, quarter-note pulses in psytrance, octave-jump
punctuation in garage, etc.

Importing this module registers every style by side-effect."""

from __future__ import annotations

from . import (  # noqa: F401
    acid,
    deep_techno,
    drum_and_bass,
    dub_techno,
    euclid,
    garage,
    lofi,
    psytrance,
    vaporwave,
)
