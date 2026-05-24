"""Parser, AST shape, and arrangement expansion."""

from __future__ import annotations

import pytest

from slackbeatz.dsl.parser import ParseError, expand_arrangement, parse


def test_song_with_all_fields_parses() -> None:
    src = """
song "Demo"
  setup "studio.sb"
  tempo 128
  key   Am
  seed  42

gen kick rhythm euclid_drums swing=0.05
gen pad  chords euclid

part intro 16
  kick
  pad

play intro intro
"""
    fa = parse(src)
    s = fa.song
    assert s is not None
    assert s.name == "Demo"
    assert s.setup_ref == "studio.sb"
    assert s.tempo == 128
    assert s.key == "Am"
    assert s.seed == 42
    assert {g.handle for g in s.gens} == {"kick", "pad"}
    kick = next(g for g in s.gens if g.handle == "kick")
    assert kick.type_ == "rhythm" and kick.style == "euclid_drums"
    assert kick.knobs == {"swing": 0.05}
    assert len(s.parts) == 1 and s.parts[0].gens == ["kick", "pad"]


def test_setup_block_with_kit_overrides() -> None:
    src = """
setup "Rig"
inst kick ch=10 note=36
inst bass ch=2
kit drums ch=10 preset=909
  clap 75
"""
    fa = parse(src)
    assert fa.setup is not None
    assert fa.setup.name == "Rig"
    assert len(fa.setup.instruments) == 2
    assert len(fa.setup.kits) == 1
    assert fa.setup.kits[0].overrides == {"clap": 75}


def test_arrangement_expansion_with_groups() -> None:
    fa = parse(
        'song "x"\n'
        'gen k rhythm euclid_drums\n'
        'part p 1\n'
        '  k\n'
        'play p (p p)*2 p\n'
    )
    assert fa.song is not None and fa.song.play is not None
    assert expand_arrangement(fa.song.play.atoms) == ["p", "p", "p", "p", "p", "p"]


def test_unknown_knob_rejected_at_parse_time() -> None:
    with pytest.raises(ParseError, match="unknown knob 'whoops'"):
        parse(
            'song "x"\n'
            'gen k rhythm euclid_drums whoops=1\n'
            'part p 1\n'
            '  k\n'
            'play p\n'
        )


def test_play_unmatched_paren_errors() -> None:
    with pytest.raises(ParseError, match=r"missing '\)'"):
        parse(
            'song "x"\n'
            'gen k rhythm euclid_drums\n'
            'part p 1\n'
            '  k\n'
            'play (p\n'
        )
