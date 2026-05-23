"""Convert a :class:`ResolvedSong` into a Standard MIDI File.

Used by the ``audio`` subcommand (and, when phase 2 ships, by the
``render`` subcommand) to materialise a song as bytes a softsynth can
consume. We reuse :func:`render_events` for event generation so the
file output is bit-identical to what realtime playback would send.

Layout:

* Type-1 MIDI file at PPQ 480 (the engine's internal resolution).
* Track 0 — meta track carrying ``set_tempo`` events at each tempo-
  segment boundary.
* Track 1..N — one per MIDI channel used by the song (so a DAW import
  shows one row per instrument), each carrying its note/CC stream.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import mido

from slackbeatz.engine.clock import PPQ
from slackbeatz.engine.scheduler import build_tempo_map, render_events
from slackbeatz.model.song import ResolvedSong


def build_midifile(song: ResolvedSong) -> mido.MidiFile:
    """Render *song* to an in-memory :class:`mido.MidiFile`."""
    mf = mido.MidiFile(ticks_per_beat=PPQ, type=1)

    # ----- Track 0: tempo map ---------------------------------------
    tempo_track = mido.MidiTrack()
    mf.tracks.append(tempo_track)
    tempo_map = build_tempo_map(song)
    prev_tick = 0
    for seg in tempo_map.segments:
        delta = seg.start_tick - prev_tick
        microseconds_per_beat = int(round(60_000_000 / seg.bpm))
        tempo_track.append(
            mido.MetaMessage(
                "set_tempo", tempo=microseconds_per_beat, time=delta
            )
        )
        prev_tick = seg.start_tick
    tempo_track.append(mido.MetaMessage("end_of_track", time=0))

    # ----- Tracks 1..N: per-channel event streams -------------------
    events = render_events(song)
    by_channel: dict[int, list[tuple[int, mido.Message]]] = defaultdict(list)
    for tick, msg in events:
        by_channel[msg.channel].append((tick, msg))

    for ch in sorted(by_channel):
        track = mido.MidiTrack()
        mf.tracks.append(track)
        # Optional but helpful: name the track so DAWs label the row.
        track.append(mido.MetaMessage("track_name", name=f"ch{ch + 1}", time=0))
        prev_tick = 0
        for tick, msg in by_channel[ch]:
            delta = tick - prev_tick
            # mido.Message is mutable on `time`; .copy(time=…) is the
            # paranoid form that always works.
            track.append(msg.copy(time=delta))
            prev_tick = tick
        track.append(mido.MetaMessage("end_of_track", time=0))

    return mf


def write_midifile(song: ResolvedSong, output_path: str | Path) -> Path:
    """Render *song* and save the result to *output_path*. Returns the path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    build_midifile(song).save(str(output_path))
    return output_path
