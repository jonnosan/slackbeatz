"""A MIDI sink that splits events across multiple named virtual ports
by channel — used by ``slackbeatz live --surge`` so each channel can
go to its own dedicated softsynth without channel-filter setup.

Routing model:

* The caller passes a ``channel_to_port`` dict like
  ``{0: 'slackbeatz-lead', 1: 'slackbeatz-bass', 9: 'slackbeatz-drums'}``.
* Each value is a *virtual MIDI port name* this sink creates on macOS
  CoreMIDI / Linux ALSA / Windows MME via mido's ``virtual=True`` flag.
* When an event with channel C is sent, it's dispatched ONLY to the
  port mapped from C.
* Channels not in the dict are silently dropped (= muted).

Any softsynth (Surge XT, FluidSynth, VCV Rack, Logic, Ableton…) can
then listen to any of these ports through its normal MIDI input
selection. The port names appear in every MIDI-aware app's input list
on the same machine.
"""

from __future__ import annotations

import mido

from .base import Sink


class NoMidiPortError(RuntimeError):
    """Raised when virtual port creation fails."""


class MultiPortSink(Sink):
    """Sink that opens N virtual MIDI ports + routes events by channel.

    Parameters
    ----------
    channel_to_port:
        Map from 0-indexed MIDI channel → virtual port name. Multiple
        channels can map to the same port (e.g. all channels could go
        to a single "slackbeatz" port if you don't want splitting).
    """

    def __init__(self, channel_to_port: dict[int, str]) -> None:
        self.channel_to_port = channel_to_port
        # port name → open mido port
        self._ports: dict[str, mido.ports.BaseOutput] = {}

    def open(self) -> None:
        # Idempotent — if the virtual ports are already open (because
        # we're being reused across playback runs), keep them. We need
        # this because each playback re-runs scheduler.open()/close()
        # but the user's Surge XT subscriptions to these virtual ports
        # mustn't blink out between songs.
        if self._ports:
            return
        unique_names = sorted(set(self.channel_to_port.values()))
        try:
            for name in unique_names:
                self._ports[name] = mido.open_output(name, virtual=True)
        except Exception as e:  # noqa: BLE001
            self.close()
            raise NoMidiPortError(
                f"failed to create virtual MIDI port {name!r}: {e}"
            ) from e

    def send(self, msg: mido.Message) -> None:
        # Meta / channel-less messages — send to every port so things
        # like all-notes-off propagate everywhere.
        if not hasattr(msg, "channel"):
            for port in self._ports.values():
                port.send(msg)
            return
        target_name = self.channel_to_port.get(msg.channel)
        if target_name is None:
            return  # channel not routed → drop
        port = self._ports.get(target_name)
        if port is not None:
            port.send(msg)

    def close(self) -> None:
        # Try all-notes-off on every channel + port before closing.
        for port in self._ports.values():
            try:
                for ch in range(16):
                    port.send(
                        mido.Message("control_change", channel=ch, control=123, value=0)
                    )
            except Exception:
                pass
            try:
                port.close()
            except Exception:
                pass
        self._ports.clear()

    @property
    def port_names(self) -> list[str]:
        """The list of unique virtual port names this sink owns."""
        return sorted(set(self.channel_to_port.values()))
