"""Phase 4 — per-part algorithm overrides.

Each indented gen line inside a `part` block accepts an optional
second token naming the algorithm to use for that handle within
that part. A `style=NAME` knob on the part header is shorthand
for one such override per handle.
"""

from __future__ import annotations

import pytest

import slackbeatz.generators  # noqa: F401 — register algorithms
from slackbeatz.dsl.parser import ParseError, parse
from slackbeatz.engine.scheduler import _instantiate_algorithm, render_events
from slackbeatz.generators.registry import REGISTRY
from slackbeatz.setup.loader import setup_from_ast
from slackbeatz.setup.resolve import ResolveError, resolve_song


def _setup():
    return setup_from_ast(parse(
        'setup "T"\n'
        'inst kick ch=10 note=36\n'
        'inst bass ch=2\n'
        'inst pad  ch=3\n'
        'inst lead ch=4\n'
    ).setup)


def _resolve(body: str):
    song = parse(
        'song "S"\n'
        '  tempo 128\n'
        '  key Am\n'
        + body
    ).song
    return resolve_song(song, _setup())


# --------------------------------------------------------------------------
# Parser — accepts the new `<handle> <algorithm>` form
# --------------------------------------------------------------------------

def test_parser_accepts_handle_only_for_backwards_compat() -> None:
    fa = parse(
        'song "S"\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    part = fa.song.parts[0]
    assert part.gens == ["bass"]
    assert part.algorithm_overrides == {}


def test_parser_stashes_per_part_algorithm_override() -> None:
    fa = parse(
        'song "S"\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass gallop\n'
        'play p\n'
    )
    part = fa.song.parts[0]
    assert part.gens == ["bass"]
    assert part.algorithm_overrides == {"bass": "gallop"}


def test_parser_rejects_three_tokens_on_part_gen_line() -> None:
    with pytest.raises(ParseError, match="expected '<handle>'"):
        parse(
            'song "S"\n'
            'gen bass bass rolling\n'
            'part p 1\n'
            '  bass gallop oops\n'
            'play p\n'
        )


def test_parser_rejects_duplicate_override_for_same_handle() -> None:
    with pytest.raises(ParseError, match="duplicate algorithm override"):
        parse(
            'song "S"\n'
            'gen bass bass rolling\n'
            'part p 1\n'
            '  bass gallop\n'
            '  bass rolling\n'
            'play p\n'
        )


# --------------------------------------------------------------------------
# Resolver — propagates overrides + validates them
# --------------------------------------------------------------------------

def test_resolver_propagates_explicit_override_to_resolved_part() -> None:
    r = _resolve(
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass gallop\n'
        'play p\n'
    )
    assert r.parts["p"].algorithm_overrides == {"bass": "gallop"}


def test_resolver_leaves_overrides_empty_when_part_uses_defaults() -> None:
    r = _resolve(
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    assert r.parts["p"].algorithm_overrides == {}


def test_resolver_rejects_unknown_algorithm_with_helpful_message() -> None:
    with pytest.raises(ResolveError, match="unknown algorithm 'nope'"):
        _resolve(
            'gen bass bass rolling\n'
            'part p 1\n'
            '  bass nope\n'
            'play p\n'
        )


def test_resolver_lists_available_algorithms_in_error() -> None:
    # The available-algorithms list narrows to the gen's type, so a
    # bass override only suggests bass algorithms (not rhythm names).
    with pytest.raises(ResolveError, match=r"\['acid_303'"):
        _resolve(
            'gen bass bass rolling\n'
            'part p 1\n'
            '  bass not_a_real_algorithm\n'
            'play p\n'
        )


def test_resolver_rejects_override_for_undeclared_handle() -> None:
    with pytest.raises(ResolveError, match="not declared at song level"):
        _resolve(
            'gen bass bass rolling\n'
            'part p 1\n'
            '  bass gallop\n'
            '  ghost gallop\n'
            'play p\n'
        )


# --------------------------------------------------------------------------
# `style=NAME` part shorthand expands via StyleProfile
# --------------------------------------------------------------------------

def test_style_shorthand_expands_to_per_handle_overrides() -> None:
    # `style=psytrance` should map every handle to the algorithm
    # the psytrance StyleProfile assigns to that handle's gen type:
    #   kick (rhythm) → gallop_kick
    #   bass (bass)   → gallop
    #   lead (melody) → psy_lead
    r = _resolve(
        'gen kick rhythm euclid_drums\n'
        'gen bass bass   rolling\n'
        'gen lead melody euclid_riff\n'
        'part p 1 style=psytrance\n'
        '  kick\n'
        '  bass\n'
        '  lead\n'
        'play p\n'
    )
    assert r.parts["p"].algorithm_overrides == {
        "kick": "gallop_kick",
        "bass": "gallop",
        "lead": "psy_lead",
    }


def test_explicit_handle_override_beats_style_shorthand() -> None:
    # `style=psytrance` expands bass → gallop, but the explicit
    # `bass rolling` line overrides that to keep the song default.
    r = _resolve(
        'gen kick rhythm euclid_drums\n'
        'gen bass bass   gallop\n'
        'part p 1 style=psytrance\n'
        '  kick\n'
        '  bass rolling\n'
        'play p\n'
    )
    assert r.parts["p"].algorithm_overrides == {
        "kick": "gallop_kick",
        "bass": "rolling",
    }


def test_style_shorthand_rejects_unknown_style() -> None:
    with pytest.raises(ResolveError, match="style='nonesuch' has no algorithm"):
        _resolve(
            'gen kick rhythm euclid_drums\n'
            'part p 1 style=nonesuch\n'
            '  kick\n'
            'play p\n'
        )


def test_style_shorthand_rejects_gen_type_not_in_profile() -> None:
    # acid's StyleProfile has no melody handle — `style=acid` on a
    # part with a melody handle should error rather than silently
    # leaving that handle on its song-level algorithm.
    with pytest.raises(ResolveError, match="style='acid' has no algorithm for 'melody'"):
        _resolve(
            'gen lead melody euclid_riff\n'
            'part p 1 style=acid\n'
            '  lead\n'
            'play p\n'
        )


# --------------------------------------------------------------------------
# Scheduler — actually instantiates the overridden algorithm
# --------------------------------------------------------------------------

def test_scheduler_picks_overridden_algorithm_class() -> None:
    # A song that declares bass=rolling but routes one part through
    # bass=gallop should instantiate two different generator classes
    # across the arrangement.
    r = _resolve(
        'gen bass bass rolling\n'
        'part default 1\n'
        '  bass\n'
        'part heavy 1\n'
        '  bass gallop\n'
        'play default heavy\n'
    )
    # The default part has no override → algorithm stays "rolling".
    assert r.parts["default"].algorithm_overrides == {}
    # The heavy part overrides bass → "gallop".
    assert r.parts["heavy"].algorithm_overrides == {"bass": "gallop"}

    rolling_cls = REGISTRY[("bass", "rolling")]
    gallop_cls = REGISTRY[("bass", "gallop")]
    assert rolling_cls is not gallop_cls

    gen = r.gens["bass"]
    default_instance = _instantiate_algorithm(gen, algorithm="rolling")
    overridden_instance = _instantiate_algorithm(gen, algorithm="gallop")
    assert isinstance(default_instance, rolling_cls)
    assert isinstance(overridden_instance, gallop_cls)


def test_song_with_per_part_override_renders_without_crashing() -> None:
    # End-to-end smoke: the override survives all the way through
    # render_events without raising. The two parts use distinct
    # bass algorithms but share the same Instrument.
    r = _resolve(
        'gen kick rhythm euclid_drums\n'
        'gen bass bass rolling\n'
        'part a 1\n'
        '  kick\n'
        '  bass\n'
        'part b 1\n'
        '  kick\n'
        '  bass gallop\n'
        'play a b\n'
    )
    events = render_events(r)
    assert events  # at least some notes came out
    # Every bass note still routes to channel 2 (the song-level
    # Instrument binding survives the algorithm swap).
    bass_notes = [
        msg for _, msg in events
        if msg.type == "note_on" and msg.channel == 1  # 0-indexed ch 2
    ]
    assert bass_notes
