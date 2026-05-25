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
    # ----- acid bass: classic 303 squelch + delay + distortion -----
    # Iteration 1.10 — pushed for more squelch (resonance 0.85, FEG mod
    # 0.85, faster filter EG decay) and added Surge's Distortion FX in
    # series on FX A2 (OJD tube-screamer model, +9 dB drive). Reference:
    # the gnarly, saturated 303 sound on Aphex Twin's "Didgeridoo" +
    # late-80s Phuture acid where the 303 was deliberately overdriven
    # through pedals / mixer preamps.
    ("bass", "acid_303"): (
        # Filter — classic ladder, very high resonance, env opens it
        # dramatically on each note for that "squelchy bloom".
        ("A Filter 1 Type", 0.1),            # LP Legacy Ladder
        ("A Filter 1 Cutoff", 0.20),         # ~50 Hz — start almost closed
        ("A Filter 1 Resonance", 0.85),      # very squelchy / verging on self-osc
        ("A Filter 1 FEG Mod Amount", 0.85), # envelope opens filter HARD
        ("A Filter 1 Keytrack", 0.55),       # cutoff tracks pitch
        # Filter EG — very snappy, classic acid envelope shape
        ("A Filter EG Attack", 0.0),
        ("A Filter EG Decay", 0.20),         # ~30 ms decay → sharp attack
        ("A Filter EG Sustain", 0.15),       # quick fall-off
        ("A Filter EG Release", 0.12),
        ("A Filter 2 Type", 0.0),            # single-filter character
        # FX A1 — dotted-1/8 delay (the 303 line ghosts onto its offbeat)
        ("FX A1 FX Type", 0.0251),
        ("FX A1 Delay Time - Left", 0.5),
        ("FX A1 Delay Time - Right", 0.5),
        ("FX A1 Feedback/EQ - Feedback", 0.45),
        ("FX A1 Feedback/EQ - Crossfeed", 0.20),
        ("FX A1 Feedback/EQ - High Cut", 0.65),
        ("FX A1 Output - Mix", 0.30),
        # FX A2 — distortion (in series after delay). OJD tube-
        # screamer model gives a warm acid-pedal flavour rather than
        # harsh fuzz; Drive 0.7 ≈ +9 dB; Output gain -4 dB to keep
        # the master sum in range.
        ("FX A2 FX Type", 0.1590),                 # Distortion
        ("FX A2 Distortion - Drive", 0.70),        # ~+9 dB drive
        ("FX A2 Distortion - Model", 0.727),       # OJD (Tube Screamer)
        ("FX A2 Distortion - Feedback", 0.10),     # subtle feedback edge
        ("FX A2 Pre-EQ - Frequency", 0.55),        # let mids through
        ("FX A2 Pre-EQ - High Cut", 1.0),          # don't pre-shave the high
        ("FX A2 Post-EQ - Frequency", 0.55),
        ("FX A2 Post-EQ - High Cut", 0.85),        # tame the harshest highs
        ("FX A2 Output - Gain", 0.42),             # -4 dB make-up
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
    # Superseded by sh101_arp in iteration 1.7 but kept for hand-
    # written .sb compatibility.
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
    # ----- warm_sub (warm_analogue style): MS-10-style sub bass -----
    # Reference: DMX Krew's MS-10 bass tone on the Breakin Records
    # output. Sub-focused, smoother + warmer than the acid 303 —
    # LP K35 filter (rounder than the ladder), much lower resonance
    # (no squelch peaking), longer envelope (sustain rather than
    # plucky), gentle saturation for that "valve" warmth without
    # explicit distortion. No delay (the lead carries the wet
    # effect in this style).
    ("bass", "warm_sub"): (
        ("A Filter 1 Type", 0.4),            # LP K35 — round / smooth
        ("A Filter 1 Cutoff", 0.40),         # ~350 Hz — open enough to hear sub + lower mid
        ("A Filter 1 Resonance", 0.25),      # very mild — no squelch
        ("A Filter 1 FEG Mod Amount", 0.35), # gentle envelope motion
        ("A Filter 1 Keytrack", 0.65),       # tracks pitch — keeps low notes warm
        ("A Filter EG Attack", 0.0),
        ("A Filter EG Decay", 0.50),         # longer decay — sustained warmth
        ("A Filter EG Sustain", 0.50),
        ("A Filter EG Release", 0.30),
        ("A Filter 2 Type", 0.0),
        # FX A1 — light tape saturation for analogue warmth.
        # Tape FX (norm 0.7949) is Surge's analogue-modelled tape
        # saturation — adds gentle harmonic content + slight
        # compression without explicit distortion.
        ("FX A1 FX Type", 0.7949),           # Tape
        ("FX A1 Output - Mix", 0.55),        # 55% wet — present but the dry stays dominant
    ),
    # ----- sh101_top (iteration 1.12): bright top-arp layer -----
    # The "molten" fast sequencer line above the lead. Brighter than
    # the lead so it sparkles in the high-mid rather than competing
    # for the same lead-cutting band. Tape FX for warmth coherence
    # with the rest of the warm_analogue mix.
    ("candy", "sh101_top"): (
        ("A Filter 1 Type", 0.4),            # LP K35 (same family as lead)
        ("A Filter 1 Cutoff", 0.65),         # brighter — sits above the lead
        ("A Filter 1 Resonance", 0.30),
        ("A Filter 1 FEG Mod Amount", 0.50),
        ("A Filter 1 Keytrack", 0.40),
        ("A Filter EG Attack", 0.0),
        ("A Filter EG Decay", 0.25),
        ("A Filter EG Sustain", 0.10),       # short decay — sparkle, not pad
        ("A Filter EG Release", 0.15),
        ("A Filter 2 Type", 0.0),
        ("FX A1 FX Type", 0.7949),           # Tape (same as bass/lead)
        ("FX A1 Output - Mix", 0.45),
    ),
    # ----- sh101_arp (iteration 1.11): warm SH-101 character -----
    # 1.7: cutoff 0.55. 1.8: bumped to 0.70 (acid context — needed to
    # cut through). 1.11: pulled back to 0.50 + lower resonance + LP
    # K35 filter (smoother than ladder) + tape FX matching the bass
    # warmth, so the lead sits IN the bass+drums mix rather than
    # cutting on top of it. User feedback was "feels bolted on" —
    # this aims for a more cohesive warm-analogue blend. The acid
    # arc no longer uses this preset (acid is pure-303 now).
    ("lead", "sh101_arp"): (
        ("A Filter 1 Type", 0.4),            # LP K35 — round / smooth (matches bass)
        ("A Filter 1 Cutoff", 0.50),         # mid — sits above the bass without screech
        ("A Filter 1 Resonance", 0.35),      # restrained — no acid squelch
        ("A Filter 1 FEG Mod Amount", 0.40), # moderate env per note (was 0.75)
        ("A Filter 1 Keytrack", 0.50),
        ("A Filter EG Attack", 0.0),
        ("A Filter EG Decay", 0.40),         # longer decay → notes sing
        ("A Filter EG Sustain", 0.35),
        ("A Filter EG Release", 0.30),
        ("A Filter 2 Type", 0.0),
        # FX A1 — Tape saturation (same as bass) for tonal cohesion.
        # No delay here — the bass already carries the wet effect
        # under it; adding delay to the lead too muddies the mix.
        ("FX A1 FX Type", 0.7949),           # Tape
        ("FX A1 Output - Mix", 0.50),
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


# --------------------------------------------------------------------------
# Patch variant table — iteration 1.13
#
# Some (role, algorithm) pairs need MULTIPLE preset variants so
# different songs in the same style can have different timbres
# without exiting the style profile. Keyed by (role, algorithm) →
# list of preset tuples. ``apply_preset`` picks ``variants[i %
# len(variants)]`` where ``i`` comes from the gen's ``patch`` knob
# (the composer hash-picks this per song for variation styles like
# warm_analogue).
#
# When a (role, algorithm) appears in BOTH this dict and
# ``ROLE_STYLE_PRESETS``, this dict wins — but the ROLE_STYLE_PRESETS
# entry is treated as "variant 0" for any (role, algorithm) absent
# here.
# Surge FX type normalised values — handy lookup so variants below
# read clearly. Probed from the Surge XT VST3 in iteration 1.10/1.13.
_FX_OFF        = 0.0
_FX_DELAY      = 0.0251
_FX_REVERB_1   = 0.0603
_FX_PHASER     = 0.0905
_FX_DISTORTION = 0.1590
_FX_CHORUS     = 0.2965
_FX_REVERB_2   = 0.3668
_FX_FLANGER    = 0.4020
_FX_TAPE       = 0.7949
_FX_SPRING_REV = 0.9146


def _preset(
    *,
    filter_type: float = 0.4,        # default LP K35 — warm + smooth
    cutoff: float = 0.50,
    resonance: float = 0.35,
    feg_mod: float = 0.40,
    keytrack: float = 0.50,
    eg_attack: float = 0.0,
    eg_decay: float = 0.40,
    eg_sustain: float = 0.35,
    eg_release: float = 0.30,
    fx1_type: float = _FX_TAPE,      # tape FX by default (warmth)
    fx1_mix: float = 0.50,
    fx2_extras: tuple[tuple[str, float], ...] = (),
) -> tuple[tuple[str, float], ...]:
    """Build a Surge preset tuple from named parameters.

    Keeps variant entries one-line readable in ``PRESET_VARIANTS``
    instead of 12-line tuples. ``fx2_extras`` is for the occasional
    variant that uses FX A2 in addition to FX A1 (e.g. tape +
    distortion stacked).
    """
    base = [
        ("A Filter 1 Type", filter_type),
        ("A Filter 1 Cutoff", cutoff),
        ("A Filter 1 Resonance", resonance),
        ("A Filter 1 FEG Mod Amount", feg_mod),
        ("A Filter 1 Keytrack", keytrack),
        ("A Filter EG Attack", eg_attack),
        ("A Filter EG Decay", eg_decay),
        ("A Filter EG Sustain", eg_sustain),
        ("A Filter EG Release", eg_release),
        ("A Filter 2 Type", _FX_OFF),
        ("FX A1 FX Type", fx1_type),
        ("FX A1 Output - Mix", fx1_mix),
    ]
    return tuple(base) + fx2_extras


# Filter type shortcuts for readability.
_F_LADDER         = 0.1   # LP Legacy Ladder — classic 303/Moog
_F_NOTCH12        = 0.2
_F_VINTAGE_LADDER = 0.3   # LP Vintage Ladder — smoother ladder
_F_K35            = 0.4   # LP K35 — round / warm
_F_HP             = 0.6   # HP OB-Xd 12 dB

# FX A2 extras for stacking — used by a few variants.
_FX2_SOFT_OJD = (
    ("FX A2 FX Type", _FX_DISTORTION),
    ("FX A2 Distortion - Drive", 0.45),
    ("FX A2 Distortion - Model", 0.727),  # OJD (Tube Screamer)
    ("FX A2 Output - Gain", 0.45),
)


PRESET_VARIANTS: dict[tuple[str, str], list[tuple[tuple[str, float], ...]]] = {
    # warm_analogue bass — six takes on the MS-10-style sub bass.
    ("bass", "warm_sub"): [
        # 0 — Smoothie K35 + tape (default, the original iter 1.10 preset)
        _preset(filter_type=_F_K35, cutoff=0.40, resonance=0.25,
                feg_mod=0.35, keytrack=0.65,
                eg_decay=0.50, eg_sustain=0.50,
                fx1_type=_FX_TAPE, fx1_mix=0.55),
        # 1 — Ladder + soft drive (rounder, dirtier)
        _preset(filter_type=_F_LADDER, cutoff=0.35, resonance=0.40,
                feg_mod=0.40, keytrack=0.60,
                eg_decay=0.55, eg_sustain=0.55, eg_release=0.35,
                fx1_type=_FX_TAPE, fx1_mix=0.45,
                fx2_extras=_FX2_SOFT_OJD),
        # 2 — Sub-heavy K35 clean (no FX, just warm sub)
        _preset(filter_type=_F_K35, cutoff=0.32, resonance=0.15,
                feg_mod=0.20, keytrack=0.75,
                eg_decay=0.60, eg_sustain=0.65, eg_release=0.40,
                fx1_type=_FX_OFF, fx1_mix=0.0),
        # 3 — K35 + chorus (analogue ensemble character)
        _preset(filter_type=_F_K35, cutoff=0.45, resonance=0.30,
                feg_mod=0.35, keytrack=0.60,
                eg_decay=0.50, eg_sustain=0.45,
                fx1_type=_FX_CHORUS, fx1_mix=0.40),
        # 4 — Vintage Ladder + reverb (atmospheric / dub-leaning)
        _preset(filter_type=_F_VINTAGE_LADDER, cutoff=0.38,
                resonance=0.35, feg_mod=0.30, keytrack=0.60,
                eg_decay=0.55, eg_sustain=0.50,
                fx1_type=_FX_REVERB_1, fx1_mix=0.25),
        # 5 — Square / FM-flavoured (HP for mid-focus + tape)
        _preset(filter_type=_F_LADDER, cutoff=0.50, resonance=0.45,
                feg_mod=0.55, keytrack=0.55,
                eg_decay=0.30, eg_sustain=0.30, eg_release=0.20,
                fx1_type=_FX_TAPE, fx1_mix=0.50),
    ],
    # warm_analogue lead — six SH-101-style sequencer voices.
    ("lead", "sh101_arp"): [
        # 0 — K35 + tape (default, the iter 1.11 preset)
        _preset(filter_type=_F_K35, cutoff=0.50, resonance=0.35,
                feg_mod=0.40, keytrack=0.50,
                eg_decay=0.40, eg_sustain=0.35,
                fx1_type=_FX_TAPE, fx1_mix=0.50),
        # 1 — Ladder + chorus (classic SH-101 edge)
        _preset(filter_type=_F_LADDER, cutoff=0.55, resonance=0.50,
                feg_mod=0.60, keytrack=0.45,
                eg_decay=0.30, eg_sustain=0.30, eg_release=0.25,
                fx1_type=_FX_CHORUS, fx1_mix=0.40),
        # 2 — K35 + phaser (Rephlex/IDM colour)
        _preset(filter_type=_F_K35, cutoff=0.60, resonance=0.40,
                feg_mod=0.50, keytrack=0.50,
                eg_decay=0.35, eg_sustain=0.30, eg_release=0.25,
                fx1_type=_FX_PHASER, fx1_mix=0.35),
        # 3 — Vintage Ladder + delay (echoey lead — Aphex-leaning)
        _preset(filter_type=_F_VINTAGE_LADDER, cutoff=0.52,
                resonance=0.45, feg_mod=0.55, keytrack=0.50,
                eg_decay=0.30, eg_sustain=0.30, eg_release=0.30,
                fx1_type=_FX_DELAY, fx1_mix=0.35),
        # 4 — K35 + flanger (swooshy, modulated)
        _preset(filter_type=_F_K35, cutoff=0.58, resonance=0.40,
                feg_mod=0.45, keytrack=0.50,
                eg_decay=0.40, eg_sustain=0.40,
                fx1_type=_FX_FLANGER, fx1_mix=0.40),
        # 5 — Ladder + tape (dirtier / saturated lead)
        _preset(filter_type=_F_LADDER, cutoff=0.50, resonance=0.45,
                feg_mod=0.55, keytrack=0.50,
                eg_decay=0.30, eg_sustain=0.30, eg_release=0.25,
                fx1_type=_FX_TAPE, fx1_mix=0.55),
    ],
    # warm_analogue top arp — six takes on the bright sequencer
    # sparkle above the lead.
    ("candy", "sh101_top"): [
        # 0 — Bell Seq sparkle, K35 + tape (iter 1.12 default)
        _preset(filter_type=_F_K35, cutoff=0.65, resonance=0.30,
                feg_mod=0.50, keytrack=0.40,
                eg_decay=0.25, eg_sustain=0.10, eg_release=0.15,
                fx1_type=_FX_TAPE, fx1_mix=0.45),
        # 1 — Clavinet/wood pluck (very short decay)
        _preset(filter_type=_F_K35, cutoff=0.50, resonance=0.25,
                feg_mod=0.55, keytrack=0.55,
                eg_decay=0.15, eg_sustain=0.05, eg_release=0.10,
                fx1_type=_FX_TAPE, fx1_mix=0.40),
        # 2 — Resonant mini-303 top (Ladder + chorus)
        _preset(filter_type=_F_LADDER, cutoff=0.55, resonance=0.60,
                feg_mod=0.70, keytrack=0.45,
                eg_decay=0.20, eg_sustain=0.10, eg_release=0.20,
                fx1_type=_FX_CHORUS, fx1_mix=0.35),
        # 3 — Echoey sparkle (K35 + delay)
        _preset(filter_type=_F_K35, cutoff=0.60, resonance=0.35,
                feg_mod=0.50, keytrack=0.40,
                eg_decay=0.20, eg_sustain=0.05, eg_release=0.25,
                fx1_type=_FX_DELAY, fx1_mix=0.40),
        # 4 — Atmospheric (K35 + reverb)
        _preset(filter_type=_F_K35, cutoff=0.55, resonance=0.30,
                feg_mod=0.45, keytrack=0.40,
                eg_decay=0.30, eg_sustain=0.20, eg_release=0.30,
                fx1_type=_FX_REVERB_1, fx1_mix=0.40),
        # 5 — Modulated (Vintage Ladder + phaser)
        _preset(filter_type=_F_VINTAGE_LADDER, cutoff=0.62,
                resonance=0.40, feg_mod=0.55, keytrack=0.45,
                eg_decay=0.25, eg_sustain=0.10, eg_release=0.20,
                fx1_type=_FX_PHASER, fx1_mix=0.40),
    ],
}


def apply_preset(synth, role: str, style: str, *, variant: int = 0, engine=None) -> bool:
    """Apply the (role, style) preset to *synth* if one exists.

    Returns True if a preset was applied; False if none is registered
    for the pair (caller falls back to the `.fxp` load attempt, which
    is broken but harmless).

    *synth* is a `dawdreamer.PluginProcessor` with the Surge XT VST3
    loaded. *engine* is the parent ``RenderEngine`` — required when
    the preset uses FX slot params (see below).

    Two-pass application is required because Surge's FX slot inner
    params (delay time, distortion drive, reverb mix, etc.) only
    expose their proper names once two conditions are met:

    1. The corresponding ``FX <slot> FX Type`` parameter has been set
       to a non-Off value.
    2. The plugin has actually processed at least one audio buffer
       (Surge initialises the FX module lazily on first render).

    Before both, the inner params all show as ``FX A1 Param 1`` …
    ``FX A1 Param 12`` so a single-pass name-to-index map wouldn't
    find them — which is why ``slackbeatz audio --setup surge``
    silently rendered without delay/distortion for iterations 1.8 +
    1.10 before this fix landed.

    Algorithm:

    * **Pass 1**: set ``FX <slot> FX Type`` entries.
    * **Warmup render** (if *engine* is provided + we set any FX
      types): load an empty MIDI + ``load_graph`` + ``render(0.01)``
      so Surge initialises the inner FX param namespace.
    * **Pass 2**: re-read the descriptor (FX names now live) and set
      everything else.

    When *engine* is ``None``, the warmup is skipped — FX-inner
    params will silently fail to apply but the rest still works.
    This shape exists so the function stays callable from contexts
    without an engine (e.g. the unit tests' stub-synth path).
    """
    key = (role, style)
    # PRESET_VARIANTS wins when it has entries for this (role, style) —
    # the indexed variant lets the composer hash-pick a timbre per song.
    # Falls back to the single-preset ROLE_STYLE_PRESETS map.
    if key in PRESET_VARIANTS:
        variants = PRESET_VARIANTS[key]
        preset = variants[variant % len(variants)]
    elif key in ROLE_STYLE_PRESETS:
        preset = ROLE_STYLE_PRESETS[key]
    else:
        return False

    fx_type_entries = [(n, v) for n, v in preset if n.endswith("FX Type")]
    other_entries = [(n, v) for n, v in preset if not n.endswith("FX Type")]

    name_to_idx = {
        p["name"]: p["index"]
        for p in synth.get_plugin_parameters_description()
    }

    # Pass 1 — set FX types so the slot's inner params will become
    # available once the warmup render fires.
    for name, value in fx_type_entries:
        idx = name_to_idx.get(name)
        if idx is None:
            continue
        synth.set_parameter(idx, value)

    # Warmup render — exposes FX inner param names. Needs an engine.
    if fx_type_entries and engine is not None:
        _warmup_render_for_fx_init(synth, engine)
        # Refresh descriptor — FX inner names are live now.
        name_to_idx = {
            p["name"]: p["index"]
            for p in synth.get_plugin_parameters_description()
        }

    # Pass 2 — set everything else.
    for name, value in other_entries:
        idx = name_to_idx.get(name)
        if idx is None:
            continue
        synth.set_parameter(idx, value)
    return True


def _warmup_render_for_fx_init(synth, engine) -> None:
    """Render ~0.5 s with a single note so Surge XT VST3 initialises
    its FX inner parameter namespace. The actual audio is discarded
    — this only exists for the side effect on the param descriptor.

    Empirically: an empty-MIDI render or a sub-100-ms render isn't
    enough — Surge needs to actually process audio for the FX
    module to wake up + expose param names. 0.5 s with one note
    reliably does it.

    See :func:`apply_preset` for the why.
    """
    import mido
    import tempfile
    from pathlib import Path
    # Real (silent-ish) MIDI: one quiet low note for one beat.
    mid = mido.MidiFile(ticks_per_beat=96)
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
    tr.append(mido.Message("note_on", channel=0, note=36, velocity=1, time=0))
    tr.append(mido.Message("note_off", channel=0, note=36, velocity=0, time=96))
    mid.tracks.append(tr)
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tf:
        mid.save(tf.name)
        warmup_path = Path(tf.name)
    try:
        synth.load_midi(str(warmup_path))
        engine.load_graph([(synth, [])])
        engine.render(0.5)
    finally:
        warmup_path.unlink(missing_ok=True)


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
