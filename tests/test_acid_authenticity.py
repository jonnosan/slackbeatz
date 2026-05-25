"""Authenticity-tuning iteration 1 — acid style.

Covers the three engine-level changes that go together:

* The new ``chords/acid_stab`` algorithm (sparse, filter-enveloped).
* The ``slide_prob`` knob on ``bass/acid_303`` (portamento CC 65/5).
* The composer wiring for acid — bass knobs, ``acid_stab`` swap,
  top-level ``lfo acid_filter``, and per-part ``apply`` lines.
"""

from __future__ import annotations

import slackbeatz.generators  # noqa: F401 — register algorithms
from slackbeatz.compose import compose_from_text
from slackbeatz.dsl.parser import parse
from slackbeatz.engine.event import CC, Note, PitchBend
from slackbeatz.engine.scheduler import _instantiate_algorithm
from slackbeatz.generators.registry import REGISTRY
from slackbeatz.model.context import PartContext
from slackbeatz.setup.loader import load_setup, setup_from_ast
from slackbeatz.setup.resolve import resolve_song
from slackbeatz.theory.meter import COMMON_TIME


# --------------------------------------------------------------------------
# Composer output for an acid phrase
# --------------------------------------------------------------------------

def test_acid_composition_is_pure_303_no_lead() -> None:
    """Iteration 1.9: pure acid = 303 + drums + sweep candy. No lead
    voice. Reference tracks (Phuture, Aphex, TB-303+808 jams) are all
    single-303. Warm-analogue / SH-101-sequenced character belongs to
    the separate `warm_analogue` style (issue #67)."""
    sb = compose_from_text("Acid trax forever - take 2")
    # No lead / chord / stab voices at all.
    assert "gen lead" not in sb
    assert "gen stab" not in sb
    assert "sh101_arp" not in sb
    assert "acid_lead" not in sb
    assert "acid_stab" not in sb
    assert "sustained_dyad" not in sb
    # 303 bass + four_floor_house drums + acid_sweep candy must all
    # still be present.
    assert "gen bass" in sb and "acid_303" in sb
    assert "gen kick" in sb and "four_floor_house" in sb
    assert "gen sweep" in sb and "acid_sweep" in sb


def test_acid_composition_sets_new_bass_knobs() -> None:
    """Iteration 1.8: cycle=0 disables the bass's built-in CC74 LFO
    so the song-wide sawtooth apply-LFO is the sole CC74 driver
    (two sources fighting on the same CC was causing chaos)."""
    sb = compose_from_text("Acid trax forever - take 2")
    bass_line = next(l for l in sb.splitlines() if l.startswith("gen bass"))
    for knob in ("cycle=0", "resonance=120", "bend=120",
                 "intensity=1.0", "slide_prob=0.35", "evolution=0.4"):
        assert knob in bass_line, f"missing {knob} in: {bass_line}"


def test_acid_composition_lfo_period_is_song_length() -> None:
    """Iteration 1.8: LFO period = 160 bars = whole song length =
    one continuous filter ramp from start to end."""
    sb = compose_from_text("Acid trax forever - take 2")
    assert "lfo acid_filter shape=sawtooth bars=160 height=0.7" in sb


def test_acid_composition_declares_top_level_lfo() -> None:
    """Iteration 1.8: LFO is a whole-song sawtooth (bars=160) so the
    filter ramps continuously from closed at song-start to open at
    song-end. height=0.7 keeps bass audible during the intro
    (CC74 minimum ~19, not 0)."""
    sb = compose_from_text("Acid trax forever - take 2")
    assert "lfo acid_filter shape=sawtooth bars=160 height=0.7" in sb


def test_acid_composition_applies_lfo_in_every_rendered_part() -> None:
    """Iteration 1.8: apply line on every rendered part (intro / main
    / build / drop / outro) so the song-wide ramp is continuous.
    The acid arrangement renders 7 parts (intro main build drop
    main2 build2 drop2) — expect 7 apply lines."""
    sb = compose_from_text("Acid trax forever - take 2")
    apply_count = sb.count("apply acid_filter target=midi:ch:2/cc:74")
    assert apply_count == 7


def test_acid_composition_parses_and_resolves_cleanly() -> None:
    sb = compose_from_text("Acid trax forever - take 2")
    ast = parse(sb)
    resolved = resolve_song(ast.song, load_setup("gm"))
    assert "acid_filter" in resolved.lfos
    drop_part = resolved.parts["drop"]
    assert len(drop_part.lfo_applications) == 1
    app = drop_part.lfo_applications[0]
    assert app.lfo_name == "acid_filter"
    assert app.target.kind == "midi_cc"


def test_non_acid_styles_do_not_emit_lfo_block() -> None:
    # Generate a deep_techno song; assert no acid_filter LFO appears.
    sb = compose_from_text("Lonely night in Berlin")
    assert "lfo acid_filter" not in sb
    # Different style profiles should not have acid_stab.
    assert "acid_stab" not in sb


# --------------------------------------------------------------------------
# chords/acid_stab algorithm shape
# --------------------------------------------------------------------------

def _build_acid_stab_algo(*, knob_overrides=None):
    """Resolve a 4-bar acid_stab gen and return the algorithm + ctx."""
    setup_ast = parse(
        'setup "T"\n'
        'inst pad ch=3\n'
    ).setup
    setup = setup_from_ast(setup_ast)
    song_ast = parse(
        'song "S"\n'
        '  tempo 124\n'
        '  key Am\n'
        'gen stab chords acid_stab inst=pad\n'
        'part p 4\n'
        '  stab\n'
        'play p\n'
    ).song
    resolved = resolve_song(song_ast, setup)
    gen = resolved.gens["stab"]
    algo = _instantiate_algorithm(gen, knob_overrides=knob_overrides)
    part = resolved.parts["p"]
    ctx = PartContext(
        name="p", role="main", bars=4, ppq=96, tempo=124, key="Am",
        rng=__import__("random").Random(1),
        next_role=None, prev_role=None, transpose_semitones=0,
        scale_override=None, tension=1.0, meter=COMMON_TIME,
    )
    return algo, ctx, part


def test_acid_stab_emits_at_most_one_note_per_bar() -> None:
    algo, ctx, _ = _build_acid_stab_algo()
    events = list(algo.generate(ctx))
    notes_by_bar: dict[int, list[Note]] = {}
    ticks_per_bar = ctx.ticks_per_bar
    for ev in events:
        if isinstance(ev, Note):
            bar = ev.tick // ticks_per_bar
            notes_by_bar.setdefault(bar, []).append(ev)
    for bar, notes in notes_by_bar.items():
        assert len(notes) == 1, f"bar {bar} has {len(notes)} notes"


def test_acid_stab_emits_cc74_filter_envelope() -> None:
    algo, ctx, _ = _build_acid_stab_algo()
    events = list(algo.generate(ctx))
    cc74 = [ev for ev in events if isinstance(ev, CC) and ev.controller == 74]
    # Each bar gets a stab → an envelope of multiple CC74 events. 4 bars
    # × 8 envelope steps = 32 CC74 events (allowing for mute rolls).
    assert len(cc74) >= 8


def test_acid_stab_lands_on_step_six_of_each_bar() -> None:
    # Step 6 of 16 at PPQ=96 → tick offset = step_to_ticks(6) = 6 * (ppq/4) = 144.
    algo, ctx, _ = _build_acid_stab_algo()
    events = list(algo.generate(ctx))
    notes = [ev for ev in events if isinstance(ev, Note)]
    ticks_per_bar = ctx.ticks_per_bar
    for n in notes:
        within_bar = n.tick % ticks_per_bar
        # Step 6 at PPQ=96 lands at tick 144.
        assert within_bar == 144


# --------------------------------------------------------------------------
# bass/acid_303 slide_prob behaviour
# --------------------------------------------------------------------------

def _build_acid_303_algo(*, slide_prob: float):
    """Resolve a 2-bar acid_303 gen with a chosen slide_prob."""
    setup_ast = parse(
        'setup "T"\n'
        'inst bass ch=2\n'
    ).setup
    setup = setup_from_ast(setup_ast)
    song_ast = parse(
        'song "S"\n'
        '  tempo 124\n'
        '  key Am\n'
        f'gen bass bass acid_303 inst=bass slide_prob={slide_prob}\n'
        'part p 2\n'
        '  bass\n'
        'play p\n'
    ).song
    resolved = resolve_song(song_ast, setup)
    gen = resolved.gens["bass"]
    algo = _instantiate_algorithm(gen)
    ctx = PartContext(
        name="p", role="main", bars=2, ppq=96, tempo=124, key="Am",
        rng=__import__("random").Random(1),
        next_role=None, prev_role=None, transpose_semitones=0,
        scale_override=None, tension=1.0, meter=COMMON_TIME,
    )
    return algo, ctx


def test_acid_303_slide_prob_zero_emits_no_portamento_ccs() -> None:
    algo, ctx = _build_acid_303_algo(slide_prob=0.0)
    events = list(algo.generate(ctx))
    portamento_on = [
        ev for ev in events
        if isinstance(ev, CC) and ev.controller == 65
    ]
    glide_time = [
        ev for ev in events
        if isinstance(ev, CC) and ev.controller == 5
    ]
    assert portamento_on == []
    assert glide_time == []


def test_acid_303_slide_prob_nonzero_emits_portamento_latch() -> None:
    algo, ctx = _build_acid_303_algo(slide_prob=0.5)
    events = list(algo.generate(ctx))
    # Should latch CC 65 = 127 once at the start.
    portamento_on = [
        ev for ev in events
        if isinstance(ev, CC) and ev.controller == 65 and ev.value == 127
    ]
    assert len(portamento_on) == 1
    # CC 5 (glide time) should fire ahead of pitch-changing notes;
    # some values are 30 (slide), others 0 (skipped this note).
    glide_time = [
        ev for ev in events
        if isinstance(ev, CC) and ev.controller == 5
    ]
    # At least the initial CC 5 = 0 latch + a few per-note toggles.
    assert len(glide_time) >= 1
    assert any(ev.value == 30 for ev in glide_time), \
        "no slide-active CC5 events emitted at slide_prob=0.5"
