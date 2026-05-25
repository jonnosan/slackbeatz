"""``candy sh101_top`` — high-register sequenced layer above the lead.

Reference: DMX Krew and the wider Berlin-school / Rephlex sound where
a primary SH-101 / MS-10 lead plays the melody while a SECOND analogue
mono-synth sequences a faster, brighter pattern above it. The two
lines lock to the same chord progression but operate at different
rhythmic densities — the lower one is the melody, the higher one is
the "molten" propulsive layer.

Algorithmically identical to
:class:`slackbeatz.generators.melody.sh101_arp.MelodySh101Arp` —
re-registered as a ``candy`` type so it routes to the candy channel
(ch 4 in the bundled surge setup) and can have its own Surge preset
giving it brighter / sparkle character. Same knobs apply
(``pitches``, ``pulses``, ``steps``, ``gate``, ``progression``,
``bars_per_chord``).

Typical knob choice in warm_analogue is ``pulses=11 steps=16`` for
~11 hits per bar (denser than the lead's 5 hits per 2 bars), and
``octave=1`` or higher so it sits above the lead in register.
"""

from __future__ import annotations

from slackbeatz.generators.melody.sh101_arp import MelodySh101Arp
from slackbeatz.generators.registry import register_generator


@register_generator("candy", "sh101_top")
class CandySh101Top(MelodySh101Arp):
    """High-register sequencer line for the warm_analogue style.
    Same behaviour as ``melody:sh101_arp`` (chord-following, fixed
    pitch sequence, euclidean trigger pattern, per-note filter
    envelope on the Surge side) — registered under a different
    ``(type, algorithm)`` key so the Surge preset table can map it
    to a brighter / smaller patch on the candy channel.
    """
