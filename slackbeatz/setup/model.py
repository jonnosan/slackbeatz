"""Resolved (post-validation) representations of a setup.

These are the immutable data types the rest of the engine reads —
``Instrument`` for a single pitched voice or one-shot drum, ``Kit`` for a
multi-drum group on one channel, and ``Setup`` collecting them by name.
The parser produces AST counterparts in :mod:`slackbeatz.dsl.ast`; the
:mod:`slackbeatz.setup.loader` module converts AST → resolved model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Three peer setup modes (see [[backend_is_setup]]):
#   external          — raw MIDI on virtual ports; no synth spawned
#   surge-standalone  — surge-xt-cli per role, audio direct to CoreAudio
#   ableton-blackhole — surge-xt-cli per role, audio routed via BlackHole
#                       channels into an external Ableton Live Set
Mode = Literal["external", "surge-standalone", "ableton-blackhole"]

# Legacy alias — kept so any pinned `setup.backend == "surge"` check
# keeps working (both surge-standalone and ableton-blackhole spawn surge).
Backend = Literal["surge", "external"]


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
    """A collection of named ``Instrument``s and ``Kit``s — the rig.

    ``mode`` selects the render path:

    * ``"external"`` — bare MIDI to an external port; no synth spawned.
    * ``"surge-standalone"`` — surge-xt-cli per pitched channel writing
      directly to CoreAudio; FluidSynth handles ch10 drums. SB owns
      mixing (no cross-bus / master FX — that's the accepted limit
      of this mode).
    * ``"ableton-blackhole"`` — surge-xt-cli per pitched channel routed
      through BlackHole channels into a fixed Ableton Live Set that
      owns mixing/FX/master. Dual MIDI subscription comes for free
      via CoreMIDI's pub/sub model (Ableton can subscribe to the
      same virtual port surge is listening on).

    Defaults to ``"external"`` so existing setups with no explicit
    mode directive keep their pre-redesign behaviour.

    ``backend`` is a derived property kept for backward compatibility:
    ``"external" → "external"``; surge-standalone and ableton-blackhole
    both → ``"surge"`` (both spawn surge-xt-cli).
    """

    name: str
    instruments: dict[str, Instrument] = field(default_factory=dict)
    kits: dict[str, Kit] = field(default_factory=dict)
    mode: Mode = "external"

    @property
    def backend(self) -> Backend:
        """Legacy view — surge-standalone + ableton-blackhole both → "surge"."""
        return "external" if self.mode == "external" else "surge"

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
