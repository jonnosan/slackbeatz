"""Stale-file diagnostics — non-fatal warnings the GUI surfaces on Open.

The redesigned Welcome flow opens a .sb file and walks it to spot
issues that the engine *could* handle gracefully but the user should
still see (unknown style names, missing setups, gens referencing dead
algorithms). The CLI's resolver raises hard on most of these — the GUI
wants to fall back + show a session-info banner instead.

This module provides the **detection** half — pure functions that walk
a parsed :class:`~slackbeatz.dsl.ast.FileAST` and return a list of
:class:`SessionWarning`. The fallback half (substituting safe defaults
into the resolved song) is reserved for the GUI shell — see Phase E /
F in the redesign plan.

Keeping these as pure functions, separate from the resolver, means:

* The CLI keeps its existing strict behaviour (any of these issues
  raises ``ResolveError``).
* The GUI can call ``check_for_warnings`` *before* resolve, build a
  banner from the result, then either let resolve raise (showing the
  banner alongside the error) or apply fallbacks (Phase F) and resolve
  successfully.
* The detectors are trivially unit-testable against AST fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from slackbeatz.dsl.ast import FileAST
from slackbeatz.generators.registry import REGISTRY
from slackbeatz.setup.loader import list_bundled_setups


WarningKind = Literal[
    "unknown_setup",
    "unknown_style",
    "unknown_algorithm",
    "unknown_voice_type",
    "duplicate_handle",
]


@dataclass(frozen=True)
class SessionWarning:
    """One non-fatal issue spotted in a .sb file.

    * ``kind`` — categorical tag the GUI uses for icon / colour.
    * ``message`` — human-readable summary for the banner ("Style 'X'
      not available; will fall back to derived style.").
    * ``line`` — 1-based source line; 0 for warnings that aren't
      tied to a single line.
    """

    kind: WarningKind
    message: str
    line: int = 0


def check_for_warnings(file_ast: FileAST) -> list[SessionWarning]:
    """Walk a parsed file and collect non-fatal issues.

    Today this checks:

    * Setup references (``setup "..."`` line in a song) that don't
      match a bundled setup name. Path-style refs aren't checked here
      — they're resolved against the song file's directory at load
      time and surface as ``SetupError`` if missing.
    * Gen lines whose ``(type_, style)`` pair isn't in the algorithm
      registry — typo or removed algorithm.
    * Gen lines with an unknown type — typo or generator removed
      from this slackbeatz version.
    * Duplicate gen handles within one song — would raise at resolve
      time, but the GUI can flag it earlier.

    Returns an empty list when the file is clean. Order matches source
    order so the banner can be rendered top-to-bottom.
    """
    warnings: list[SessionWarning] = []

    song = file_ast.song
    if song is None:
        return warnings

    # Bundled-setup names — only flag for setup refs that look like
    # bare names (not paths). Path refs get checked + raised at load.
    if song.setup_ref is not None and _looks_like_bundled_name(song.setup_ref):
        bundled = set(list_bundled_setups())
        if song.setup_ref not in bundled:
            warnings.append(SessionWarning(
                kind="unknown_setup",
                message=(
                    f"Setup {song.setup_ref!r} not in the bundled list. "
                    f"Available: {sorted(bundled)}"
                ),
                line=song.line,
            ))

    # Per-gen algorithm + type checks.
    known_types = {t for (t, _algo) in REGISTRY}
    seen_handles: set[str] = set()
    for gen in song.gens:
        if gen.type_ not in known_types:
            warnings.append(SessionWarning(
                kind="unknown_voice_type",
                message=(
                    f"Gen {gen.handle!r} declares unknown voice type "
                    f"{gen.type_!r}. Known types: {sorted(known_types)}"
                ),
                line=gen.line,
            ))
            # Skip algorithm check if the type is unknown — the
            # (type_, style) lookup would always miss and the type
            # message is the actionable one.
        elif (gen.type_, gen.style) not in REGISTRY:
            available = sorted(
                a for (t, a) in REGISTRY if t == gen.type_
            )
            warnings.append(SessionWarning(
                kind="unknown_algorithm",
                message=(
                    f"Gen {gen.handle!r} ({gen.type_}) references "
                    f"unknown algorithm {gen.style!r}. Available: "
                    f"{available}"
                ),
                line=gen.line,
            ))
        if gen.handle in seen_handles:
            warnings.append(SessionWarning(
                kind="duplicate_handle",
                message=f"Duplicate gen handle {gen.handle!r}.",
                line=gen.line,
            ))
        seen_handles.add(gen.handle)

    return warnings


def _looks_like_bundled_name(ref: str) -> bool:
    """True if *ref* should be checked against the bundled-setup list.

    Path-style refs (contain a separator or end with ``.sb``) get
    resolved at load time — the loader raises a precise error if
    they're missing, so we don't double-warn here.
    """
    return "/" not in ref and "\\" not in ref and not ref.endswith(".sb")


def format_warning_summary(warnings: list[SessionWarning]) -> str:
    """One-line summary for the status bar / log: e.g.
    "3 warnings: 1 unknown algorithm, 2 duplicate handles".

    Returns an empty string when *warnings* is empty so callers can
    use it as a truthy check.
    """
    if not warnings:
        return ""
    counts: dict[WarningKind, int] = {}
    for w in warnings:
        counts[w.kind] = counts.get(w.kind, 0) + 1
    bits = [
        f"{n} {kind.replace('_', ' ')}{'s' if n != 1 else ''}"
        for kind, n in sorted(counts.items())
    ]
    return f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}: " + ", ".join(bits)
