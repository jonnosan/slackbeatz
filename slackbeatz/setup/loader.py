"""Loading and converting setup definitions.

Three things live here:

* :func:`setup_from_ast` — convert a parsed :class:`SetupAST` into the
  immutable resolved :class:`Setup`. Applies kit presets, validates
  channel ranges, rejects duplicate names.
* :func:`load_setup` — resolve a setup reference (path or bundled name)
  into a :class:`Setup`. Lookup order matches the design plan.
* :func:`list_bundled_setups` — names of `.sb` files shipped in
  ``slackbeatz/setups/``, used by ``slackbeatz list-setups``.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from slackbeatz.drums.presets import PRESETS, preset_map
from slackbeatz.dsl.ast import KitDecl, InstDecl, SetupAST
from slackbeatz.dsl.parser import parse_file

from .model import Instrument, Kit, Setup


class SetupError(Exception):
    """Raised on invalid setup definitions (bad channel, missing preset, etc)."""


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------

def _validate_channel(name: str, raw: object, *, line: int) -> int:
    if not isinstance(raw, int):
        raise SetupError(f"line {line}: {name}: ch must be an integer, got {raw!r}")
    if not 1 <= raw <= 16:
        raise SetupError(f"line {line}: {name}: ch must be in 1..16, got {raw}")
    return raw


def _validate_note(name: str, raw: object, *, line: int) -> int:
    if not isinstance(raw, int):
        raise SetupError(f"line {line}: {name}: note must be an integer, got {raw!r}")
    if not 0 <= raw <= 127:
        raise SetupError(f"line {line}: {name}: note must be in 0..127, got {raw}")
    return raw


def _instrument_from_decl(decl: InstDecl) -> Instrument:
    ch = _validate_channel(decl.name, decl.knobs.get("ch"), line=decl.line)
    note_raw = decl.knobs.get("note")
    note = _validate_note(decl.name, note_raw, line=decl.line) if note_raw is not None else None
    return Instrument(name=decl.name, channel=ch, note=note)


def _kit_from_decl(decl: KitDecl) -> Kit:
    ch = _validate_channel(decl.name, decl.knobs.get("ch"), line=decl.line)
    # `preset=909` parses as int by the lexer; coerce to str for lookup.
    preset_raw = decl.knobs.get("preset")
    if preset_raw is None:
        drum_notes = preset_map("gm")
    else:
        preset_key = str(preset_raw)
        if preset_key not in PRESETS:
            raise SetupError(
                f"line {decl.line}: kit {decl.name}: unknown preset {preset_key!r} "
                f"(known: {sorted(PRESETS)})"
            )
        drum_notes = preset_map(preset_key)
    # Apply per-kit overrides.
    for drum, note in decl.overrides.items():
        if not 0 <= note <= 127:
            raise SetupError(
                f"line {decl.line}: kit {decl.name}: override {drum}={note} "
                "out of 0..127"
            )
        drum_notes[drum] = note
    return Kit(name=decl.name, channel=ch, drum_notes=drum_notes)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def setup_from_ast(ast: SetupAST) -> Setup:
    """Convert a parsed :class:`SetupAST` into a resolved :class:`Setup`."""
    instruments: dict[str, Instrument] = {}
    for idecl in ast.instruments:
        if idecl.name in instruments:
            raise SetupError(
                f"line {idecl.line}: duplicate instrument name {idecl.name!r}"
            )
        instruments[idecl.name] = _instrument_from_decl(idecl)

    kits: dict[str, Kit] = {}
    for kdecl in ast.kits:
        if kdecl.name in kits:
            raise SetupError(
                f"line {kdecl.line}: duplicate kit name {kdecl.name!r}"
            )
        if kdecl.name in instruments:
            raise SetupError(
                f"line {kdecl.line}: kit name {kdecl.name!r} clashes with an "
                "instrument name; pick distinct names so gens resolve cleanly"
            )
        kits[kdecl.name] = _kit_from_decl(kdecl)

    return Setup(name=ast.name, instruments=instruments, kits=kits)


def _bundled_dir() -> Path:
    """Path to the package's bundled `setups/` directory."""
    # importlib.resources.files() gives a Traversable; convert to Path so
    # callers can list .sb files easily.
    return Path(str(resources.files("slackbeatz") / "setups"))


def list_bundled_setups() -> list[str]:
    """Return the names (without `.sb` extension) of bundled setups."""
    d = _bundled_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.sb"))


def _resolve_path(ref: str, *, base_path: Path | None) -> Path | None:
    """Find a `.sb` file for *ref*. Lookup order:

    1. If *ref* contains a path separator or ends with ``.sb``, treat as a
       path: try relative to *base_path*, then the CWD.
    2. Otherwise try bundled-name match (``slackbeatz/setups/<ref>.sb``).
    """
    looks_like_path = "/" in ref or "\\" in ref or ref.endswith(".sb")
    if looks_like_path:
        candidates: list[Path] = []
        if base_path is not None:
            candidates.append((base_path / ref).resolve())
        candidates.append(Path(ref).resolve())
        for c in candidates:
            if c.is_file():
                return c
        return None
    # Bundled lookup.
    bundled = _bundled_dir() / f"{ref}.sb"
    if bundled.is_file():
        return bundled
    return None


def load_setup(ref: str, *, base_path: Path | None = None) -> Setup:
    """Load a setup by reference. *base_path* is typically the directory of
    the song file containing the ``setup "..."`` line, so relative paths
    resolve as the user expects.

    Raises :class:`SetupError` if the reference can't be resolved or the
    file doesn't contain a usable setup block.
    """
    path = _resolve_path(ref, base_path=base_path)
    if path is None:
        bundled = ", ".join(list_bundled_setups()) or "(none)"
        raise SetupError(
            f"setup {ref!r} not found — tried path resolution and bundled "
            f"names. Bundled setups: {bundled}"
        )
    file_ast = parse_file(path)
    if file_ast.setup is None:
        raise SetupError(f"{path}: file does not contain a `setup` block")
    return setup_from_ast(file_ast.setup)
