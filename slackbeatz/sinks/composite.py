"""Route MIDI events to different child sinks based on channel.

The motivating use case is ``slackbeatz live --surge``:

* Drums (channel 10) stay on FluidSynth's existing virtual port (a
  :class:`RealtimeSink`) — FluidSynth handles GM drum kits well.
* Pitched channels (1=lead, 2=bass, 3=pad, 4=candy) go to one named
  virtual port each (a :class:`MultiPortSink`) so each Surge XT window
  can listen to its own port without needing a channel-filter setting.

Channel-less messages (sysex, meta) are sent to the default sink only —
this matches the previous single-sink behaviour for things like song
start / stop where there's no per-channel context.
"""

from __future__ import annotations

import mido

from .base import Sink


class CompositeSink(Sink):
    """Dispatch per-channel to one of several child sinks.

    Parameters
    ----------
    default:
        Sink that receives any event whose channel is not listed in
        *channel_overrides*. Also receives channel-less messages.
    channel_overrides:
        Map from 0-indexed MIDI channel → sink. Multiple channels can
        share the same sink object (and typically do — all pitched
        channels share one MultiPortSink).
    """

    def __init__(
        self,
        default: Sink,
        channel_overrides: dict[int, Sink],
        *,
        manage_overrides: bool = True,
    ) -> None:
        self.default = default
        self.channel_overrides = channel_overrides
        # When False (Player default), open() and close() leave the
        # override sinks alone — the caller (Player) owns their
        # lifecycle so they survive across playback runs.
        self.manage_overrides = manage_overrides

    def open(self) -> None:
        # Always open the default; only open overrides if we manage them.
        self.default.open()
        if not self.manage_overrides:
            return
        seen: set[int] = {id(self.default)}
        for s in self.channel_overrides.values():
            if id(s) in seen:
                continue
            seen.add(id(s))
            s.open()

    def send(self, msg: mido.Message) -> None:
        if hasattr(msg, "channel"):
            target = self.channel_overrides.get(msg.channel, self.default)
        else:
            target = self.default
        target.send(msg)

    def close(self) -> None:
        try:
            self.default.close()
        except Exception:
            pass
        if not self.manage_overrides:
            return
        seen: set[int] = {id(self.default)}
        for s in self.channel_overrides.values():
            if id(s) in seen:
                continue
            seen.add(id(s))
            try:
                s.close()
            except Exception:
                pass
