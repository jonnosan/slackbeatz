"""Phase D — `scene` block parsing and resolution.

The scene block carries per-channel mixer state (vol, pan, program,
mute, solo) for round-trip through the GUI's Save action. The shape is
deliberately recursive (SceneEntry can carry children) so future
phases (Surge patches, Sampler voices, per-part automation lanes) can
extend without parser changes.
"""

from __future__ import annotations

import pytest

import slackbeatz.generators  # noqa: F401 — register algorithms
from slackbeatz.dsl.parser import ParseError, parse
from slackbeatz.setup.loader import setup_from_ast
from slackbeatz.setup.resolve import ResolveError, resolve_song


def _setup():
    return setup_from_ast(parse(
        'setup "T"\n'
        'inst kick ch=10 note=36\n'
        'inst bass ch=2\n'
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
# Parser
# --------------------------------------------------------------------------

def test_parser_accepts_scene_block_with_ch_entries() -> None:
    fa = parse(
        'song "S"\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
        'scene\n'
        '  ch 2 vol=0.78 pan=0.5 program=33 mute=false solo=false\n'
        '  ch 10 vol=0.65\n'
    )
    assert fa.song.scene is not None
    entries = fa.song.scene.entries
    assert len(entries) == 2
    assert entries[0].scope == "ch"
    assert entries[0].selector == 2
    assert entries[0].knobs == {
        "vol": 0.78, "pan": 0.5, "program": 33,
        "mute": False, "solo": False,
    }
    assert entries[1].selector == 10
    assert entries[1].knobs == {"vol": 0.65}


def test_parser_rejects_duplicate_scene_block() -> None:
    with pytest.raises(ParseError, match="more than one scene block"):
        parse(
            'song "S"\n'
            'gen bass bass rolling\n'
            'part p 1\n'
            '  bass\n'
            'play p\n'
            'scene\n'
            '  ch 2 vol=0.5\n'
            'scene\n'
            '  ch 3 vol=0.5\n'
        )


def test_parser_rejects_unknown_scene_scope() -> None:
    # Forward-incompatibility guard — future scopes (surge, sampler,
    # part) should fail loudly today so a typo doesn't silently no-op.
    with pytest.raises(ParseError, match="unknown scene scope 'surge'"):
        parse(
            'song "S"\n'
            'gen bass bass rolling\n'
            'part p 1\n'
            '  bass\n'
            'play p\n'
            'scene\n'
            '  surge ch=2 patch="Acid 1"\n'
        )


def test_parser_rejects_invalid_channel_number() -> None:
    with pytest.raises(ParseError, match="channel out of 1..16"):
        parse(
            'song "S"\n'
            'gen bass bass rolling\n'
            'part p 1\n'
            '  bass\n'
            'play p\n'
            'scene\n'
            '  ch 99 vol=0.5\n'
        )


def test_parser_rejects_unknown_scene_knob() -> None:
    # Knob whitelist guards against typos like `volume=` vs `vol=`.
    with pytest.raises(ParseError, match="unknown knob 'volume'"):
        parse(
            'song "S"\n'
            'gen bass bass rolling\n'
            'part p 1\n'
            '  bass\n'
            'play p\n'
            'scene\n'
            '  ch 2 volume=0.5\n'
        )


def test_parser_parses_true_false_as_bool() -> None:
    fa = parse(
        'song "S"\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
        'scene\n'
        '  ch 2 mute=true solo=false\n'
    )
    assert fa.song.scene.entries[0].knobs == {"mute": True, "solo": False}


# --------------------------------------------------------------------------
# Resolver
# --------------------------------------------------------------------------

def test_resolver_propagates_scene_channels() -> None:
    r = _resolve(
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
        'scene\n'
        '  ch 2 vol=0.78 mute=false\n'
        '  ch 10 vol=0.65\n'
    )
    assert r.scene.channels == {
        2: {"vol": 0.78, "mute": False},
        10: {"vol": 0.65},
    }


def test_resolver_leaves_scene_empty_when_no_scene_block() -> None:
    r = _resolve(
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    assert r.scene.channels == {}


# --------------------------------------------------------------------------
# Player round-trip — save mute/solo into scene, restore on load
# --------------------------------------------------------------------------

def test_player_emits_scene_block_for_user_mutes(tmp_path) -> None:
    from slackbeatz.player import Player

    src_path = tmp_path / "song.sb"
    src_path.write_text(
        'song "S"\n'
        '  tempo 128\n'
        '  key Am\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    p = Player(port_name="test", setup_arg="gm")
    p.load_file(src_path)
    p.toggle_mute(2)
    p.toggle_mute(10)

    out_path = tmp_path / "round_trip.sb"
    p.save_state(out_path)

    written = out_path.read_text()
    assert "scene" in written
    assert "ch 2 mute=true" in written
    assert "ch 10 mute=true" in written


def test_player_emits_no_scene_block_when_nothing_muted(tmp_path) -> None:
    from slackbeatz.player import Player

    src_path = tmp_path / "song.sb"
    src_path.write_text(
        'song "S"\n'
        '  tempo 128\n'
        '  key Am\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    p = Player(port_name="test", setup_arg="gm")
    p.load_file(src_path)

    out_path = tmp_path / "round_trip.sb"
    p.save_state(out_path)

    # No scene block emitted — neither the standalone `scene` keyword
    # line nor any indented `ch N` lines should appear in output.
    written = out_path.read_text()
    assert "\nscene\n" not in written
    assert "scene\n" not in written.lstrip()  # not even at the top


def test_player_restores_mutes_from_scene_block_on_load(tmp_path) -> None:
    from slackbeatz.player import Player

    src_path = tmp_path / "song.sb"
    src_path.write_text(
        'song "S"\n'
        '  tempo 128\n'
        '  key Am\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
        'scene\n'
        '  ch 2 mute=true\n'
        '  ch 5 solo=true\n'
    )
    p = Player(port_name="test", setup_arg="gm")
    p.load_file(src_path)
    # _resolve_current is triggered by save_state — easiest way to
    # force the load-time scene application from a test.
    p._resolve_current()
    assert 2 in p._user_mutes
    assert 5 in p._solo


def test_player_strips_existing_scene_block_on_save(tmp_path) -> None:
    """Save → Open → Save should not accumulate duplicate scene blocks."""
    from slackbeatz.player import Player

    src_path = tmp_path / "song.sb"
    src_path.write_text(
        'song "S"\n'
        '  tempo 128\n'
        '  key Am\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
        'scene\n'
        '  ch 2 mute=true\n'
    )
    p = Player(port_name="test", setup_arg="gm")
    p.load_file(src_path)
    p._resolve_current()
    # User unmutes ch 2; saves. Should NOT emit a scene block (nothing
    # left to persist), and the source's stale `ch 2 mute=true` line
    # should be gone.
    p.toggle_mute(2)
    out_path = tmp_path / "round_trip.sb"
    p.save_state(out_path)
    written = out_path.read_text()
    assert "mute=true" not in written
    assert written.count("scene") == 0


def test_resolver_rejects_duplicate_channel_in_scene() -> None:
    with pytest.raises(ResolveError, match="duplicate scene entry for channel 2"):
        _resolve(
            'gen bass bass rolling\n'
            'part p 1\n'
            '  bass\n'
            'play p\n'
            'scene\n'
            '  ch 2 vol=0.78\n'
            '  ch 2 vol=0.65\n'
        )
