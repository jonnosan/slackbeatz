"""Realtime MIDI output via ``mido`` / ``python-rtmidi``.

On macOS the conventional virtual port is **IAC Bus 1** (enable in
*Audio MIDI Setup → MIDI Studio → IAC Driver*). On Linux,
``snd-virmidi`` / JACK / ALSA all expose ports the same way. Windows
needs a loopMIDI-style virtual driver.

Port selection:

* If ``port_name`` is set, ``mido.open_output(port_name)`` is used —
  the name must match an entry from :func:`available_ports`.
* If ``port_name`` is ``None``, the first available output is chosen
  (typically what you want on a Mac with IAC enabled and nothing else).
"""

from __future__ import annotations

import mido

from .base import Sink


def available_ports() -> list[str]:
    """Names of currently-available MIDI output ports."""
    return list(mido.get_output_names())


class NoMidiPortError(RuntimeError):
    """Raised when no MIDI output is available and one wasn't specified."""


class RealtimeSink(Sink):
    """Send messages to a hardware or virtual MIDI output port.

    Parameters
    ----------
    port_name:
        Exact port name from :func:`available_ports`, or ``None`` to
        auto-select the first available port.
    """

    def __init__(self, port_name: str | None = None) -> None:
        self.port_name = port_name
        self._port: mido.ports.BaseOutput | None = None

    def open(self) -> None:
        ports = available_ports()
        if not ports:
            raise NoMidiPortError(
                "No MIDI output ports available. On macOS, enable the IAC "
                "Driver in Audio MIDI Setup → MIDI Studio."
            )
        chosen = self.port_name or ports[0]
        if self.port_name is not None and self.port_name not in ports:
            raise NoMidiPortError(
                f"MIDI port {self.port_name!r} not found. "
                f"Available: {ports}"
            )
        self._port = mido.open_output(chosen)

    def send(self, msg: mido.Message) -> None:
        assert self._port is not None, "RealtimeSink.send before open()"
        self._port.send(msg)

    def close(self) -> None:
        if self._port is None:
            return
        # Best-effort all-notes-off on every channel before closing so we
        # don't leave hanging notes if playback was interrupted.
        try:
            for ch in range(16):
                self._port.send(
                    mido.Message("control_change", channel=ch, control=123, value=0)
                )
        except Exception:
            pass
        self._port.close()
        self._port = None
