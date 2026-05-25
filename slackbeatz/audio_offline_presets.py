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
    # ----- sh101_arp (iteration 1.7): pure SH-101 character -----
    # 1.7 → 1.8: brightened cutoff (was 0.55, now 0.70 ≈ 2.5 kHz) so
    # the lead actually cuts through the bass + drums mix.
    # Added a short Delay on FX A1 to give the lead some space + width
    # (helps the line stand out without raising velocity further).
    ("lead", "sh101_arp"): (
        ("A Filter 1 Type", 0.1),            # LP Legacy Ladder
        ("A Filter 1 Cutoff", 0.70),         # brighter — sits above the bass
        ("A Filter 1 Resonance", 0.55),      # less res than bass (less screech)
        ("A Filter 1 FEG Mod Amount", 0.75), # strong env per note
        ("A Filter 1 Keytrack", 0.50),
        ("A Filter EG Attack", 0.0),
        ("A Filter EG Decay", 0.30),
        ("A Filter EG Sustain", 0.30),
        ("A Filter EG Release", 0.25),
        ("A Filter 2 Type", 0.0),
        # Short delay — 1/16 note at 124 BPM ~ 120 ms. Subtle, just
        # spreads the lead into stereo space.
        ("FX A1 FX Type", 0.0251),
        ("FX A1 Delay Time - Left", 0.32),    # ~120 ms
        ("FX A1 Delay Time - Right", 0.35),   # slightly different = stereo spread
        ("FX A1 Feedback/EQ - Feedback", 0.30),
        ("FX A1 Feedback/EQ - High Cut", 0.55),
        ("FX A1 Output - Mix", 0.22),
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


def apply_preset(synth, role: str, style: str, *, engine=None) -> bool:
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
    if key not in ROLE_STYLE_PRESETS:
        return False
    preset = ROLE_STYLE_PRESETS[key]

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
