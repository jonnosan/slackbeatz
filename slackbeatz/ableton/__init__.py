"""Ableton integration — AbletonOSC client + macro-preset registry.

In ``ableton`` mode the user builds one Live Set (``Slackbeatz.als``)
with an Instrument Rack on every role-track. Each rack exposes 8
macros mapped to whatever internal parameters the user wires up,
following the shared **macro contract** (see :data:`MACRO_NAMES`):

    Macro 1  Cutoff      — filter brightness
    Macro 2  Resonance   — filter peak
    Macro 3  Attack      — envelope start
    Macro 4  Release     — envelope tail
    Macro 5  Drive       — saturation / distortion
    Macro 6  FX send     — delay + reverb wet
    Macro 7  Character   — detune / osc shape / wavetable position
    Macro 8  Glide/Mod   — portamento or LFO depth

SB sends macro values 0..1 via AbletonOSC. The actual sound depends
on how the user wired the rack — SB doesn't know or care whether the
inside is Analog, Wavetable, Bass Station, or a chain of plugins.

The (role, style) preset registry (:mod:`.macro_presets`) holds base
values + per-macro variance. The "Set Ableton patches for style"
button on the Setup screen pushes the values to the appropriate
tracks, with per-song-seed variance applied so two acid tracks each
have a unique filter / drive / attack character within the acid
signature.
"""

from __future__ import annotations

# Macro names in fixed order — index 0 = Macro 1 in Ableton's UI.
# The order is the contract; renaming or reordering breaks every
# user's existing rack wiring.
MACRO_NAMES: tuple[str, ...] = (
    "cutoff", "resonance", "attack", "release",
    "drive", "fx", "character", "glide",
)
