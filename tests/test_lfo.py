"""Tests for issue #65 — LFO support.

Covers the data-model math (shape sampling, target parsing), the
parser additions (`lfo` top-level + `apply` per-part), and the
resolver wiring.
"""

from __future__ import annotations

import math

import pytest

import slackbeatz.generators  # noqa: F401 — register algorithms
from slackbeatz.dsl.parser import ParseError, parse
from slackbeatz.model.lfo import LfoSpec, lfo_value_at, parse_target
from slackbeatz.setup.loader import setup_from_ast
from slackbeatz.setup.resolve import ResolveError, resolve_song


def _setup():
    return setup_from_ast(parse(
        'setup "T"\n'
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
# Shape math
# --------------------------------------------------------------------------

def test_sine_lfo_centres_on_offset() -> None:
    spec = LfoSpec(name="x", shape="sine", period_bars=4, height=1.0)
    # At phase 0, sin(0)=0 → raw=0.5 → effective_offset=0.5 → value 0.5
    assert abs(lfo_value_at(spec, 0.0) - 0.5) < 1e-6


def test_sawtooth_ramps_linearly() -> None:
    """Sawtooth at height=1.0 should ramp the full [0, 1] range
    (iteration 1.8 fix — pre-fix sawtooth was clamped to [0, 0.5]
    because default offset was 0.0 not 0.5)."""
    spec = LfoSpec(name="x", shape="sawtooth", period_bars=4, height=1.0)
    # Phase 0 → 0, phase 0.5 → 0.5, phase 1 → 1 (within rounding).
    assert abs(lfo_value_at(spec, 0.0) - 0.0) < 1e-6
    assert abs(lfo_value_at(spec, 0.5) - 0.5) < 1e-6
    # Phase ~0.999 should be near 1; exactly 1 wraps back to 0 per spec.
    assert lfo_value_at(spec, 0.999) > 0.99


def test_sawtooth_height_half_centres_around_offset() -> None:
    """Sawtooth at offset=0.5 height=0.5 should ramp [0.25, 0.75] —
    a half-range sweep centred on the middle."""
    spec = LfoSpec(name="x", shape="sawtooth", period_bars=4,
                   height=0.5, offset=0.5)
    assert abs(lfo_value_at(spec, 0.0) - 0.25) < 1e-6
    assert abs(lfo_value_at(spec, 0.5) - 0.50) < 1e-6
    assert lfo_value_at(spec, 0.999) > 0.74


def test_sawtooth_offset_shifts_range() -> None:
    """Sawtooth at offset=0.3 height=0.4 should ramp [0.1, 0.5]."""
    spec = LfoSpec(name="x", shape="sawtooth", period_bars=4,
                   height=0.4, offset=0.3)
    assert abs(lfo_value_at(spec, 0.0) - 0.1) < 1e-6
    assert lfo_value_at(spec, 0.999) > 0.49


def test_square_uses_width_as_duty_cycle() -> None:
    spec = LfoSpec(name="x", shape="square", period_bars=4, width=0.3, height=1.0)
    # Below duty cycle → high.
    assert lfo_value_at(spec, 0.1) > lfo_value_at(spec, 0.5)


def test_noise_returns_in_range() -> None:
    import random
    spec = LfoSpec(name="x", shape="noise", period_bars=4, height=1.0)
    rng = random.Random(42)
    for phase in [0.0, 0.1, 0.5, 0.9]:
        v = lfo_value_at(spec, phase, rng)
        assert 0.0 <= v <= 1.0


# --------------------------------------------------------------------------
# Target parsing
# --------------------------------------------------------------------------

def test_parse_target_midi_cc() -> None:
    t = parse_target("midi:ch:2/cc:74")
    assert t.kind == "midi_cc"
    assert t.ref == "ch:2/cc:74"


def test_parse_target_surge() -> None:
    t = parse_target("surge:/param/a/filter_a/cutoff/value")
    assert t.kind == "surge_param"
    assert t.ref == "/param/a/filter_a/cutoff/value"


def test_parse_target_pattern_and_feel() -> None:
    t1 = parse_target("pattern:bass:swing")
    assert t1.kind == "pattern_knob"
    t2 = parse_target("feel:bass:humanize")
    assert t2.kind == "feel_knob"


def test_parse_target_unknown_prefix_raises() -> None:
    with pytest.raises(ValueError):
        parse_target("unknown:something")


# --------------------------------------------------------------------------
# Parser additions
# --------------------------------------------------------------------------

def test_parser_accepts_lfo_top_level() -> None:
    fa = parse(
        'song "S"\n'
        'gen bass bass rolling\n'
        'lfo slow_filter shape=sine bars=8 height=0.6\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    assert len(fa.song.lfos) == 1
    lfo = fa.song.lfos[0]
    assert lfo.name == "slow_filter"
    assert lfo.knobs["shape"] == "sine"
    assert lfo.knobs["bars"] == 8


def test_parser_rejects_lfo_without_shape() -> None:
    with pytest.raises(ParseError, match="lfo requires shape"):
        parse(
            'song "S"\n'
            'gen bass bass rolling\n'
            'lfo bad bars=8\n'
            'part p 1\n'
            '  bass\n'
            'play p\n'
        )


def test_parser_rejects_duplicate_lfo_name() -> None:
    with pytest.raises(ParseError, match="duplicate lfo name"):
        parse(
            'song "S"\n'
            'gen bass bass rolling\n'
            'lfo l1 shape=sine bars=8\n'
            'lfo l1 shape=square bars=4\n'
            'part p 1\n'
            '  bass\n'
            'play p\n'
        )


def test_parser_accepts_apply_inside_part() -> None:
    fa = parse(
        'song "S"\n'
        'gen bass bass rolling\n'
        'lfo l shape=sine bars=8\n'
        'part p 1\n'
        '  bass\n'
        '  apply l target=midi:ch:2/cc:74\n'
        'play p\n'
    )
    part = fa.song.parts[0]
    assert len(part.lfo_apply_lines) == 1
    name, target, _ = part.lfo_apply_lines[0]
    assert name == "l"
    assert target == "midi:ch:2/cc:74"


def test_parser_rejects_apply_without_target() -> None:
    with pytest.raises(ParseError, match="apply requires target|expected: apply"):
        parse(
            'song "S"\n'
            'gen bass bass rolling\n'
            'lfo l shape=sine bars=8\n'
            'part p 1\n'
            '  bass\n'
            '  apply l\n'
            'play p\n'
        )


# --------------------------------------------------------------------------
# Resolver wiring
# --------------------------------------------------------------------------

def test_resolver_propagates_lfos_and_applications() -> None:
    r = _resolve(
        'gen bass bass rolling\n'
        'lfo sweep shape=sine bars=4 height=0.8\n'
        'part p 1\n'
        '  bass\n'
        '  apply sweep target=midi:ch:2/cc:74\n'
        'play p\n'
    )
    assert "sweep" in r.lfos
    assert r.lfos["sweep"].shape == "sine"
    assert r.lfos["sweep"].period_bars == 4
    apps = r.parts["p"].lfo_applications
    assert len(apps) == 1
    assert apps[0].lfo_name == "sweep"
    assert apps[0].target.kind == "midi_cc"


def test_resolver_rejects_apply_referencing_undefined_lfo() -> None:
    with pytest.raises(ResolveError, match="undefined lfo 'nonesuch'"):
        _resolve(
            'gen bass bass rolling\n'
            'part p 1\n'
            '  bass\n'
            '  apply nonesuch target=midi:ch:2/cc:74\n'
            'play p\n'
        )


def test_resolver_rejects_unknown_lfo_shape() -> None:
    with pytest.raises(ResolveError, match="unknown shape"):
        _resolve(
            'gen bass bass rolling\n'
            'lfo bad shape=triangle bars=4\n'
            'part p 1\n'
            '  bass\n'
            'play p\n'
        )


# --------------------------------------------------------------------------
# Scheduler emission — MIDI CC LFO produces CC events
# --------------------------------------------------------------------------

def test_scheduler_emits_cc_events_for_midi_cc_lfo() -> None:
    from slackbeatz.engine.scheduler import render_events
    r = _resolve(
        'gen bass bass rolling\n'
        'lfo sweep shape=sine bars=1 height=1.0\n'
        'part p 1\n'
        '  bass\n'
        '  apply sweep target=midi:ch:2/cc:74\n'
        'play p\n'
    )
    events = render_events(r)
    cc_events = [
        msg for _tick, msg in events
        if msg.type == "control_change" and msg.control == 74 and msg.channel == 1
    ]
    # 1 bar × PPQ=96 / 24 ticks per step = 4 LFO events per bar.
    assert len(cc_events) >= 1
    for msg in cc_events:
        assert 0 <= msg.value <= 127
