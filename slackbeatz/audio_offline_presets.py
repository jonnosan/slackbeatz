"""Hand-tuned Surge XT VST3 parameter presets — bypass for the broken
.fxp load path in dawdreamer.

## Why this module exists

`dawdreamer.PluginProcessor.load_preset(path)` accepts Surge XT's
`.fxp` factory-patch files without raising, but **does not apply
them** — the synth stays at its bare init state (Filter Type: Off,
default oscillator, no FX). Every "audio --setup surge" render the
codebase has produced prior to this module was actually Surge's init
patch, not the per-(role, style) patch the `_STYLE_PATCH_FOR_ROLE`
lookup intended.

The root cause: `.fxp` is the VST 2.x preset format. VST3 hosts (which
dawdreamer is) want `.vstpreset` files. Surge's own runtime
(`surge-xt-cli`) knows how to load `.fxp` internally — that's why the
live `--surge` path sounds right — but the dawdreamer-driven offline
path can't bridge that gap.

This module provides hand-crafted parameter presets via
`set_parameter` calls. Less faithful to the original `.fxp` patches
than a real load, but at least the output has a recognisable
character (filter on, sensible envelopes, etc) instead of being a
naked oscillator.

## Per-preset shape

Each preset is a tuple of `(param_name, normalised_value)` pairs.
Parameter names match Surge XT VST3's parameter dump (case-sensitive,
e.g. `"A Filter 1 Cutoff"`). Normalised values are 0.0–1.0; the
human-readable text (`"587.33 Hz"`, `"LP Legacy Ladder"`) is what
Surge resolves the normalised value into via its parameter mapping.

Empirically-derived normalised → display mappings (probed via
`synth.get_plugin_parameters_description()`):

* **Filter 1 Type**: 0.0=Off, 0.1=LP Legacy Ladder, 0.2=N 12 dB,
  0.3=LP Vintage Ladder, 0.4=LP K35, 0.5=HP Cutoff Warp, etc.
* **Filter 1 Cutoff**: 0.0=8 Hz, 0.3=130 Hz, 0.5=587 Hz, 0.7=2.3 kHz,
  1.0=20 kHz (log scale).
* **Filter 1 Resonance**: 0.0=0%, 0.5=50%, 1.0=100%.

## CC automation

The static presets here set the base patch character. Dynamic
modulation (filter sweeps, etc.) comes from
`audio_offline._cc_to_automation` which translates the rendered
MIDI's CC74 / CC71 stream into a buffer-aligned array passed to
`synth.set_automation`.
"""

from __future__ import annotations


# (role, style) → list of (param_name, normalised_value) pairs.
#
# Only the (role, style) combos that have authored presets here use
# this path. Others fall back to the `.fxp` load attempt (which
# silently fails and yields Surge's init state — same as before this
# module landed).
ROLE_STYLE_PRESETS: dict[tuple[str, str], tuple[tuple[str, float], ...]] = {
    # ----- acid bass: classic 303 squelch -----
    ("bass", "acid_303"): (
        # Filter — classic ladder, high resonance, env opens it on each note
        ("A Filter 1 Type", 0.1),            # LP Legacy Ladder
        ("A Filter 1 Cutoff", 0.30),         # ~130 Hz starting cutoff (mostly closed)
        ("A Filter 1 Resonance", 0.75),      # squelchy
        ("A Filter 1 FEG Mod Amount", 0.65), # envelope opens filter dramatically
        ("A Filter 1 Keytrack", 0.55),       # cutoff tracks pitch
        # Filter EG — snappy
        ("A Filter EG Attack", 0.0),
        ("A Filter EG Decay", 0.30),
        ("A Filter EG Sustain", 0.20),
        ("A Filter EG Release", 0.15),
        # Filter 2 — off (single-filter character)
        ("A Filter 2 Type", 0.0),
    ),
    # ----- acid chord stab (LEGACY): kept so manual .sb files using
    # chords:acid_stab still render correctly. The acid style profile
    # in compose.py dropped this gen in iteration 1.6 — bass + lead
    # interplay carry the song now.
    ("pad", "acid_stab"): (
        ("A Filter 1 Type", 0.1),            # LP Legacy Ladder
        ("A Filter 1 Cutoff", 0.40),         # slightly more open than bass
        ("A Filter 1 Resonance", 0.65),
        ("A Filter 1 FEG Mod Amount", 0.55),
        ("A Filter 1 Keytrack", 0.30),
        ("A Filter EG Attack", 0.0),
        ("A Filter EG Decay", 0.20),         # quick decay → snappy stab
        ("A Filter EG Sustain", 0.05),       # near-zero sustain
        ("A Filter EG Release", 0.10),
        ("A Filter 2 Type", 0.0),
    ),
    # ----- acid lead (iteration 1.6): sequenced melodic punctuation -----
    # The 303-flavoured lead that interleaves with the bass. More open
    # filter than the bass (lead sits higher in the mix) with a
    # punchy filter envelope per note for that "blooming squeal"
    # character. Slightly less resonance than the bass — the lead
    # should sing, not screech.
    ("lead", "acid_lead"): (
        ("A Filter 1 Type", 0.1),            # LP Legacy Ladder
        ("A Filter 1 Cutoff", 0.50),         # ~600 Hz — more open than bass
        ("A Filter 1 Resonance", 0.55),      # mid resonance — sings
        ("A Filter 1 FEG Mod Amount", 0.70), # strong env per note
        ("A Filter 1 Keytrack", 0.45),
        ("A Filter EG Attack", 0.0),
        ("A Filter EG Decay", 0.25),
        ("A Filter EG Sustain", 0.30),       # some sustain so the note rings
        ("A Filter EG Release", 0.20),
        ("A Filter 2 Type", 0.0),
    ),
    # ----- acid candy/sweep: noise-y riser texture -----
    # The candy channel runs `acid_sweep` which emits a short noise
    # burst + CC ramp on build sections. Configure for an aggressive
    # high-resonance sound that the CC ramp can drive dramatically.
    ("candy", "acid_sweep"): (
        ("A Filter 1 Type", 0.1),            # LP Legacy Ladder
        ("A Filter 1 Cutoff", 0.25),
        ("A Filter 1 Resonance", 0.80),      # extra-squelchy for the sweep peak
        ("A Filter 1 FEG Mod Amount", 0.40),
        ("A Filter 1 Keytrack", 0.20),
        ("A Filter EG Attack", 0.05),
        ("A Filter EG Decay", 0.40),
        ("A Filter EG Sustain", 0.30),
        ("A Filter EG Release", 0.30),
        ("A Filter 2 Type", 0.0),
    ),
}


def apply_preset(synth, role: str, style: str) -> bool:
    """Apply the (role, style) preset to *synth* if one exists.

    Returns True if a preset was applied; False if none is registered
    for the pair (caller falls back to the `.fxp` load attempt, which
    is broken but harmless).

    *synth* is a `dawdreamer.PluginProcessor` with the Surge XT VST3
    loaded.
    """
    key = (role, style)
    if key not in ROLE_STYLE_PRESETS:
        return False
    name_to_idx = {
        p["name"]: p["index"]
        for p in synth.get_plugin_parameters_description()
    }
    for name, value in ROLE_STYLE_PRESETS[key]:
        idx = name_to_idx.get(name)
        if idx is None:
            # Surge param renamed / removed; skip silently — the rest
            # of the preset still applies.
            continue
        synth.set_parameter(idx, value)
    return True


# Parameter names that the per-CC automation layer drives. Only used
# when extracting CC events from the rendered MIDI — see
# `audio_offline._cc_to_automation`.
CC_TO_PARAM_NAME: dict[int, str] = {
    74: "A Filter 1 Cutoff",      # MIDI standard: "Brightness"
    71: "A Filter 1 Resonance",   # MIDI standard: "Timbre / Harmonic Content"
    1:  "A LFO 1 Rate",            # mod wheel — used by some leads
    7:  "A Volume",                # channel volume
    91: "A FX2 Mix",               # reverb send → master FX2 wet
}
