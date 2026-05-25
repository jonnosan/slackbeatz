"""Persisted session state for the GUI — recents, last-used setup, etc.

A small JSON file at ``~/.slackbeatz/state.json`` (or the path under
``$SLACKBEATZ_STATE_DIR`` for testing / sandboxing) carries the bits
of UI state that should survive across launches:

* ``recents`` — the most recently opened ``.sb`` paths, MRU-sorted, cap 5
* ``last_opened`` — the file the Welcome screen will re-open by default
* ``last_setup`` — the setup name to pre-select in NewSongDialog and to
  fall back to when a loaded song has no embedded setup
* ``window_geometry`` — Tk geometry string (e.g. ``"1100x700+120+80"``)

Defaults are returned cleanly when the file is missing or corrupt, so
first-launch and "user deleted state.json" both Just Work without the
caller having to special-case.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


# How many recent files to remember in the Welcome screen list.
_RECENTS_CAP = 5

# Default fallbacks. ``last_setup="surge"`` matches the redesigned
# default backend choice for new sessions (see
# :mod:`slackbeatz.setups.surge`); the bundled ``surge.sb`` carries
# ``backend surge``. First-launch users land on Surge XT unless they
# pick something else explicitly.
_DEFAULT_LAST_SETUP = "surge"
_DEFAULT_WINDOW_GEOMETRY = "1100x700"


@dataclass
class SessionState:
    """The full persisted UI session state.

    Mutable on purpose — callers update fields and call :func:`save`
    rather than building a new instance each tweak.
    """

    recents: list[str] = field(default_factory=list)
    last_opened: str | None = None
    last_setup: str = _DEFAULT_LAST_SETUP
    window_geometry: str = _DEFAULT_WINDOW_GEOMETRY


def state_dir() -> Path:
    """Return the directory state.json lives in.

    ``$SLACKBEATZ_STATE_DIR`` overrides for tests / sandboxes; default
    is ``~/.slackbeatz``. Created on demand by :func:`save`.
    """
    override = os.environ.get("SLACKBEATZ_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".slackbeatz"


def state_path() -> Path:
    """Return the JSON file's full path."""
    return state_dir() / "state.json"


def load(path: Path | None = None) -> SessionState:
    """Read state.json from *path* (default :func:`state_path`).

    Returns a fresh :class:`SessionState` with defaults when the file
    is missing, empty, or fails to parse — callers don't need to
    distinguish those failure modes from first-launch.

    Missing keys in an otherwise-valid file fall back to per-field
    defaults so adding a new field to :class:`SessionState` doesn't
    invalidate existing state files.
    """
    target = path or state_path()
    if not target.is_file():
        return SessionState()
    try:
        raw = json.loads(target.read_text())
    except (json.JSONDecodeError, OSError):
        return SessionState()
    if not isinstance(raw, dict):
        return SessionState()
    state = SessionState()
    recents = raw.get("recents")
    if isinstance(recents, list):
        state.recents = [str(r) for r in recents if isinstance(r, str)]
    last_opened = raw.get("last_opened")
    if isinstance(last_opened, str):
        state.last_opened = last_opened
    last_setup = raw.get("last_setup")
    if isinstance(last_setup, str) and last_setup:
        state.last_setup = last_setup
    geom = raw.get("window_geometry")
    if isinstance(geom, str) and geom:
        state.window_geometry = geom
    return state


def save(state: SessionState, path: Path | None = None) -> None:
    """Atomically write *state* to disk.

    Writes to a temp file in the same directory and renames into place
    so a crash mid-write can't leave the user with a corrupt state
    file. Creates the parent directory on demand.
    """
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(state)
    # NamedTemporaryFile with delete=False so we can rename it; tempdir
    # set to the target's parent so the rename stays on the same
    # filesystem (cross-fs renames aren't atomic).
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".tmp",
        dir=str(target.parent),
        delete=False,
    ) as tf:
        json.dump(payload, tf, indent=2)
        tf.write("\n")
        tmp_path = Path(tf.name)
    tmp_path.replace(target)


def note_opened(state: SessionState, opened_path: Path | str) -> None:
    """Record that *opened_path* was just opened — updates recents +
    ``last_opened`` in place.

    De-dupes by string equality (canonical absolute path) — opening the
    same file twice doesn't bloat the recents list. The newest entry
    sits at index 0; the list is capped at ``_RECENTS_CAP``.
    """
    p = str(Path(opened_path))
    state.last_opened = p
    # Drop any existing entry for this path so it can move to the
    # front; preserves MRU order on re-open.
    state.recents = [r for r in state.recents if r != p]
    state.recents.insert(0, p)
    del state.recents[_RECENTS_CAP:]


def prune_missing_recents(state: SessionState) -> list[str]:
    """Drop recents whose files no longer exist on disk.

    Returns the list of removed paths so the GUI can show a one-line
    notice ("3 recent files have been moved or deleted"). Useful to
    call once at Welcome-screen launch — keeps the recents list from
    showing stale entries the user can't actually open.
    """
    kept: list[str] = []
    removed: list[str] = []
    for r in state.recents:
        if Path(r).is_file():
            kept.append(r)
        else:
            removed.append(r)
    state.recents = kept
    if state.last_opened and not Path(state.last_opened).is_file():
        state.last_opened = None
    return removed
