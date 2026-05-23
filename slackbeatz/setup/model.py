"""Resolved (post-validation) representations of a setup.

These are the immutable data types the rest of the engine reads —
``Instrument`` for a single pitched voice or one-shot drum, ``Kit`` for a
multi-drum group on one channel, and ``Setup`` collecting them by name.
The parser produces AST counterparts in :mod:`slackbeatz.dsl.ast`; the
:mod:`slackbeatz.setup.loader` module converts AST → resolved model.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Instrument:
    """One logical voice on the rig.

    If ``note`` is set, the voice is a one-shot drum — algorithms ignore
    the part's key and always emit at this MIDI note. If ``note`` is
    ``None``, the voice is pitched and algorithms pick notes from the
    part's key.
    """

    name: str
    channel: int  # 1..16
    note: int | None = None

    @property
    def is_drum(self) -> bool:
        return self.note is not None

    @property
    def is_pitched(self) -> bool:
        return self.note is None


@dataclass(frozen=True)
class Kit:
    """A multi-drum group sharing one MIDI channel."""

    name: str
    channel: int
    drum_notes: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Setup:
    """A collection of named ``Instrument``s and ``Kit``s — the rig."""

    name: str
    instruments: dict[str, Instrument] = field(default_factory=dict)
    kits: dict[str, Kit] = field(default_factory=dict)

    def find(self, handle: str) -> Instrument | Kit | None:
        """Return the entry named *handle*, or ``None`` if not present.

        Instruments are checked first; kits second. Names should be unique
        across the two namespaces — the loader enforces this at build time.
        """
        if handle in self.instruments:
            return self.instruments[handle]
        if handle in self.kits:
            return self.kits[handle]
        return None
