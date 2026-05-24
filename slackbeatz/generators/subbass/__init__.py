"""``subbass`` gen type — root-note reinforcement layer on channel 6.

Sub-bass plays at the bottom of the mix on its own channel with a
distinct patch (Surge XT's ``Basses/Sub 1.fxp`` by default), giving
each track a deep foundation that doesn't fight the main bass voice
for spectral space. The pattern varies per style — sparse drone in
ambient styles, quarter-note pulses in psytrance, octave-jump
punctuation in garage, etc.

The 9 per-style files this used to ship were collapsed in #49 to
two parameterised algorithms:

* :mod:`.drone` — long sustained notes every N bars; covers
  acid / deep_techno / drum_and_bass / dub_techno / lofi /
  vaporwave (each picking ``bars_per_note=`` + optional
  ``alternate_fifth=`` to suit).
* :mod:`.pulse` — short hits at named step positions
  (``pattern=quarter|kick_beats|euclid_3_16``); covers euclid /
  garage / psytrance.

Importing this module registers both algorithms by side-effect."""

from __future__ import annotations

from . import drone, pulse  # noqa: F401
