"""``bass warm_sub`` — warm analogue sub bass for the warm_analogue style.

Reference: DMX Krew's bass character on the Breakin Records output
("SH101 Triggers MS10", "Molten Analogue", "Honeydew") — MS-10 /
SH-101-style monosynth bass, sub-focused, smoother and less squelchy
than a TB-303. Sits underneath a melodic sequenced lead rather than
being the focal element.

Same step pattern as :mod:`slackbeatz.generators.bass.rolling`
(euclidean 8 pulses in 16 steps) — DMX Krew's basslines are also
straight 1/8th pulses with occasional fifth / octave variation. The
algorithmic structure is shared. The character difference lives in:

* The Surge preset (audio_offline_presets.py:("bass","warm_sub")) —
  warmer filter (Sub Comb / LP K35), less resonance, slower envelope.
* The patch lookup (surge_host.py:_STYLE_PATCH_FOR_ROLE) — "Smoothie"
  or similar mellow patch.
* Default knob values tuned for sustain rather than punch.
"""

from __future__ import annotations

from slackbeatz.generators.bass.rolling import BassRolling
from slackbeatz.generators.registry import register_generator


@register_generator("bass", "warm_sub")
class BassWarmSub(BassRolling):
    """Algorithmically identical to ``BassRolling`` — this class
    exists so the Surge preset table can key on a different
    algorithm name and load a warmer patch / preset for the
    warm_analogue style. See module docstring for the rationale.
    """
