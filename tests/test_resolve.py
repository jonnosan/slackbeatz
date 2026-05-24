"""Setup loading + song-to-setup binding."""

from __future__ import annotations

import pytest

from slackbeatz.dsl.parser import parse
from slackbeatz.setup.loader import setup_from_ast
from slackbeatz.setup.resolve import ResolveError, resolve_song


def _setup():
    return setup_from_ast(parse(
        'setup "T"\n'
        'inst kick  ch=10 note=36\n'
        'inst bass  ch=2\n'
        'kit  drums ch=10 preset=gm\n'
    ).setup)


def _song(body: str):
    return parse(
        'song "S"\n'
        '  tempo 128\n'
        '  key Am\n'
        + body
    ).song


def test_resolve_binds_inst() -> None:
    s = _song(
        'gen kick rhythm euclid_drums\n'
        'gen bass bass   euclid\n'
        'part p 1\n  kick\n  bass\nplay p\n'
    )
    r = resolve_song(s, _setup())
    assert r.gens["kick"].instrument is not None
    assert r.gens["kick"].instrument.note == 36
    assert r.gens["bass"].instrument is not None and r.gens["bass"].instrument.is_pitched


def test_rhythm_pointed_at_pitched_inst_errors() -> None:
    s = _song(
        'gen lead rhythm euclid_drums\n'
        'part p 1\n  lead\nplay p\n'
    )
    # `lead` doesn't exist as an inst in our setup, and `bass` is pitched.
    s = _song(
        'gen bass rhythm euclid_drums\n'
        'part p 1\n  bass\nplay p\n'
    )
    with pytest.raises(ResolveError, match="is pitched"):
        resolve_song(s, _setup())


def test_pitched_pointed_at_drum_inst_errors() -> None:
    s = _song(
        'gen kick bass rolling\n'
        'part p 1\n  kick\nplay p\n'
    )
    with pytest.raises(ResolveError, match="one-shot drum"):
        resolve_song(s, _setup())


def test_unknown_part_in_play_errors() -> None:
    s = _song(
        'gen kick rhythm euclid_drums\n'
        'part p 1\n  kick\nplay nope\n'
    )
    with pytest.raises(ResolveError, match="undeclared part"):
        resolve_song(s, _setup())


def test_inline_ch_fallback_works_with_empty_setup() -> None:
    from slackbeatz.setup.model import Setup
    s = _song(
        'gen kick rhythm euclid_drums ch=10 note=36\n'
        'part p 1\n  kick\nplay p\n'
    )
    r = resolve_song(s, Setup(name="(empty)"))
    assert r.gens["kick"].instrument is not None
    assert r.gens["kick"].instrument.channel == 10
    assert r.gens["kick"].instrument.note == 36
