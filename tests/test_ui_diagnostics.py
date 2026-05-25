"""Tests for :mod:`slackbeatz.ui.diagnostics` — stale-file warnings."""

from __future__ import annotations

import slackbeatz.generators  # noqa: F401 — register algorithms
from slackbeatz.dsl.parser import parse
from slackbeatz.ui.diagnostics import (
    SessionWarning,
    check_for_warnings,
    format_warning_summary,
)


def _ast(body: str):
    return parse(body)


# --------------------------------------------------------------------------
# check_for_warnings
# --------------------------------------------------------------------------

def test_clean_file_produces_no_warnings() -> None:
    fa = _ast(
        'song "S"\n'
        '  setup "gm"\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    assert check_for_warnings(fa) == []


def test_unknown_bundled_setup_flagged() -> None:
    fa = _ast(
        'song "S"\n'
        '  setup "nonesuch"\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    warnings = check_for_warnings(fa)
    assert len(warnings) == 1
    assert warnings[0].kind == "unknown_setup"
    assert "nonesuch" in warnings[0].message


def test_path_setup_ref_is_not_flagged() -> None:
    # Path refs are resolved + raised at load time — don't double-warn
    # here.
    fa = _ast(
        'song "S"\n'
        '  setup "./local.sb"\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    warnings = check_for_warnings(fa)
    assert all(w.kind != "unknown_setup" for w in warnings)


def test_unknown_algorithm_flagged_with_available_list() -> None:
    fa = _ast(
        'song "S"\n'
        'gen bass bass not_a_real_algo\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    warnings = check_for_warnings(fa)
    assert len(warnings) == 1
    w = warnings[0]
    assert w.kind == "unknown_algorithm"
    assert "bass" in w.message
    assert "not_a_real_algo" in w.message
    # The error message should list real bass algorithms.
    assert "rolling" in w.message


def test_unknown_voice_type_flagged() -> None:
    fa = _ast(
        'song "S"\n'
        'gen bass notatype rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    warnings = check_for_warnings(fa)
    kinds = [w.kind for w in warnings]
    assert "unknown_voice_type" in kinds
    # Algorithm check is skipped when the type is unknown.
    assert "unknown_algorithm" not in kinds


def test_duplicate_handle_flagged() -> None:
    fa = _ast(
        'song "S"\n'
        'gen bass bass rolling\n'
        'gen bass bass rolling\n'
        'part p 1\n'
        '  bass\n'
        'play p\n'
    )
    warnings = check_for_warnings(fa)
    kinds = [w.kind for w in warnings]
    assert "duplicate_handle" in kinds


def test_warning_line_numbers_match_source() -> None:
    fa = _ast(
        'song "S"\n'      # line 1
        'gen bass bass not_a_real_algo\n'   # line 2
        'part p 1\n'      # line 3
        '  bass\n'        # line 4
        'play p\n'        # line 5
    )
    warnings = check_for_warnings(fa)
    assert warnings[0].line == 2


def test_no_song_block_produces_no_warnings() -> None:
    fa = _ast(
        'setup "T"\n'
        'inst kick ch=10 note=36\n'
    )
    assert check_for_warnings(fa) == []


# --------------------------------------------------------------------------
# format_warning_summary
# --------------------------------------------------------------------------

def test_format_summary_empty() -> None:
    assert format_warning_summary([]) == ""


def test_format_summary_single_warning() -> None:
    ws = [SessionWarning(kind="unknown_algorithm", message="x", line=1)]
    assert format_warning_summary(ws) == "1 warning: 1 unknown algorithm"


def test_format_summary_mixed_kinds() -> None:
    ws = [
        SessionWarning(kind="unknown_algorithm", message="x", line=1),
        SessionWarning(kind="duplicate_handle", message="y", line=2),
        SessionWarning(kind="duplicate_handle", message="z", line=3),
    ]
    s = format_warning_summary(ws)
    assert s.startswith("3 warnings: ")
    assert "1 unknown algorithm" in s
    assert "2 duplicate handles" in s
