"""Drum-splitter sink — routes ch10 notes to per-drum virtual MIDI ports.

In ``ableton`` mode each pitched role gets its own virtual port
(``slackbeatz-bass`` etc) for the Ableton MIDI track to subscribe
to. Drums on ch10 are different: a single channel carries many
distinct drum hits (kick at note 36, snare at 38, hat at 42, …),
and the user wants each drum on its own Ableton track so they can
host individual instruments.

This sink solves that by reading the Setup's ch10 ``inst``
declarations to build a ``note → slackbeatz-drum-<name>`` map. ch10
note_on/note_off events are dispatched to the matching virtual port.
Anything on ch10 with no matching inst goes to a ``slackbeatz-drum-
other`` catch-all port. Non-ch10 events pass through unchanged to
the wrapped underlying sink.

Example map for the bundled ableton.sb:

  note 36 (kick)  → slackbeatz-drum-kick
  note 38 (snare) → slackbeatz-drum-snare
  note 39 (clap)  → slackbeatz-drum-clap
  note 42 (hats)  → slackbeatz-drum-hats
  note 46 (ohats) → slackbeatz-drum-ohats
  (any other ch10 note) → slackbeatz-drum-other

The wrapped sink (typically the regular MultiPortSink) handles every
other channel exactly as before — drum split is additive, not a
rewrite of the rest of the routing.
"""

from __future__ import annotations

import mido

from .base import Sink


# Always-present catch-all port name for unmapped ch10 notes.
DRUM_OTHER_PORT = "slackbeatz-drum-other"


def drum_port_name(inst_name: str) -> str:
    """Conventional virtual port name for a drum inst (``kick`` → ``slackbeatz-drum-kick``)."""
    return f"slackbeatz-drum-{inst_name}"


def build_drum_note_map(setup) -> dict[int, str]:
    """Build ``{note: port_name}`` from *setup*'s ch10 ``inst`` lines.

    Only instruments on channel 10 with a ``note=`` value count;
    other instruments (pitched voices, percussion on other channels)
    are skipped. Duplicate notes are first-wins (rare but possible
    if the user has overlapping inst lines).
    """
    out: dict[int, str] = {}
    for inst in setup.instruments.values():
        if inst.channel != 10 or inst.note is None:
            continue
        if inst.note in out:
            continue
        out[inst.note] = drum_port_name(inst.name)
    return out


class DrumSplitSink(Sink):
    """Wraps another sink; intercepts ch10 notes and routes per-drum.

    Parameters
    ----------
    underlying:
        Sink that handles everything that isn't a routed ch10 note —
        typically the existing :class:`MultiPortSink`-based composite
        chain from Player._make_sink.
    drum_ports:
        ``{port_name: open mido port}`` — one entry per drum inst plus
        the catch-all ``slackbeatz-drum-other`` port. Caller opens these
        via :meth:`MultiPortSink.open` or directly via ``mido.open_output``.
    note_to_port:
        ``{note_number: port_name}`` from :func:`build_drum_note_map`.
    """

    DRUM_CHANNEL_0IDX = 9  # ch10 in 1-indexed

    def __init__(
        self,
        underlying: Sink,
        drum_ports: dict[str, mido.ports.BaseOutput],
        note_to_port: dict[int, str],
    ) -> None:
        self.underlying = underlying
        self.drum_ports = drum_ports
        self.note_to_port = note_to_port

    def open(self) -> None:
        # The underlying sink owns its own lifecycle; drum ports are
        # opened by the caller (shared with MultiPortSink).
        self.underlying.open()

    def send(self, msg: mido.Message) -> None:
        # Only intercept channel-bearing events on ch10. Everything
        # else (other channels, sysex, meta) falls through.
        if (
            hasattr(msg, "channel")
            and msg.channel == self.DRUM_CHANNEL_0IDX
            and msg.type in ("note_on", "note_off")
        ):
            port_name = self.note_to_port.get(msg.note, DRUM_OTHER_PORT)
            port = self.drum_ports.get(port_name)
            if port is not None:
                port.send(msg)
            return
        self.underlying.send(msg)

    def close(self) -> None:
        try:
            self.underlying.close()
        except Exception:
            pass
        # Drum ports are caller-owned (opened with virtual=True at
        # song-load time and kept alive across playback restarts);
        # not closing here.
