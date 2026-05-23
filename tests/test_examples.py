"""Smoke-render each bundled example so a busted style algorithm shows up
in CI rather than only when someone plays the song."""

from __future__ import annotations

from pathlib import Path

import pytest

import slackbeatz.generators  # noqa: F401 — register algorithms
from slackbeatz.dsl.parser import parse_file
from slackbeatz.engine.scheduler import render_events
from slackbeatz.setup.loader import load_setup, setup_from_ast
from slackbeatz.setup.resolve import resolve_song

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize(
    "song_file",
    sorted(p.name for p in EXAMPLES.glob("*.sb") if p.name != "studio.sb"),
)
def test_example_renders_to_events(song_file: str) -> None:
    p = EXAMPLES / song_file
    fa = parse_file(p)
    assert fa.song is not None
    if fa.song.setup_ref is not None:
        setup = load_setup(fa.song.setup_ref, base_path=p.parent)
    else:
        assert fa.setup is not None
        setup = setup_from_ast(fa.setup)
    resolved = resolve_song(fa.song, setup)
    events = render_events(resolved)
    assert events, f"{song_file} rendered to zero events"
