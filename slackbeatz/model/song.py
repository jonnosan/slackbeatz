"""Resolved-song data types — the output of :mod:`slackbeatz.setup.resolve`.

These differ from the AST in :mod:`slackbeatz.dsl.ast` in two ways:

* Generators are bound to their :class:`Instrument` or :class:`Kit`.
* Per-knob defaults are applied where the AST left them unset.

The scheduler reads only from these types; it never touches AST nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from slackbeatz.dsl.ast import KnobValue
from slackbeatz.setup.model import Instrument, Kit, Setup
from slackbeatz.theory.meter import Meter, COMMON_TIME


@dataclass(frozen=True)
class ResolvedGen:
    """One generator with its rig binding nailed down."""

    handle: str
    type_: str
    style: str
    knobs: dict[str, KnobValue]
    instrument: Instrument | None = None  # for rhythm + pitched types
    kit: Kit | None = None  # for drums type
    meter: Meter | None = None  # polymeter override; None = inherit part

    @property
    def seed_override(self) -> int | None:
        """The gen-level seed, if `seed=` was set on the gen line."""
        v = self.knobs.get("seed")
        return v if isinstance(v, int) else None


@dataclass(frozen=True)
class ResolvedPart:
    """One named song section with all overrides resolved."""

    name: str
    bars: int  # lower bound (= the only value if no `..` range was given)
    tempo: int  # already resolved (part > song default)
    key: str  # already resolved (part > song default)
    role: str  # defaults to name if `role=` not set
    seed_override: int | None  # None ⇒ use song / CLI / default
    scale_override: str | None = None  # set if `scale=` on the part
    transpose_prob: float = 0.0  # per-arrangement-instance roll
    bars_max: int | None = None   # issue #21: upper bound for bars=N..M
    tension: float | None = None  # issue #14: explicit override; else derive
    meter: Meter = COMMON_TIME    # 4/4 unless set on the part or song
    gen_handles: list[str] = field(default_factory=list)
    # Phase 4: per-part algorithm overrides (handle → algorithm name).
    # Populated from indented `<handle> <algorithm>` lines and from the
    # `style=NAME` part-level shorthand. The scheduler consults this
    # map when looking up the generator class for a (part, handle)
    # pair — falling back to the song-level algorithm when absent.
    algorithm_overrides: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SceneState:
    """Per-channel mixer state loaded from a `scene` block, applied by
    the Player when the song starts.

    Empty when the source had no scene block; populated when the GUI
    Save action emitted one. Future scope kinds (Surge patches, Sampler
    voices, per-part automation lanes) get added as sibling fields here
    without disturbing the channel-state path.
    """

    # 1-based channel → knob dict. Knobs cover persisted mixer state
    # (``vol`` float, ``pan`` float, ``program`` int, ``mute`` bool,
    # ``solo`` bool). Only keys present in the source appear here; the
    # Player treats missing entries as "no override".
    channels: dict[int, dict[str, object]] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedSong:
    """A song with setup applied, ready for the scheduler."""

    name: str
    setup: Setup
    tempo: int
    key: str
    seed: int  # base seed (song level if set, else CLI seed, else 0)
    gens: dict[str, ResolvedGen]
    parts: dict[str, ResolvedPart]
    arrangement: list[str]  # flat list of part names (groups + *N expanded)
    scale_override: str | None = None  # song-level `scale <name>`, optional
    meter: Meter = COMMON_TIME  # song-level default time signature
    # Per-channel mixer state restored at load time. Empty when the
    # source had no scene block.
    scene: SceneState = field(default_factory=lambda: SceneState())
