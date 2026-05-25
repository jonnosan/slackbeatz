"""Tests for :mod:`slackbeatz.ui.state` — persisted GUI session state.

The state file is a small bit of JSON at ``~/.slackbeatz/state.json``
holding recents / last_setup / window_geometry. These tests redirect
the location via the ``SLACKBEATZ_STATE_DIR`` env var so they don't
touch the user's real state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slackbeatz.ui import state as state_mod
from slackbeatz.ui.state import (
    SessionState,
    load,
    note_opened,
    prune_missing_recents,
    save,
    state_dir,
    state_path,
)


@pytest.fixture
def tmp_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SLACKBEATZ_STATE_DIR", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------
# Location resolution
# --------------------------------------------------------------------------

def test_state_dir_honours_env_override(tmp_state_dir) -> None:
    assert state_dir() == tmp_state_dir
    assert state_path() == tmp_state_dir / "state.json"


def test_state_dir_defaults_to_dot_slackbeatz_in_home(monkeypatch) -> None:
    monkeypatch.delenv("SLACKBEATZ_STATE_DIR", raising=False)
    assert state_dir() == Path.home() / ".slackbeatz"


# --------------------------------------------------------------------------
# Load — defaults & corrupt-file tolerance
# --------------------------------------------------------------------------

def test_load_returns_defaults_when_file_missing(tmp_state_dir) -> None:
    s = load()
    assert s.recents == []
    assert s.last_opened is None
    assert s.last_setup == "surge"
    assert s.window_geometry == "1100x700"


def test_load_returns_defaults_when_file_is_corrupt_json(tmp_state_dir) -> None:
    (tmp_state_dir / "state.json").write_text("{not valid json")
    s = load()
    assert s.recents == []
    assert s.last_setup == "surge"


def test_load_returns_defaults_when_file_is_not_an_object(tmp_state_dir) -> None:
    (tmp_state_dir / "state.json").write_text('"oops a string"')
    s = load()
    assert s.last_setup == "surge"


def test_load_tolerates_missing_keys(tmp_state_dir) -> None:
    # A state file that pre-dates a new field should still load — the
    # missing field falls back to its default.
    (tmp_state_dir / "state.json").write_text('{"recents": ["a.sb"]}')
    s = load()
    assert s.recents == ["a.sb"]
    assert s.last_setup == "surge"
    assert s.window_geometry == "1100x700"


def test_load_ignores_non_string_entries_in_recents(tmp_state_dir) -> None:
    (tmp_state_dir / "state.json").write_text('{"recents": ["ok.sb", 42, null]}')
    s = load()
    assert s.recents == ["ok.sb"]


# --------------------------------------------------------------------------
# Save — atomic write + round-trip
# --------------------------------------------------------------------------

def test_save_then_load_round_trips_all_fields(tmp_state_dir) -> None:
    src = SessionState(
        recents=["a.sb", "b.sb"],
        last_opened="a.sb",
        last_setup="external",
        window_geometry="1280x800+100+50",
    )
    save(src)
    got = load()
    assert got == src


def test_save_creates_parent_directory(tmp_path, monkeypatch) -> None:
    # Use a directory that doesn't exist yet.
    sub = tmp_path / "fresh" / "nested"
    monkeypatch.setenv("SLACKBEATZ_STATE_DIR", str(sub))
    save(SessionState(last_setup="external"))
    assert (sub / "state.json").is_file()


def test_save_does_not_leave_temp_files_behind(tmp_state_dir) -> None:
    save(SessionState(last_setup="external"))
    leftovers = list(tmp_state_dir.glob("*.tmp"))
    assert leftovers == []


# --------------------------------------------------------------------------
# note_opened — recents MRU semantics
# --------------------------------------------------------------------------

def test_note_opened_prepends_to_recents(tmp_state_dir) -> None:
    s = SessionState()
    note_opened(s, "song1.sb")
    note_opened(s, "song2.sb")
    assert s.recents == ["song2.sb", "song1.sb"]
    assert s.last_opened == "song2.sb"


def test_note_opened_dedupes_existing_path(tmp_state_dir) -> None:
    # Re-opening a recent file should move it to the front, not
    # create a duplicate entry.
    s = SessionState(recents=["a.sb", "b.sb", "c.sb"])
    note_opened(s, "b.sb")
    assert s.recents == ["b.sb", "a.sb", "c.sb"]


def test_note_opened_caps_recents_at_five(tmp_state_dir) -> None:
    s = SessionState()
    for i in range(8):
        note_opened(s, f"song{i}.sb")
    assert len(s.recents) == 5
    # The five most recent are kept, newest first.
    assert s.recents == [f"song{i}.sb" for i in (7, 6, 5, 4, 3)]


# --------------------------------------------------------------------------
# prune_missing_recents — drop entries whose files vanished
# --------------------------------------------------------------------------

def test_prune_missing_recents_drops_vanished_files(tmp_state_dir) -> None:
    # Create one file that exists; reference another that doesn't.
    existing = tmp_state_dir / "real.sb"
    existing.write_text("song \"x\"\n")
    s = SessionState(
        recents=[str(existing), str(tmp_state_dir / "missing.sb")],
        last_opened=str(tmp_state_dir / "missing.sb"),
    )
    removed = prune_missing_recents(s)
    assert s.recents == [str(existing)]
    assert s.last_opened is None  # cleared because the file is gone
    assert len(removed) == 1
    assert "missing.sb" in removed[0]


def test_prune_missing_recents_keeps_last_opened_when_present(tmp_state_dir) -> None:
    existing = tmp_state_dir / "real.sb"
    existing.write_text("song \"x\"\n")
    s = SessionState(recents=[str(existing)], last_opened=str(existing))
    prune_missing_recents(s)
    assert s.last_opened == str(existing)
