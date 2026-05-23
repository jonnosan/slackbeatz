"""Engine helpers + the seed-reproducibility contract."""

from __future__ import annotations

import random

import slackbeatz.generators  # noqa: F401 — register algorithms
from slackbeatz.dsl.parser import parse
from slackbeatz.engine.clock import PPQ, TempoMap, TempoSegment, bars_to_ticks
from slackbeatz.engine.scheduler import derive_seed, render_events
from slackbeatz.generators._shared import (
    HitParams,
    euclid,
    humanize_hit,
    sidechain_envelope,
)
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


# --- Per-hit shaping helpers ----------------------------------------------

def test_humanize_hit_baseline_no_knobs() -> None:
    """With all chance knobs at default, only the vel_jitter applies."""
    rng = random.Random(42)
    params = HitParams(base_vel=100, intensity=1.0, vel_jitter=8)
    vel, tick = humanize_hit(params, rng, step=0, tick=100)
    assert 92 <= vel <= 108
    assert tick == 100  # humanize=0


def test_humanize_hit_drop_prob_one_always_drops() -> None:
    params = HitParams(base_vel=100, drop_prob=1.0)
    assert humanize_hit(params, random.Random(0), step=0, tick=0) is None


def test_humanize_hit_drop_prob_zero_never_drops() -> None:
    params = HitParams(base_vel=100, drop_prob=0.0)
    # Loop a bunch of seeds to be sure.
    for s in range(20):
        assert humanize_hit(params, random.Random(s), step=0, tick=0) is not None


def test_humanize_hit_humanize_offsets_tick() -> None:
    params = HitParams(base_vel=100, humanize=10, vel_jitter=0)
    rng = random.Random(7)
    _, tick = humanize_hit(params, rng, step=0, tick=500)
    assert 490 <= tick <= 510


def test_humanize_hit_accent_boosts_periodic() -> None:
    params = HitParams(base_vel=80, vel_jitter=0, accent=4)
    rng = random.Random(0)
    boosted, _ = humanize_hit(params, rng, step=0, tick=0)
    normal, _ = humanize_hit(params, rng, step=1, tick=0)
    assert boosted == 80 + 12
    assert normal == 80


# --- Sidechain envelope ---------------------------------------------------

def test_sidechain_envelope_at_beat_starts_is_duck() -> None:
    # At tick 0 (beat 1) the multiplier should be the duck floor.
    assert sidechain_envelope(0, PPQ, duck=0.5) == 0.5


def test_sidechain_envelope_mid_beat_is_full() -> None:
    # Past the half-beat mark the envelope is fully restored.
    assert sidechain_envelope(PPQ // 2 + 10, PPQ, duck=0.5) == 1.0


def test_sidechain_envelope_disabled_when_duck_is_one() -> None:
    for pos in (0, 100, 240, 479):
        assert sidechain_envelope(pos, PPQ, duck=1.0) == 1.0


def test_sidechain_envelope_resets_each_beat() -> None:
    # tick=PPQ is exactly beat 2's start — should duck again.
    assert sidechain_envelope(PPQ, PPQ, duck=0.5) == 0.5
    # … and slowly come back up.
    mid_quarter = sidechain_envelope(PPQ + PPQ // 4, PPQ, duck=0.5)
    assert 0.5 < mid_quarter < 1.0
