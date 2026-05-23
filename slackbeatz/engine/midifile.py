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

For audio rendering via a General-MIDI soundfont, each non-drum channel
gets a ``program_change`` at tick 0 picked by the gen's ``(type, style)``
pair — without this, every channel defaults to GM program 0 (Acoustic
Grand Piano) and a song sounds like a piano recital. Each gen can
override the default via a ``program=N`` knob.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import mido

from slackbeatz.engine.clock import PPQ
from slackbeatz.engine.scheduler import build_tempo_map, render_events
from slackbeatz.model.song import ResolvedSong


# Per-(type, style) default GM program number (0-indexed).
# Reference: https://en.wikipedia.org/wiki/General_MIDI#Program_change_events
_GM_PROGRAM_DEFAULTS: dict[tuple[str, str], int] = {
    # Bass: 38 Synth Bass 1 for euclid/psytrance (TB-303-ish punchy),
    # 39 Synth Bass 2 for deep_techno (warmer / longer-sustained).
    ("bass", "euclid"):       38,
    ("bass", "deep_techno"):  39,
    ("bass", "psytrance"):    38,

    # Melody: Saw Lead (81) for euclid / psytrance — bright, cuts through.
    # Deep techno wants a softer voice; Lead 8 voice/halo (87 Bass+Lead)
    # is too aggressive — use Pad 1 (new age) 88 for sustained modal lead.
    ("melody", "euclid"):       81,
    ("melody", "deep_techno"):  88,
    ("melody", "psytrance"):    80,  # Square Lead — psytrance staple

    # Chords / pads: Warm Pad (89) for euclid, Pad 4 choir (91) for
    # deep_techno (jazzy choir-ish), Pad 6 Metallic (94) for psytrance.
    ("chords", "euclid"):       89,
    ("chords", "deep_techno"):  91,
    ("chords", "psytrance"):    94,

    # Candy / risers: FX 5 brightness (100) for euclid build/drop sweeps,
    # FX 7 echoes (102) for deep_techno slow LFO modulation,
    # FX 8 sci-fi (103) for psytrance acid sweeps.
    ("candy", "euclid"):       100,
    ("candy", "deep_techno"): 102,
    ("candy", "psytrance"):   103,

    # rhythm / drums live on the GM percussion channel (MIDI ch 10);
    # FluidSynth auto-routes to the drum-kit bank there, no program
    # change needed.
}


def _program_for_gen(gen) -> int | None:
    """Resolve a GM program number for a gen.

    Returns ``None`` for drum-channel gens (FluidSynth handles those
    automatically on channel 10) and when no default is registered for
    the gen's ``(type, style)`` pair.
    """
    # Explicit override via knob wins.
    prog = gen.knobs.get("program")
    if isinstance(prog, int):
        return prog
    # Drum-style gens — let FluidSynth's percussion channel handle them.
    if gen.type_ in ("rhythm", "drums"):
        return None
    return _GM_PROGRAM_DEFAULTS.get((gen.type_, gen.style))


def _gens_by_channel(song: ResolvedSong) -> dict[int, list]:
    """Map 0-indexed MIDI channel → list of resolved gens that emit there."""
    out: dict[int, list] = defaultdict(list)
    for gen in song.gens.values():
        if gen.instrument is not None:
            out[gen.instrument.channel - 1].append(gen)
        elif gen.kit is not None:
            out[gen.kit.channel - 1].append(gen)
    return out


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

    gens_by_channel = _gens_by_channel(song)
    for ch in sorted(by_channel):
        track = mido.MidiTrack()
        mf.tracks.append(track)
        # Name the track so DAWs label the row.
        track.append(mido.MetaMessage("track_name", name=f"ch{ch + 1}", time=0))
        # Pick a GM patch for this channel based on the first gen using
        # it. If two gens share a channel and want different patches the
        # user can override per-gen via `program=N`; otherwise the first
        # one wins (we sort by handle for stable output).
        gens_here = sorted(gens_by_channel.get(ch, []), key=lambda g: g.handle)
        for gen in gens_here:
            program = _program_for_gen(gen)
            if program is not None:
                track.append(
                    mido.Message(
                        "program_change", channel=ch, program=program, time=0
                    )
                )
                break  # only the first gen's patch applies to the channel
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
