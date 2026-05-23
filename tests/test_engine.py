"""Engine helpers + the seed-reproducibility contract."""

from __future__ import annotations

import slackbeatz.generators  # noqa: F401 — register algorithms
from slackbeatz.dsl.parser import parse
from slackbeatz.engine.clock import PPQ, TempoMap, TempoSegment, bars_to_ticks
from slackbeatz.engine.scheduler import derive_seed, render_events
from slackbeatz.generators._shared import euclid
from slackbeatz.setup.loader import setup_from_ast
from slackbeatz.setup.resolve import resolve_song


def test_euclid_kick_is_four_on_floor() -> None:
    assert euclid(4, 16) == [
        True, False, False, False,
        True, False, False, False,
        True, False, False, False,
        True, False, False, False,
    ]


def test_euclid_handles_edges() -> None:
    assert euclid(0, 16) == [False] * 16
    assert euclid(16, 16) == [True] * 16


def test_tempo_map_16_bars_at_128bpm_is_30s() -> None:
    tm = TempoMap([TempoSegment(0, bars_to_ticks(16), 128)])
    # 16 bars × 4 beats × (60s / 128bpm) = 30s
    assert tm.time_at(bars_to_ticks(16)) == 30.0


def test_derive_seed_is_deterministic() -> None:
    a = derive_seed(42, "drop", "kick")
    b = derive_seed(42, "drop", "kick")
    c = derive_seed(43, "drop", "kick")
    assert a == b
    assert a != c


_SETUP = """
setup "T"
inst kick ch=10 note=36
inst bass ch=2
"""

_SONG = """
song "R"
  tempo 130
  key   Am
  seed  {seed}
gen kick rhythm euclid
gen bass bass   euclid
part p 4
  kick
  bass
play p p
"""


def _render(seed: int) -> list[tuple[int, str, int, int]]:
    setup = setup_from_ast(parse(_SETUP).setup)
    song = parse(_SONG.format(seed=seed)).song
    resolved = resolve_song(song, setup)
    return [
        (t, m.type, getattr(m, "note", -1), getattr(m, "velocity", -1))
        for t, m in render_events(resolved)
        if m.type == "note_on"
    ]


def test_same_seed_byte_identical() -> None:
    assert _render(42) == _render(42)


def test_different_seed_perturbs_but_keeps_count() -> None:
    a, b = _render(42), _render(99)
    assert len(a) == len(b)  # rhythmic skeleton stable
    assert a != b  # velocity jitter changes


def test_ppq_is_480() -> None:
    assert PPQ == 480
