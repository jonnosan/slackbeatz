"""AST nodes for parsed `.sb` files.

Two top-level block kinds — `setup` and `song`. A file may contain either or
both (a self-contained song embeds its rig; a standalone setup file is
referenced by other songs).

Everything carries a `line` attribute pointing at the source line so error
messages can be precise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Knob values are kept as their lexed type — int / float / str — so that
# downstream validation can complain with type-appropriate messages.
KnobValue = int | float | str
Knobs = dict[str, KnobValue]


@dataclass
class InstDecl:
    """`inst <name> ch=N [note=M] ...`"""

    name: str
    knobs: Knobs  # must contain "ch"; may contain "note"
    line: int


@dataclass
class KitDecl:
    """`kit <name> ch=N [preset=P]` plus indented `<drum> <note>` overrides."""

    name: str
    knobs: Knobs  # must contain "ch"; may contain "preset"
    overrides: dict[str, int]  # drum-name -> midi note
    line: int


@dataclass
class SetupAST:
    """A `setup "name"` block plus all `inst`/`kit` lines that belong to it."""

    name: str
    instruments: list[InstDecl] = field(default_factory=list)
    kits: list[KitDecl] = field(default_factory=list)
    line: int = 0


@dataclass
class GenDecl:
    """`gen <handle> <type> <style> [<k=v>...]`"""

    handle: str
    type_: str
    style: str
    knobs: Knobs = field(default_factory=dict)
    line: int = 0


@dataclass
class PartDecl:
    """`part <name> <bars> [<k=v>...]` plus indented gen-handle lines."""

    name: str
    bars: int
    knobs: Knobs = field(default_factory=dict)  # tempo, key, role, seed
    gens: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class ArrAtom:
    """Atom of a `play` line.

    Either a reference to a part (``ref`` set, ``group`` empty) or a
    parenthesised group (``group`` set, ``ref`` ``None``). ``repeat`` is the
    multiplier from a trailing ``*N``; defaults to 1.
    """

    ref: str | None = None
    group: list["ArrAtom"] = field(default_factory=list)
    repeat: int = 1


@dataclass
class PlayLine:
    atoms: list[ArrAtom]
    line: int = 0


@dataclass
class SongAST:
    """A `song "name"` block plus its indented attributes and the gens /
    parts / play lines at indent 0 that follow it.
    """

    name: str
    setup_ref: str | None = None  # path or bundled name from `setup "..."`
    tempo: int | None = None
    key: str | None = None
    seed: int | None = None
    scale: str | None = None  # song-wide scale override (e.g. "dorian")
    gens: list[GenDecl] = field(default_factory=list)
    parts: list[PartDecl] = field(default_factory=list)
    play: PlayLine | None = None
    line: int = 0


@dataclass
class FileAST:
    """The result of parsing a single `.sb` file.

    A file may contain a setup, a song, both, or neither (empty). The parser
    rejects more than one of either.
    """

    setup: SetupAST | None = None
    song: SongAST | None = None
    source_path: str | None = None
