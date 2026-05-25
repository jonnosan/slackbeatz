"""Round-trip a :class:`Setup` back to ``.sb`` source text.

The redesigned GUI's Setup editor needs to mutate a working copy of a
:class:`Setup` and emit it back into the song file on Save. This
module is the emit half — pure, deterministic, no side effects.

Round-trip guarantees:

* ``parse → setup_from_ast → emit_setup → parse → setup_from_ast``
  produces an equal :class:`Setup`. (Verified by
  ``tests/test_setup_serialize.py``.)
* The emitted text is human-readable and stable — the same Setup
  always emits the same bytes, instrument / kit order preserved.
* Kit drum notes are emitted as explicit indented overrides rather
  than ``preset=NAME`` so the result round-trips even after the user
  has edited individual drum notes away from any known preset.

The shape is deliberately minimal — no comments, no blank lines
beyond a single trailing newline. The GUI's Save action concatenates
the emitted setup block with the song block; that path owns
formatting between blocks.
"""

from __future__ import annotations

from .model import Instrument, Kit, Setup


def emit_setup(setup: Setup) -> str:
    """Return the ``.sb`` source for *setup* — header + insts + kits.

    Output shape::

        setup "<name>"
        inst <name> ch=<N> [note=<M>]
        ...
        kit <name> ch=<N>
          <drum> <note>
          ...

    A trailing newline is always present so the result can be
    concatenated directly with subsequent blocks. Instruments and
    kits are emitted in their dictionary iteration order — which
    matches insertion order in Python 3.7+ — so a parse → emit
    round-trip preserves the source order.
    """
    lines: list[str] = [f'setup "{setup.name}"']
    for inst in setup.instruments.values():
        lines.append(_emit_instrument(inst))
    for kit in setup.kits.values():
        lines.extend(_emit_kit_lines(kit))
    return "\n".join(lines) + "\n"


def _emit_instrument(inst: Instrument) -> str:
    """One-line ``inst`` declaration. ``note=`` only when set (drum)."""
    if inst.note is not None:
        return f"inst {inst.name} ch={inst.channel} note={inst.note}"
    return f"inst {inst.name} ch={inst.channel}"


def _emit_kit_lines(kit: Kit) -> list[str]:
    """Header line + one indented override line per drum.

    Always emits drum notes explicitly (no ``preset=NAME``
    compression). This is verbose but guarantees a clean round-trip
    after the user has edited individual notes — preset matching
    isn't worth the fragility for a feature that emits at GUI-save
    cadence, not in a hot path.
    """
    lines = [f"kit {kit.name} ch={kit.channel}"]
    for drum, note in kit.drum_notes.items():
        lines.append(f"  {drum} {note}")
    return lines
