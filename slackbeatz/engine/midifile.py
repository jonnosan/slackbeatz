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
    # Bass: 38 Synth Bass 1 for euclid/psytrance/acid (TB-303-ish punchy),
    # 39 Synth Bass 2 for deep_techno (warmer / longer-sustained),
    # 34 Electric Bass (Pick) for vaporwave (lazy mid-80s pick bass).
    # Acid stays on 38 — the closest GM patch to a real TB-303.
    ("bass", "rolling"):       38,
    ("bass", "subdrone"):  39,
    ("bass", "gallop"):    38,
    ("bass", "mellow_pick"):    34,
    ("bass", "acid_303"):         38,
    # Dub techno: 92 Pad 5 (Bowed) — a warm sustained drone that sits
    # under the chord stabs without competing for attention.
    ("bass", "sustain_drone"):   92,
    # Drum'n'bass: 39 Synth Bass 2 — the warm sub-bass voice that
    # sits low under the breakbeat.
    ("bass", "reese"): 39,
    # Garage: 38 Synth Bass 1 — punchy sub-bass.
    ("bass", "two_step_sub"):       38,
    # Lofi: Acoustic Bass (33) — warm fingered upright character.
    ("bass", "acoustic_walk"):         33,

    # Sub-bass — Synth Bass 2 (39) for the warmer-sustained voicings,
    # Synth Bass 1 (38) for the punchier styles. GM doesn't have a
    # dedicated "sub" voice; 39 is the closest deep-sub texture in the
    # standard FluidSynth soundfont.
    ("subbass", "euclid"):       39,
    ("subbass", "deep_techno"):  39,
    ("subbass", "psytrance"):    38,   # punchier — matches the gallop
    ("subbass", "vaporwave"):    39,
    ("subbass", "acid"):         39,
    ("subbass", "dub_techno"):   39,
    ("subbass", "drum_and_bass"): 39,   # the Reese is a sustained 39
    ("subbass", "garage"):       38,    # snappy
    ("subbass", "lofi"):         39,

    # Melody: Saw Lead (81) for euclid / Square Lead (80) for psytrance —
    # bright, cuts through. Deep techno wants Pad 1 new age (88).
    # Vaporwave wants Tenor Sax (66). Acid uses 87 (Bass+Lead) as the
    # occasional stab voice — closest to an organ punch on top of the 303.
    ("melody", "euclid_riff"):       81,
    ("melody", "sparse_pad_lead"):  88,
    ("melody", "psy_lead"):    80,
    ("melody", "lazy_sax"):    66,
    ("melody", "acid_stab"):         87,
    ("melody", "distant_lead"):   88,   # Pad 1 — distant lead
    ("melody", "atmos_lead"): 88,  # Pad 1 — sparse atmospheric lead
    ("melody", "vocal_chop"):       53,   # Voice Aahs — vocal-stab feel
    # Lofi: Electric Piano 2 (5) — the warm Rhodes Mk II sound that
    # defines lofi melody. Could also use Vibraphone (11) or
    # Soprano Sax (64) for variety.
    ("melody", "rhodes_phrase"):          5,

    # Chords / pads: Warm Pad (89) for euclid, Pad 4 choir (91) for
    # deep_techno, Pad 6 Metallic (94) for psytrance, Electric Piano 1
    # Rhodes (4) for vaporwave. Acid uses Rock Organ (18) — the
    # Hammond stab that punctuates Phuture-style productions.
    ("chords", "triad_sustain"):       89,
    ("chords", "pad_drift"):  91,
    ("chords", "psy_swell"):    94,
    ("chords", "arp_walk"):     4,
    ("chords", "sustained_dyad"):         18,
    # Dub techno: 90 Pad 3 (Polysynth) — the iconic chord-stab voice.
    ("chords", "offbeat_stab"):   90,
    # Drum'n'bass: 89 Warm Pad — atmospheric lush voicings.
    ("chords", "atmos_pad"): 89,
    # Garage: 5 Electric Piano 2 (Wurli) — jazzy R&B-flavoured stabs.
    ("chords", "wurli_chop"):        5,
    # Lofi: Electric Piano 1 (4) — the classic Rhodes Mk I sound.
    # Pairs perfectly with EP2 melody and acoustic bass.
    ("chords", "rhodes_chord"):          4,

    # Candy: FX 5 brightness (100) for euclid sweeps, FX 7 echoes (102)
    # for deep_techno LFO modulation, FX 8 sci-fi (103) for psytrance
    # acid sweeps, Tubular Bells (14) for vaporwave. Acid uses 100 —
    # the same brightness as euclid, just sparser.
    ("candy", "euclid_riser"):       100,
    ("candy", "slow_lfo"): 102,
    ("candy", "psy_sweep"):   103,
    ("candy", "bell_lfo"):    14,
    ("candy", "acid_sweep"):        100,
    ("candy", "drone_lfo"):  99,    # FX 4 atmosphere — slow textural drone
    ("candy", "atmos_lfo"): 102,  # FX 7 Echoes — atmospheric texture
    ("candy", "minimal_lfo"):       100,   # FX 5 Brightness — short stabs
    # Lofi: FX 1 (96) — rain-like noise that approximates vinyl crackle.
    ("candy", "crackle_lfo"):          96,

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
    if gen.type_ == "rhythm":
        return None
    return _GM_PROGRAM_DEFAULTS.get((gen.type_, gen.style))


def program_map(song: ResolvedSong) -> dict[int, int]:
    """Return ``{channel_1_indexed: gm_program}`` for *song*'s pitched
    gens. Mirrors what ``scheduler._initial_program_changes`` sends —
    used by the GUI's Instruments tab to pre-populate the per-channel
    program dropdowns + re-sync them on player state changes.

    The first gen on each channel wins (matches the scheduler's own
    de-dup rule). Drum gens are skipped (they have no GM program;
    FluidSynth auto-routes ch 10 to the percussion bank).
    """
    out: dict[int, int] = {}
    for gen in song.gens.values():
        if gen.instrument is None:
            continue
        channel = gen.instrument.channel  # already 1-indexed
        if channel in out:
            continue
        prog = _program_for_gen(gen)
        if prog is None:
            continue
        out[channel] = prog
    return out


def _track_label_for(song: ResolvedSong, channel_0idx: int) -> str:
    """Pick a descriptive track name for the given 0-indexed MIDI
    channel. Used as the SMF track_name meta event so DAW arrangement
    views show 'lead' / 'bass' / 'pad' / 'drums' rather than 'ch1' /
    'ch2' / ... Falls back to ``ch{N}`` only if we can't find any
    inst on this channel.

    Channel 9 (= MIDI channel 10) always labels as 'drums' regardless
    of which inst maps there — that's the GM percussion convention.
    """
    channel_1idx = channel_0idx + 1
    if channel_1idx == 10:
        return "drums"

    # Find any instrument or kit on this channel.
    for inst in song.setup.instruments.values():
        if inst.channel == channel_1idx:
            return inst.name
    for kit in song.setup.kits.values():
        if kit.channel == channel_1idx:
            return kit.name
    return f"ch{channel_1idx}"


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

    # ----- Track 0: tempo map + song name ---------------------------
    tempo_track = mido.MidiTrack()
    mf.tracks.append(tempo_track)
    # Top-level track name so the DAW labels the song in its
    # arrangement view / file browser hover.
    tempo_track.append(mido.MetaMessage("track_name", name=song.name or "slackbeatz", time=0))
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
        # Name the track using the inst name from the setup so DAWs
        # (Ableton, Logic, Reaper) show meaningful labels like
        # "lead / bass / pad / drums" instead of "ch1 / ch2 / ...".
        # MIDI channel 10 = GM percussion → label "drums".
        track_label = _track_label_for(song, ch)
        track.append(mido.MetaMessage("track_name", name=track_label, time=0))
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
