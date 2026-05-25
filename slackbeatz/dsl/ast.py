"""AST nodes for parsed `.sb` files.

Two top-level block kinds — `setup` and `song`. A file may contain either or
both (a self-contained song embeds its rig; a standalone setup file is
referenced by other songs).

Everything carries a `line` attribute pointing at the source line so error
messages can be precise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Knob values are kept as their lexed type — int / float / str / tuple-of-str
# — so that downstream validation can complain with type-appropriate
# messages. The tuple form covers list-valued knobs like
# ``phrases=["breathe in", "and out"]`` on the speech generator.
KnobValue = bool | int | float | str | tuple[str, ...]
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
    """A `setup "name"` block plus all `inst`/`kit` lines that belong to it.

    ``backend`` carries the `backend NAME` directive (``"surge"`` or
    ``"external"``); ``None`` when the directive is absent — the
    loader picks the default in that case.
    """

    name: str
    instruments: list[InstDecl] = field(default_factory=list)
    kits: list[KitDecl] = field(default_factory=list)
    backend: str | None = None
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
    """`part <name> <bars> [<k=v>...]` plus indented gen-handle lines.

    Each indented gen line is one of:

    * ``  bass`` — handle only
    * ``  bass rolling`` — handle + per-part algorithm override
    * ``  bass swing=0.6 humanize=4`` — handle + per-part knob overrides
    * ``  bass rolling swing=0.6 humanize=4`` — handle + algorithm + knobs

    Algorithm overrides flow into :attr:`algorithm_overrides`; knob
    overrides flow into :attr:`knob_overrides`; handles always end up in
    :attr:`gens` in source order so the existing scheduler iteration is
    unchanged.
    """

    name: str
    bars: int
    knobs: Knobs = field(default_factory=dict)  # tempo, key, role, seed
    gens: list[str] = field(default_factory=list)
    # Part-local overrides: handle → algorithm name. A handle absent
    # from this dict uses the song-level algorithm. Populated by the
    # parser when a gen-line has a second token; expanded by the
    # resolver for ``style=NAME`` shorthand.
    algorithm_overrides: dict[str, str] = field(default_factory=dict)
    # Part-local knob overrides: handle → {knob → value}. Merged over
    # the song-level gen knobs at scheduler time so an algorithm gets
    # an effective knob dict combining: engine default → style profile
    # → song-level gen → part-local override.
    knob_overrides: dict[str, "Knobs"] = field(default_factory=dict)
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
class SceneEntry:
    """One entry in a ``scene`` block.

    ``scope`` is the keyword identifying what the entry targets — today
    just ``"ch"`` (per-channel mixer state), but the shape is recursive
    so future kinds (``"surge"``, ``"sampler"``, ``"part"`` for the
    automation direction in :doc:`/plans/i-want-to-redesign-glistening-spark.md`)
    can land without parser churn.

    For ``scope="ch"``, the positional integer in ``selector`` is the
    1-based MIDI channel. Knobs cover mixer state (``vol``, ``pan``,
    ``program``, ``mute``, ``solo``). Other scope kinds will encode
    their selector as a knob (e.g. ``surge ch=2 patch=…``) — kept
    homogeneous here.
    """

    scope: str
    selector: int | None = None  # positional int (e.g. channel) when present
    knobs: Knobs = field(default_factory=dict)
    children: list["SceneEntry"] = field(default_factory=list)
    line: int = 0


@dataclass
class SceneAST:
    """A top-level ``scene`` block — mixer + future synth-patch state.

    Persisted by the GUI's Save action so a loaded .sb restores mute /
    solo / volume / pan / program-change per channel. Surge patch and
    Sampler voice persistence land in a follow-up — they need Player
    accumulators that aren't wired yet.
    """

    entries: list[SceneEntry] = field(default_factory=list)
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
    meter: str | None = None  # time signature, e.g. "3/4"
    gens: list[GenDecl] = field(default_factory=list)
    parts: list[PartDecl] = field(default_factory=list)
    # Voice-scoped knob defaults — populated by `voice <type>` top-level
    # blocks. Keyed by gen type_ (rhythm / bass / melody / chords /
    # candy / subbass / speech / sample); value is a knob dict applied
    # to every gen of that type. Cascades between the song-level gen
    # knobs and part-scoped knob overrides.
    voice_defaults: dict[str, Knobs] = field(default_factory=dict)
    play: PlayLine | None = None
    # Optional scene block — mixer / scene state for round-trip via the
    # GUI's Save action. ``None`` when the source has no scene block;
    # populated by the parser when a ``scene`` keyword appears.
    scene: "SceneAST | None" = None
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
