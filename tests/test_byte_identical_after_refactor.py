"""Byte-identical MIDI baseline for the generator refactor.

The plan at `~/.claude/plans/maybe-the-effect-i-cozy-piglet.md`
renames every generator from style-based (`bass/psytrance.py`) to
algorithm-based (`bass/gallop.py`) and consolidates near-identical
generators. Output must stay byte-identical to today's for every
example song + canonical composed phrase.

This module:

* Snapshots the SHA-256 of each rendered :class:`mido.MidiFile`
  for the corpus (every `examples/**/*.sb` + the 9 composed
  phrases listed below).
* Compares the live snapshot against the JSON committed at
  ``tests/data/midi_hashes.json``.

First run (pre-refactor) creates the JSON; subsequent runs must
match. To regenerate after an intentional behaviour change, delete
the JSON + re-run pytest (it auto-records when the file is
missing).
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

import slackbeatz.generators  # noqa: F401 — register all (type, style) algos
from slackbeatz.compose import compose_from_text
from slackbeatz.dsl.parser import ParseError, parse, parse_file
from slackbeatz.engine.midifile import build_midifile
from slackbeatz.setup.loader import SetupError, load_setup
from slackbeatz.setup.resolve import ResolveError, resolve_song


DATA_DIR = Path(__file__).parent / "data"
HASH_FILE = DATA_DIR / "midi_hashes.json"
EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


# One phrase per style profile in `compose._STYLE_PROFILES`. Mirrors
# the canonical list in `test_composed_song_resolves_against_gm_setup`
# + adds a lofi phrase so the full 9-style matrix is covered.
COMPOSED_PHRASES: list[str] = [
    "Lonely night at the warehouse",     # deep_techno
    "Cosmic mushroom dance",             # psytrance
    "Sunset over Plaza",                 # vaporwave
    "Acid trax forever",                 # acid
    "Smoke and fog and rain submerged",  # dub_techno
    "Jungle rolling neurofunk",          # drum_and_bass
    "UK 2step london garage",            # garage
    "Lofi rainy afternoon",              # lofi
    "Hello world",                       # euclid fallback
]


def _hash_resolved(resolved) -> str:
    """SHA-256 the serialized :class:`mido.MidiFile` bytes for *resolved*.

    Uses :func:`build_midifile` so the test exercises the same code
    path that ``slackbeatz render`` writes. We serialize to an
    in-memory buffer to avoid touching the filesystem; mido's
    ``MidiFile.save`` produces deterministic output given the same
    events (it has no internal randomness or timestamps).
    """
    mf = build_midifile(resolved)
    buf = io.BytesIO()
    mf.save(file=buf)
    return hashlib.sha256(buf.getvalue()).hexdigest()


def _setup_for_example(sb_path: Path, file_ast):
    """Resolve the setup an example .sb references — name from its
    `setup=` line, base_path from the .sb itself so relative paths
    work. Falls back to "gm" when nothing is declared.
    """
    name = (
        file_ast.song.setup_ref
        if file_ast.song is not None and file_ast.song.setup_ref
        else "gm"
    )
    return load_setup(name, base_path=sb_path)


def _collect_hashes() -> dict[str, str]:
    """Render every entry in the corpus and return ``{key: sha256}``.

    Keys are stable identifiers — ``composed:<phrase>`` for composer
    output, ``file:<relative path>`` for example .sb files. Any entry
    that fails to parse / resolve is *omitted* (rather than raising)
    so a partially-broken corpus doesn't block the test; the diff
    against the snapshot still catches drift.
    """
    setup_default = load_setup("gm")
    out: dict[str, str] = {}

    for phrase in COMPOSED_PHRASES:
        sb_text = compose_from_text(phrase)
        try:
            fa = parse(sb_text)
        except ParseError:
            continue
        if fa.song is None:
            continue
        try:
            resolved = resolve_song(fa.song, setup_default, cli_seed=0)
        except (ResolveError, SetupError):
            continue
        out[f"composed:{phrase}"] = _hash_resolved(resolved)

    repo_root = EXAMPLES_DIR.parent
    for sb_path in sorted(EXAMPLES_DIR.rglob("*.sb")):
        try:
            fa = parse_file(sb_path)
        except ParseError:
            continue
        if fa.song is None:
            continue
        try:
            setup = _setup_for_example(sb_path, fa)
        except SetupError:
            continue
        try:
            resolved = resolve_song(fa.song, setup, cli_seed=0)
        except (ResolveError, SetupError):
            continue
        rel = sb_path.relative_to(repo_root).as_posix()
        out[f"file:{rel}"] = _hash_resolved(resolved)

    return out


def test_midi_hashes_match_baseline():
    """All MIDI hashes must match the JSON snapshot.

    On first run (no JSON), the snapshot is auto-recorded and the
    test passes. Subsequent runs assert byte-equivalence; a diff
    means the refactor changed output.
    """
    actual = _collect_hashes()
    if not HASH_FILE.is_file():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        HASH_FILE.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
        pytest.skip(
            f"recorded {len(actual)} baseline hashes in {HASH_FILE.name} — "
            "commit and re-run to make the assertion fire on regressions."
        )

    expected = json.loads(HASH_FILE.read_text())
    if actual == expected:
        return
    # Build a readable diff so failures point at exact entries.
    diffs: list[str] = []
    all_keys = sorted(set(expected) | set(actual))
    for k in all_keys:
        e = expected.get(k)
        a = actual.get(k)
        if e != a:
            diffs.append(f"  {k}: expected={e or '(missing)'} actual={a or '(missing)'}")
    raise AssertionError(
        f"{len(diffs)} MIDI hash(es) differ from baseline "
        f"({HASH_FILE.name}):\n" + "\n".join(diffs)
    )
