"""Standard MIDI File output. Phase-2 placeholder.

The intended shape:

* Open a ``mido.MidiFile`` at the song's PPQ.
* Insert ``set_tempo`` meta-events at each tempo-segment boundary.
* Maintain one track per MIDI channel used by the song so a DAW
  importing the file gets a usable per-instrument breakdown out of the
  box.
* ``send()`` collects messages with their absolute ticks; ``close()``
  writes the file to ``output_path``.

For v1, raises :class:`NotImplementedError` from ``open()``.
"""

from __future__ import annotations

from pathlib import Path

import mido

from .base import Sink


class MidiFileSink(Sink):
    """Write a Standard MIDI File. Not yet implemented in v1."""

    def __init__(self, output_path: str | Path) -> None:
        self.output_path = Path(output_path)

    def open(self) -> None:
        raise NotImplementedError(
            "MidiFileSink is a phase-2 placeholder. The realtime sink is the "
            "v1 target; once that's bedded in, this will buffer events with "
            "their absolute ticks, group them by channel onto separate tracks, "
            "and write a Standard MIDI File via mido.MidiFile."
        )

    def send(self, msg: mido.Message) -> None:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover — no resources to release
        pass
