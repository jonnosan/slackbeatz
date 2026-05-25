"""Compose a slackbeatz song from arbitrary text input.

The function :func:`compose_from_text` does five things:

1. **Extract a title** from the first phrase of the input.
2. **Score mood / sentiment** by keyword matching against per-style word
   banks (vaporwave words, psytrance words, etc.) and a positive /
   negative sentiment list.
3. **Derive deterministic seeds + key + tempo** from a SHA-256 digest
   of the *original* (case-preserving) input — so flipping one letter
   produces a different song with the same overall shape.
4. **Pick an arrangement template** appropriate for the chosen style.
5. **Emit a complete `.sb` file** as a string, ready to be saved /
   parsed / rendered.

Everything is purely deterministic: same input → byte-identical output.

Design notes:

* The scoring is intentionally simple. It's a sentiment-by-keyword
  heuristic, not an LLM call — same input always lands on the same
  style. Adding new keywords / styles is a one-dict-entry change.

* All "random" musical choices (key root, tempo wobble, per-gen flair
  knobs) come from disjoint slices of the SHA-256 digest. The seed
  itself is just one of those slices, but it cascades through the
  scheduler's :func:`derive_seed` into every (part, gen) PRNG so the
  microvariation is wired through.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path


# --------------------------------------------------------------------------
# Style scoring — keyword banks
# --------------------------------------------------------------------------

# Each style has a bag of keywords; if the input contains the keyword,
# the style scores points. The style with the highest total wins.
# Keywords are matched case-insensitively against word tokens in the
# input (whole-word match for short ones, substring match for distinctive
# multi-letter ones).
_STYLE_KEYWORDS: dict[str, dict[str, int]] = {
    "vaporwave": {
        # Pink / neon / sunset / drift = vaporwave aesthetic
        "dream":  3, "neon":   3, "mall":   3, "sunset": 3, "summer": 1,
        "soft":   1, "memory": 3, "nostalg": 4, "pink":   2, "drift":  2,
        "lonely": 2, "wave":   2, "ocean":  1, "slow":   1, "haze":   2,
        "tape":   2, "vapor":  4, "plaza":  3, "palm":   1, "fade":   1,
    },
    "psytrance": {
        "trip":     3, "trance":   4, "spiral":   3, "infinite": 3,
        "cosmic":   3, "alien":    3, "shaman":   3, "ritual":   3,
        "tribal":   2, "fire":     1, "burn":     1, "energy":   2,
        "warrior":  2, "psychedel": 4, "goa":     3, "third eye": 4,
        "dmt":      3, "mushroom": 2, "forest":   1,
    },
    "deep_techno": {
        "deep":      3, "underground": 4, "minimal":  3, "warehouse": 3,
        "berlin":    3, "tunnel":      2, "machine":  2, "ghost":    2,
        "smoke":     1, "concrete":    2, "midnight": 2, "tresor":   3,
        "detroit":   2, "factory":     2, "abandoned": 2, "ruin":    1,
    },
    "dub_techno": {
        "dub":       4, "rain":    2, "echo":    3, "mist":   2,
        "fog":       2, "distant": 2, "blue":    1, "shadow": 1,
        "submerge":  3, "submarine": 2, "tide":   2, "drift":  1,
        "atmosphere": 2, "ether":   2, "basic channel": 5,
    },
    "acid": {
        "acid":   5, "303":     4, "squelch": 4, "phuture": 5,
        "chicago": 3, "jack":   3, "raw":     1, "trax":    3,
        "wild":   1, "burner":  1, "filter":  2,
    },
    "drum_and_bass": {
        "jungle":   4, "amen":   4, "break":    2, "junglist": 5,
        "bass":     2, "fast":   1, "rush":     2, "ragga":    3,
        "neurofunk": 4, "liquid": 3, "drum":    2, "roller":   2,
    },
    "garage": {
        "garage":  4, "uk":      2, "london":   3, "2step":    4,
        "shuffle": 3, "vocal":   2, "rolling":  1, "skip":     2,
        "fwd":     2, "speed":   2,  # speed garage
    },
    "euclid": {
        # General techno fallback — these mostly score low so other
        # styles can override.
        "techno":  3, "club":    1, "dance":    1, "night":    1,
        "beat":    1, "rhythm":  1, "pulse":    1, "drive":    2,
        "loop":    1, "floor":   1, "groove":   1,
    },
    "lofi": {
        # Lofi hip-hop / chillhop / "study beats" keyword bank.
        "lofi":     5, "chill":    3, "study":    3, "rhodes":   4,
        "vinyl":    3, "rainy":    2, "coffee":   2, "jazz":     2,
        "hip-hop":  2, "hiphop":   2, "chillhop": 5, "beats":    1,
        "warm":     2, "cozy":     3, "smoke":    1, "evening":  1,
        "saturday": 2, "vibes":    2, "yume":     2, "japanese": 1,
        "tape":     1,
    },
}

# Sentiment: positive = bright/major-leaning; negative = dark/minor-leaning.
_SENTIMENT_WORDS: dict[str, int] = {
    # Dark / sad
    "dark":     -3, "sad":      -3, "lonely":   -2, "sorrow":   -3,
    "grim":     -2, "cold":     -2, "night":    -1, "shadow":   -2,
    "lost":     -2, "alone":    -2, "dread":    -3, "fear":     -2,
    "winter":   -1, "void":     -2, "death":    -3, "broken":   -3,
    "empty":    -2, "ghost":    -1, "abyss":    -3, "decay":    -2,
    "haunted":  -2, "weeping":  -3, "fall":     -1, "ash":      -1,
    "rain":     -1, "mourning": -3, "grief":    -3,
    # Bright / happy
    "bright":    3, "happy":     3, "joy":      3, "summer":    2,
    "warm":      2, "love":      2, "dance":    1, "celebrate": 3,
    "shine":     2, "golden":    2, "sweet":    2, "dawn":      2,
    "spring":    2, "hope":      2, "smile":    2, "laugh":     2,
    "bloom":     2, "alive":     2, "sun":      1, "light":     1,
    "rainbow":   3, "victory":   3, "dance":    1, "rise":      1,
}


# --------------------------------------------------------------------------
# Style → musical-defaults map
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class GenSpec:
    """One generator slot in a style profile.

    Today's mapping ``(handle, type) → style`` becomes
    ``(handle, type) → (algorithm, knob_defaults)``: a style
    profile pins the *algorithm* each gen runs plus any baked-in
    knob values the style wants applied unconditionally.

    Pre-rename, ``algorithm`` mirrors the old style name verbatim
    so byte output is unchanged — the field is a forward-compatible
    column that the bulk rename in #50 will populate with
    algorithm names.
    """

    handle: str
    type_: str
    algorithm: str
    knob_defaults: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class StyleProfile:
    """Per-style musical defaults the composer falls back to."""

    base_tempo: int                     # central bpm
    tempo_range: int                    # ±N bpm hash-driven wobble
    arrangement: list[tuple[str, int]]  # (role, bars) pairs
    gens: list[GenSpec]                 # one slot per (handle, type)
    favours_minor: bool = True           # bias key picker toward minor


_STYLE_PROFILES: dict[str, StyleProfile] = {
    "euclid": StyleProfile(
        base_tempo=128, tempo_range=4,
        arrangement=[
            ("intro", 16), ("build", 8), ("drop", 32),
            ("break", 16), ("build", 8), ("drop", 32),
        ],
        gens=[
            GenSpec("kick",  "rhythm", "euclid_drums"),
            GenSpec("snare", "rhythm", "euclid_drums"),
            GenSpec("hats",  "rhythm", "euclid_drums"),
            GenSpec("bass",  "bass",   "rolling"),
            GenSpec("lead",  "melody", "euclid_riff"),
            GenSpec("pad",   "chords", "triad_sustain"),
            GenSpec("riser", "candy",  "euclid_riser"),
        ],
    ),
    "deep_techno": StyleProfile(
        base_tempo=122, tempo_range=4,
        arrangement=[
            ("intro", 16), ("main", 32), ("break", 16),
            ("main", 32), ("outro", 16),
        ],
        gens=[
            GenSpec("kick",  "rhythm", "four_floor_deep"),
            GenSpec("hats",  "rhythm", "four_floor_deep"),
            GenSpec("clap",  "rhythm", "four_floor_deep"),
            GenSpec("bass",  "bass",   "subdrone"),
            GenSpec("lead",  "melody", "sparse_pad_lead"),
            GenSpec("pad",   "chords", "pad_drift"),
            GenSpec("riser", "candy",  "slow_lfo"),
        ],
    ),
    "psytrance": StyleProfile(
        base_tempo=142, tempo_range=4,
        arrangement=[
            ("intro", 8), ("build", 8), ("drop", 32),
            ("bridge", 16), ("build", 8), ("drop", 32),
        ],
        gens=[
            GenSpec("kick",  "rhythm", "gallop_kick"),
            GenSpec("hats",  "rhythm", "gallop_kick"),
            GenSpec("clap",  "rhythm", "gallop_kick"),
            GenSpec("bass",  "bass",   "gallop"),
            GenSpec("lead",  "melody", "psy_lead"),
            GenSpec("pad",   "chords", "psy_swell"),
            GenSpec("riser", "candy",  "psy_sweep"),
        ],
    ),
    "vaporwave": StyleProfile(
        base_tempo=75, tempo_range=4,
        arrangement=[
            ("intro", 16), ("main", 32), ("main", 32), ("outro", 16),
        ],
        gens=[
            GenSpec("kick",  "rhythm", "slow_kick"),
            GenSpec("snare", "rhythm", "slow_kick"),
            GenSpec("hats",  "rhythm", "slow_kick"),
            GenSpec("bass",  "bass",   "mellow_pick"),
            GenSpec("lead",  "melody", "lazy_sax"),
            GenSpec("pad",   "chords", "arp_walk"),
            GenSpec("bells", "candy",  "bell_lfo"),
        ],
    ),
    "acid": StyleProfile(
        # Iteration 1.6: dropped the chord stab entirely — user feedback
        # was "stabs sound boring and out of place, maybe they shouldn't
        # be there at all". Replaced with a sequenced lead that
        # interleaves with the 303 bass (notes on the off-eighths /
        # off-sixteenths where the bass doesn't fall). Real acid tracks
        # carry harmony through the bass; the lead is melodic
        # punctuation, not a chord pad.
        #
        # Bass character: aggressive filter LFO (cycle=6, resonance
        # ceiling 120), pitch-bend wobble (120 units = ±3 cents), 35%
        # per-note portamento for slides. drop_intensity + evolution
        # give the build → drop transitions an automatic energy ramp.
        base_tempo=124, tempo_range=4,
        arrangement=[
            ("intro", 16), ("main", 32), ("build", 8), ("drop", 32),
            ("main", 32), ("build", 8), ("drop", 32),
        ],
        gens=[
            GenSpec("kick",  "rhythm", "four_floor_house"),
            GenSpec("clap",  "rhythm", "four_floor_house"),
            GenSpec("hats",  "rhythm", "four_floor_house"),
            GenSpec("bass",  "bass",   "acid_303", knob_defaults={
                # Iteration 1.8 — cycle=0 disables the bass's built-in
                # CC74/CC71 sine LFO. Two LFO sources on the same CC
                # was causing chaos (whichever event hit later won).
                # The song-wide sawtooth (`apply acid_filter`) is now
                # the sole CC74 driver — one slow ramp from filter-
                # closed at song-start to filter-open at song-end.
                # Per-note motion still comes from the Surge preset's
                # filter envelope (FEG Mod Amount 0.65).
                "cycle": 0,
                "resonance": 120,
                "bend": 120,
                "intensity": 1.0,
                "slide_prob": 0.35,
                "evolution": 0.4,
            }),
            # Iteration 1.7 — SH-101-style sequencer-clocked arp.
            # Fixed pitch sequence (root / min3 / P5 / P4 of minor
            # pentatonic), euclidean trigger pattern of 5 pulses in
            # 16 steps. Pitches always play in the same order; the
            # rhythm comes from the euclidean spacing. Classic
            # acid-techno lead character.
            GenSpec("lead",  "melody", "sh101_arp", knob_defaults={
                "pitches": "0,3,7,5",
                "pulses": 5,
                "steps": 16,
                "gate": 0.85,
                "evolution": 0.4,
                "base_vel": 85,
                "intensity": 0.85,
            }),
            GenSpec("sweep", "candy",  "acid_sweep"),
        ],
    ),
    "dub_techno": StyleProfile(
        base_tempo=120, tempo_range=3,
        arrangement=[
            ("intro", 16), ("main", 64), ("outro", 16),
        ],
        gens=[
            GenSpec("kick",  "rhythm", "four_floor_dub"),
            GenSpec("hats",  "rhythm", "four_floor_dub"),
            GenSpec("drone", "bass",   "sustain_drone"),
            GenSpec("stab",  "chords", "offbeat_stab"),
            GenSpec("tex",   "candy",  "drone_lfo"),
        ],
    ),
    "drum_and_bass": StyleProfile(
        base_tempo=172, tempo_range=4,
        arrangement=[
            ("intro", 8), ("main", 32), ("break", 8), ("main", 32),
        ],
        gens=[
            GenSpec("kick",  "rhythm", "breakbeat"),
            GenSpec("snare", "rhythm", "breakbeat"),
            GenSpec("hats",  "rhythm", "breakbeat"),
            GenSpec("sub",   "bass",   "reese"),
            GenSpec("lead",  "melody", "atmos_lead"),
            GenSpec("pad",   "chords", "atmos_pad"),
            GenSpec("tex",   "candy",  "atmos_lfo"),
        ],
    ),
    "garage": StyleProfile(
        base_tempo=132, tempo_range=4,
        arrangement=[
            ("intro", 8), ("main", 32), ("break", 8), ("main", 32),
        ],
        gens=[
            GenSpec("kick",  "rhythm", "two_step"),
            GenSpec("snare", "rhythm", "two_step"),
            GenSpec("hats",  "rhythm", "two_step"),
            GenSpec("sub",   "bass",   "two_step_sub"),
            GenSpec("vocal", "melody", "vocal_chop"),
            GenSpec("wurli", "chords", "wurli_chop"),
            GenSpec("tex",   "candy",  "minimal_lfo"),
        ],
    ),
    "lofi": StyleProfile(
        base_tempo=82, tempo_range=4,            # 78-86 BPM — typical lofi
        arrangement=[
            ("intro", 8), ("main", 32), ("break", 8), ("main", 32), ("outro", 16),
        ],
        gens=[
            GenSpec("kick",    "rhythm", "dusty_swing"),
            GenSpec("snare",   "rhythm", "dusty_swing"),
            GenSpec("hats",    "rhythm", "dusty_swing"),
            GenSpec("upright", "bass",   "acoustic_walk"),
            GenSpec("rhodes",  "melody", "rhodes_phrase"),
            GenSpec("ep",      "chords", "rhodes_chord"),
            GenSpec("crackle", "candy",  "crackle_lfo"),
        ],
    ),
}


# Map composer-chosen handle → which `inst` in the bundled `gm` setup
# it should route to. Lets the .sb file use a descriptive handle ("bells",
# "organ", "drone") while still resolving against the standard rig.
_HANDLE_TO_INST: dict[str, str] = {
    "bells":  "riser",
    "organ":  "pad",
    "sweep":  "riser",
    "drone":  "bass",
    "stab":   "pad",
    "tex":    "riser",
    "sub":    "bass",
    "vocal":  "lead",
    "wurli":  "pad",
    # lofi handles
    "upright": "bass",
    "rhodes":  "lead",
    "ep":      "pad",
    "crackle": "riser",
}


# Which gens are active in each part-role.
#
# Intros include rhythm + bass + chords + candy so the song opens with
# substance — drums for the rhythmic anchor, bass for low-end weight,
# chords/candy for harmonic colour. The lead/melody holds back until
# the main section so its entry is a satisfying arrival rather than
# just one more layer from bar 1.
#
# Earlier revisions:
# - v1: only chords + candy → bare 16-bar pad intro ("slow plod" user
#   complaint).
# - v2: added rhythm but no bass → drums-only intro at 1/12 the main
#   loudness, still felt empty.
# - v3 (now): rhythm + bass + chords + candy → drums + bass from bar 1,
#   lead enters in main.
_ROLE_GEN_TYPES: dict[str, frozenset[str]] = {
    "intro":   frozenset({"rhythm", "bass", "chords", "candy"}),
    "build":   frozenset({"rhythm", "bass", "chords", "candy"}),
    "drop":    frozenset({"rhythm", "bass", "melody", "chords", "candy"}),
    "main":    frozenset({"rhythm", "bass", "melody", "chords", "candy"}),
    "break":   frozenset({"chords", "melody", "candy"}),
    "bridge":  frozenset({"rhythm", "bass", "chords"}),
    "outro":   frozenset({"rhythm", "bass", "chords", "candy"}),
}


_NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]


# --------------------------------------------------------------------------
# Style-driven LFO + apply emission (issue #65 wiring into the composer)
# --------------------------------------------------------------------------

def _emit_style_lfos(lines: list[str], style: str) -> None:
    """Append top-level ``lfo NAME ...`` declarations the style needs.

    Iteration 1.8 — replaced the per-drop sawtooth (bars=8) with a
    much longer sawtooth (bars=160 ≈ whole song at the acid arrangement's
    160 bars). Combined with the scheduler's new absolute-song
    phase calculation, this gives ONE continuous ramp from filter-
    closed at song-start to filter-open at song-end — the "starts low
    and opens up gradually" character classic acid tracks have.
    Applied to every part role (not just drops) so the ramp is
    continuous.
    """
    if style == "acid":
        # 160 bars matches the acid arrangement's total length;
        # phase wraps cleanly at the end. height=0.7 (not 1.0) so
        # CC74 ramps 0.15→0.85 (≈19→108) — bass stays audible
        # during the intro (closed-but-not-silent filter) and
        # never hits the brightest extreme even at song end.
        lines.append("lfo acid_filter shape=sawtooth bars=160 height=0.7")


# Per-(style, role) `apply` lines — the part loop in render_sb()
# inserts these as indented children of the part header. Keyed by
# part-role so the same LFO can attach selectively to certain
# section types if desired. For the acid whole-song ramp, every
# rendered role gets the apply so the ramp progresses throughout.
_STYLE_APPLY_LINES: dict[str, dict[str, tuple[str, ...]]] = {
    "acid": {
        "intro": ("apply acid_filter target=midi:ch:2/cc:74",),
        "main":  ("apply acid_filter target=midi:ch:2/cc:74",),
        "build": ("apply acid_filter target=midi:ch:2/cc:74",),
        "drop":  ("apply acid_filter target=midi:ch:2/cc:74",),
        "outro": ("apply acid_filter target=midi:ch:2/cc:74",),
    },
}


def _apply_lines_per_role(style: str) -> dict[str, tuple[str, ...]]:
    """Return per-role apply lines for *style*, or an empty dict."""
    return _STYLE_APPLY_LINES.get(style, {})


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def compose_from_text(
    text: str,
    *,
    output_path: Path | str | None = None,
    seed_offset: int = 0,
    style_override: str | None = None,
    algorithm_per_type: dict[str, str] | None = None,
    tempo_override: int | None = None,
) -> str:
    """Compose a slackbeatz `.sb` file from an arbitrary input string.

    Returns the `.sb` content as a string. If *output_path* is given,
    the content is also written there.

    The input string is hashed (SHA-256, case-preserving) to derive all
    "random" choices — same input ⇒ byte-identical output, but flipping
    a single character produces a different song with similar overall
    shape.

    *seed_offset* is folded into the SHA-256 digest so the same phrase
    produces a different song for each integer value — used by the
    GUI seed box (and the ↻ Re-roll button) to spin variations on a
    phrase without retyping.

    *style_override* — force a specific style name regardless of what
    the title's keyword scoring would pick. Valid names are the keys
    of slackbeatz.generators.registry minus the type prefix (e.g.
    "deep_techno", "psytrance", "acid", "vaporwave", "dub_techno",
    "drum_and_bass", "garage", "euclid").

    *algorithm_per_type* — optional per-gen-type algorithm override.
    The primary *style* (and its profile in :data:`_STYLE_PROFILES`)
    still drives the song's gen layout / tempo / arrangement; entries
    in this map just rewrite individual gens' algorithm column so
    e.g. ``{"chords": "rhodes_chord"}`` runs the rhodes_chord
    algorithm for chords inside an otherwise-acid song. Unknown gen
    types are ignored.

    *tempo_override* — force a specific BPM in place of the
    sentiment-derived value.
    """
    title = extract_title(text)
    # Mood / style come from the *title* (first phrase only) — the
    # spec is "determine the mood/emotion of that title".
    style = style_override or pick_style(title)
    sentiment = score_sentiment(title)
    # Seed comes from the *full* input — text after the first phrase
    # contributes too, so adding " — take 2" produces a different song
    # with the same title + style. seed_offset participates by being
    # appended to the hashed payload — same phrase + same offset is
    # byte-identical; same phrase + different offset is different.
    payload = text.encode("utf-8")
    if seed_offset:
        payload += b"\x00" + str(seed_offset).encode("ascii")
    h = hashlib.sha256(payload).digest()
    seed = int.from_bytes(h[0:6], "big")
    tempo = tempo_override if tempo_override is not None else derive_tempo(h, style, sentiment)
    key = derive_key(h, style, sentiment)
    content = render_sb(
        title, style, key, tempo, seed, h, sentiment,
        algorithm_per_type=algorithm_per_type,
    )
    if output_path is not None:
        Path(output_path).write_text(content)
    return content


# --------------------------------------------------------------------------
# Title extraction
# --------------------------------------------------------------------------

def extract_title(text: str) -> str:
    """Return the first phrase of *text* as a song title.

    Splits on phrase-ending punctuation (``.``, ``!``, ``?``, ``;``,
    newline) and takes the first non-empty piece. Strips leading /
    trailing whitespace + punctuation. Caps at 8 words.
    """
    text = text.strip()
    if not text:
        return "Untitled"
    for delim in (".", "!", "?", "\n", ";"):
        if delim in text:
            text = text.split(delim, 1)[0]
            break
    text = text.strip(" \t\r\n.,!?;:-—\"'`()[]{}")
    words = text.split()
    if len(words) > 8:
        text = " ".join(words[:8])
    return text or "Untitled"


# --------------------------------------------------------------------------
# Mood / sentiment scoring
# --------------------------------------------------------------------------

def pick_style(text: str) -> str:
    """Pick the best-fit style for *text* by keyword scoring."""
    lower = text.lower()
    scores: dict[str, int] = {}
    for style, kws in _STYLE_KEYWORDS.items():
        score = 0
        for kw, points in kws.items():
            if kw in lower:
                score += points
        scores[style] = score
    best = max(scores, key=lambda s: scores[s])
    # If everything scored 0, fall back to euclid (general-purpose).
    if scores[best] == 0:
        return "euclid"
    return best


def score_sentiment(text: str) -> int:
    """Sum sentiment points: negative = dark, positive = bright."""
    lower = text.lower()
    total = 0
    for word, points in _SENTIMENT_WORDS.items():
        if word in lower:
            total += points
    return total


# --------------------------------------------------------------------------
# Hash-driven musical choices
# --------------------------------------------------------------------------

def derive_tempo(h: bytes, style: str, sentiment: int) -> int:
    """Pick a tempo around the style's base, with hash-driven wobble and
    a small sentiment nudge (dark → slower, bright → faster)."""
    profile = _STYLE_PROFILES[style]
    base = profile.base_tempo
    span = profile.tempo_range
    # Hash byte for wobble — map to ±span.
    if span > 0:
        offset = (h[8] % (2 * span + 1)) - span
    else:
        offset = 0
    if sentiment <= -3:
        offset -= 2
    elif sentiment >= 3:
        offset += 2
    return max(40, min(220, base + offset))


def derive_key(h: bytes, style: str, sentiment: int) -> str:
    """Pick a key root from the hash; sentiment biases minor vs major.

    All slackbeatz styles work in both major and minor keys, but most
    have hardcoded scales (psytrance → phrygian, vaporwave → dorian,
    etc.) that ignore the major/minor flag at the gen level. The flag
    still affects bass-fifth voicing in some gens, so it's worth setting.
    """
    pitch_class = h[9] % 12
    name = _NOTE_NAMES[pitch_class]
    profile = _STYLE_PROFILES[style]
    # Strongly positive sentiment → major; otherwise minor (matches
    # most slackbeatz styles' natural feel).
    if sentiment >= 4 and not profile.favours_minor:
        return name
    if sentiment >= 4:
        return name        # major
    return f"{name}m"      # minor


def derive_scale_override(h: bytes, style: str, sentiment: int) -> str | None:
    """Optional scale override — some hash bits trigger a "flavour" scale.

    Roughly 1/4 of inputs get a non-default scale. The picker biases
    toward modal scales that mesh well with the style.
    """
    # Use one hash bit to decide whether to override; another to pick.
    if h[10] & 0x03 != 0:  # 75% chance NOT to override
        return None
    pool: list[str]
    if sentiment <= -2:
        # Dark moods → exotic / minor flavours
        pool = ["phrygian", "hijaz", "hungarian_minor", "harmonic_minor"]
    elif sentiment >= 2:
        # Bright moods → bright / modal flavours
        pool = ["lydian", "mixolydian", "major_pentatonic", "melodic_minor"]
    else:
        # Neutral → modal options
        pool = ["dorian", "minor_pentatonic", "blues_minor", "phrygian"]
    return pool[h[11] % len(pool)]


def derive_seed_overrides(h: bytes) -> dict[str, int]:
    """Per-gen seed overrides — pulled from different hash slices so a
    single-character input change perturbs each gen independently."""
    return {
        "lead":  int.from_bytes(h[16:18], "big"),
        "bass":  int.from_bytes(h[18:20], "big"),
        "pad":   int.from_bytes(h[20:22], "big"),
        "riser": int.from_bytes(h[22:24], "big"),
        "tex":   int.from_bytes(h[24:26], "big"),
        "sub":   int.from_bytes(h[26:28], "big"),
        "drone": int.from_bytes(h[28:30], "big"),
    }


# --------------------------------------------------------------------------
# .sb file emission
# --------------------------------------------------------------------------

def render_sb(
    title: str,
    style: str,
    key: str,
    tempo: int,
    seed: int,
    h: bytes,
    sentiment: int,
    *,
    algorithm_per_type: dict[str, str] | None = None,
) -> str:
    """Format a complete `.sb` file string.

    *algorithm_per_type* (optional) overrides the per-gen algorithm
    column for individual gen types — e.g.
    ``{"chords": "rhodes_chord"}`` writes ``gen chord chords
    rhodes_chord`` while every other gen still uses the primary
    *style*'s default algorithm for that type. The primary style
    continues to drive the song profile (which gens are included,
    the arrangement template), so per-type overrides change only
    the per-gen algorithm.
    """
    algorithm_per_type = algorithm_per_type or {}
    profile = _STYLE_PROFILES[style]
    seed_overrides = derive_seed_overrides(h)
    scale_override = derive_scale_override(h, style, sentiment)
    # A second "flair" byte drives a handful of style-specific knobs.
    flair = h[14]
    humanize = 2 + (flair % 4)              # 2..5
    drop_prob = round(0.02 + (h[15] % 8) * 0.01, 2)  # 0.02 .. 0.09
    accent = 4 if (flair & 0x80) else 0

    lines: list[str] = [
        f"# Auto-composed from text — sentiment={sentiment:+d}, style={style}",
        f"# Title derived from first phrase of the input.",
        "",
        f'song "{title}"',
        f'  setup "gm"',
        f"  tempo {tempo}",
        f"  key   {key}",
        f"  seed  {seed}",
    ]
    if scale_override is not None:
        lines.append(f"  scale {scale_override}")
    lines.append("")

    # Gens with style-aware knobs sprinkled in.
    gens = profile.gens
    for spec in gens:
        handle = spec.handle
        gen_type = spec.type_
        # Per-type override (from the Builder 🎨 Per-voice section)
        # still wins; otherwise the style profile's algorithm is what
        # ends up on the .sb gen line.
        gen_algorithm = algorithm_per_type.get(gen_type, spec.algorithm)
        parts = [f"gen {handle:<6} {gen_type:<7} {gen_algorithm}"]
        # If the chosen handle isn't a standard gm-setup instrument name,
        # add an inst= knob mapping it onto the real rig.
        if handle in _HANDLE_TO_INST:
            parts.append(f"inst={_HANDLE_TO_INST[handle]}")
        # Static knob defaults baked into the style profile — empty
        # today, populated by the subbass + candy consolidation in #49.
        for k, v in spec.knob_defaults.items():
            parts.append(f"{k}={v}")
        # Rhythm gens get the humanize / drop_prob knobs.
        if gen_type == "rhythm":
            parts.append(f"humanize={humanize}")
            if drop_prob > 0 and handle in ("hats", "hat", "snare", "clap"):
                parts.append(f"drop_prob={drop_prob}")
            if accent and handle.startswith(("hat", "kick")):
                parts.append(f"accent={accent}")
        elif gen_type == "bass":
            # Sub-bass styles get an octave_jump touch.
            if flair & 0x10:
                parts.append("octave_jump=0.05")
            # Acid bass gets burble.
            if gen_algorithm == "gallop" and flair & 0x20:
                parts.append("burble_prob=0.08")
        elif gen_type == "melody":
            if flair & 0x40:
                parts.append("motif_memory=4")
            if flair & 0x08:
                parts.append("passing_tones=0.1")
            if seed_overrides.get(handle) is not None:
                parts.append(f"seed={seed_overrides[handle] % 1000}")
        elif gen_type == "chords":
            if flair & 0x04:
                parts.append("voice_lead=1")
            if gen_algorithm == "arp_walk" and flair & 0x02:
                parts.append("arp_prob=0.1")
        lines.append(" ".join(parts))
    lines.append("")

    # Style-driven LFO declarations — only acid has wired LFO usage
    # today. The `apply` lines underneath each part hook the LFO to
    # the bass filter cutoff during drops, so the sawtooth ramp
    # combines with the built-in sine sweep in acid_303 for dramatic
    # filter motion (sine continuously breathes; sawtooth ramps up
    # then snaps back at the bar boundary).
    _emit_style_lfos(lines, style)
    lines.append("")

    # Per-style map of `apply` lines to emit inside each part, keyed by
    # role. Built once here so the part loop below can splice them in
    # at the right indent.
    apply_lines_per_role = _apply_lines_per_role(style)

    # Parts — names dedupe via suffixes; each part picks its active gens
    # by role.
    arrangement_names: list[str] = []
    used_names: dict[str, int] = {}
    for role, bars in profile.arrangement:
        if role in used_names:
            used_names[role] += 1
            part_name = f"{role}{used_names[role]}"
        else:
            used_names[role] = 1
            part_name = role
        arrangement_names.append(part_name)

        active = [
            spec for spec in gens
            if spec.type_ in _ROLE_GEN_TYPES.get(
                role, frozenset(s.type_ for s in gens)
            )
        ]
        # Always include at least one gen so empty parts don't render silent.
        if not active:
            active = gens[:1]

        lines.append(f"part {part_name} {bars} role={role}")
        for spec in active:
            lines.append(f"  {spec.handle}")
        # LFO applies for this role — sit alongside the gen handle
        # lines at the same indent.
        for apply_line in apply_lines_per_role.get(role, ()):
            lines.append(f"  {apply_line}")
        lines.append("")

    lines.append(f"play {' '.join(arrangement_names)}")
    lines.append("")
    return "\n".join(lines)
