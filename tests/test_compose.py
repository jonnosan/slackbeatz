"""Tests for the text → song composer."""

from __future__ import annotations

import slackbeatz.generators  # noqa: F401 — register algorithms
from slackbeatz.compose import (
    compose_from_text,
    extract_title,
    pick_style,
    score_sentiment,
)
from slackbeatz.dsl.parser import parse
from slackbeatz.setup.loader import load_setup
from slackbeatz.setup.resolve import resolve_song


# --- Title extraction -----------------------------------------------------

def test_extract_title_first_sentence() -> None:
    assert extract_title("Lonely night. The rain was cold.") == "Lonely night"


def test_extract_title_strips_punctuation() -> None:
    assert extract_title("  — Cosmic dance!! ") == "Cosmic dance"


def test_extract_title_caps_at_8_words() -> None:
    long = "one two three four five six seven eight nine ten"
    assert extract_title(long) == "one two three four five six seven eight"


def test_extract_title_empty_input() -> None:
    assert extract_title("") == "Untitled"


def test_extract_title_only_punctuation() -> None:
    assert extract_title("...?!?") == "Untitled"


# --- Style picking --------------------------------------------------------

def test_pick_style_vaporwave_keywords() -> None:
    assert pick_style("Sunset over neon plaza") == "vaporwave"


def test_pick_style_psytrance_keywords() -> None:
    assert pick_style("Cosmic mushroom dance — third eye opening") == "psytrance"


def test_pick_style_acid_keywords() -> None:
    assert pick_style("Acid trax in Chicago") == "acid"


def test_pick_style_drum_and_bass_keywords() -> None:
    assert pick_style("Jungle rolling junglist break") == "drum_and_bass"


def test_pick_style_garage_keywords() -> None:
    assert pick_style("UK 2step london garage shuffle") == "garage"


def test_pick_style_dub_techno_keywords() -> None:
    assert pick_style("Submerged echo in the fog") == "dub_techno"


def test_pick_style_deep_techno_keywords() -> None:
    assert pick_style("Berlin warehouse midnight machine") == "deep_techno"


def test_pick_style_no_keywords_falls_back_to_euclid() -> None:
    assert pick_style("Hello world") == "euclid"


# --- Sentiment scoring ----------------------------------------------------

def test_score_sentiment_dark_input() -> None:
    assert score_sentiment("dark lonely night sorrow") < 0


def test_score_sentiment_bright_input() -> None:
    assert score_sentiment("happy summer dawn golden") > 0


def test_score_sentiment_neutral() -> None:
    assert score_sentiment("hello world") == 0


# --- End-to-end determinism + sensitivity --------------------------------

def test_compose_deterministic() -> None:
    a = compose_from_text("Lonely night at the warehouse")
    b = compose_from_text("Lonely night at the warehouse")
    assert a == b


def test_compose_case_sensitive() -> None:
    a = compose_from_text("lonely night at the warehouse")
    b = compose_from_text("Lonely Night at the Warehouse")
    assert a != b
    # Both should pick the same style (keyword matching is case-insensitive)
    style_a = next(g.style for g in parse(a).song.gens)
    style_b = next(g.style for g in parse(b).song.gens)
    assert style_a == style_b


def test_compose_single_char_change_differs() -> None:
    a = compose_from_text("Lonely night")
    b = compose_from_text("Lonely nights")  # one letter
    assert a != b


# --- Composed song actually resolves ------------------------------------

def test_algorithm_per_type_overrides_individual_gen_lines() -> None:
    """``algorithm_per_type={'chords': 'rhodes_chord'}`` rewrites
    only the chord gen line; bass / melody / candy stay on the
    primary style's defaults.

    Uses deep_techno here because the acid profile no longer carries
    a chord gen (iteration 1.6: bass + lead interplay replaces the
    chord stab). Any style with a chord gen would do.
    """
    sb = compose_from_text(
        "deep warehouse berlin",
        style_override="deep_techno",
        algorithm_per_type={"chords": "rhodes_chord"},
    )
    found = {}
    for line in sb.splitlines():
        stripped = line.strip()
        if not stripped.startswith("gen "):
            continue
        # gen <handle> <type> <algorithm> [knobs…]
        bits = stripped.split()
        if len(bits) >= 4:
            handle, type_, algorithm = bits[1], bits[2], bits[3]
            found[(handle, type_)] = algorithm
    chord_algos = {s for (_h, t), s in found.items() if t == "chords"}
    other_algos = {s for (_h, t), s in found.items() if t != "chords"}
    assert chord_algos == {"rhodes_chord"}, found
    assert other_algos and "rhodes_chord" not in other_algos, found


def test_algorithm_per_type_none_is_byte_identical() -> None:
    """Passing ``algorithm_per_type=None`` (or omitting it) must
    reproduce the byte-identical .sb the composer wrote pre-feature."""
    a = compose_from_text("deep warehouse berlin", style_override="deep_techno")
    b = compose_from_text("deep warehouse berlin", style_override="deep_techno",
                          algorithm_per_type=None)
    c = compose_from_text("deep warehouse berlin", style_override="deep_techno",
                          algorithm_per_type={})
    assert a == b == c


def test_composed_song_resolves_against_gm_setup() -> None:
    """Every style + handle combo the composer emits must bind to the
    bundled `gm` setup."""
    setup = load_setup("gm")
    for text in [
        "Lonely night at the warehouse",      # deep_techno
        "Cosmic mushroom dance",              # psytrance
        "Sunset over Plaza",                  # vaporwave
        "Acid trax forever",                  # acid
        "Smoke and fog and rain submerged",   # dub_techno
        "Jungle rolling neurofunk",           # drum_and_bass
        "UK 2step london garage",             # garage
        "Hello world",                        # euclid fallback
    ]:
        sb = compose_from_text(text)
        fa = parse(sb)
        assert fa.song is not None, text
        resolved = resolve_song(fa.song, setup)
        # All gens must have bound to a real inst/kit.
        for handle, gen in resolved.gens.items():
            assert (gen.instrument is not None) or (gen.kit is not None), (
                f"{text!r}: gen {handle!r} resolved to neither inst nor kit"
            )
