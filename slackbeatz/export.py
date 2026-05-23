"""Stems-bundle export — write a portable folder/zip that any DAW
can import as audio stems + MIDI.

Structure of a bundle:

    <song_name>/
        <song_name>.mid             # full multi-track MIDI
        stems/
            01_drums.wav            # rendered per-channel audio
            02_bass.wav
            03_lead.wav
            …
        README.md                   # human-readable manifest
        manifest.json               # machine-readable manifest

Stems are produced by writing a per-channel MIDI file (only that one
channel's events, plus the tempo map) and rendering each through
FluidSynth with --fast-render. The full MIDI is also included so a
DAW user can drag it onto MIDI tracks for editing while keeping the
WAVs on audio tracks for the actual sound.

Output is a directory (default) or a single .zip — slackbeatz export
detects the suffix.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import mido

from slackbeatz.audio import find_soundfont, require_tool
from slackbeatz.engine.clock import PPQ, bars_to_ticks
from slackbeatz.engine.midifile import (
    _GM_PROGRAM_DEFAULTS,
    _program_for_gen,
    build_midifile,
)
from slackbeatz.engine.scheduler import build_tempo_map
from slackbeatz.model.song import ResolvedSong


def export_bundle(
    song: ResolvedSong,
    output_path: Path,
    *,
    soundfont: Path | None = None,
    sample_rate: int = 44100,
) -> Path:
    """Build a stems bundle for *song* at *output_path*.

    If *output_path* ends in ``.zip`` the bundle is zipped; otherwise
    it's written as a folder. Returns the actual output path.
    """
    sf = soundfont or find_soundfont(None)
    fluidsynth = require_tool("fluidsynth")

    # Stage the bundle in a temp dir, then zip or move into place.
    with tempfile.TemporaryDirectory() as staging:
        stage = Path(staging) / (song.name.replace(" ", "_") or "song")
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "stems").mkdir()

        # Full multi-track MIDI.
        midi_name = f"{stage.name}.mid"
        full_midi_path = stage / midi_name
        full_mid = build_midifile(song)
        full_mid.save(str(full_midi_path))

        # Identify channels with notes — those become stems.
        channels_used = sorted({
            msg.channel for tr in full_mid.tracks for msg in tr
            if msg.type == "note_on" and msg.velocity > 0
        })

        # Per-channel stem rendering.
        stem_files: list[dict] = []
        for stem_index, channel_0idx in enumerate(channels_used, start=1):
            track_name = _track_label_for_channel(song, channel_0idx)
            stem_filename = (
                f"{stem_index:02d}_{_safe_filename(track_name)}.wav"
            )
            stem_path = stage / "stems" / stem_filename

            # Build a per-channel MIDI from the full one.
            stem_midi = _filter_midi_to_channel(full_mid, channel_0idx)
            with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
                tmp_midi_path = Path(tmp.name)
            try:
                stem_midi.save(str(tmp_midi_path))
                # Run fluidsynth on the per-channel MIDI.
                subprocess.run(
                    [
                        fluidsynth, "-ni", "-q",
                        "-r", str(sample_rate),
                        f"--fast-render={stem_path}",
                        str(sf), str(tmp_midi_path),
                    ],
                    check=True,
                    stdin=subprocess.DEVNULL,
                )
            finally:
                tmp_midi_path.unlink(missing_ok=True)

            program = _channel_program(song, channel_0idx)
            stem_files.append({
                "channel": channel_0idx + 1,
                "name": track_name,
                "program": program,
                "stem": f"stems/{stem_filename}",
            })

        # Manifest + README.
        manifest = _build_manifest(song, midi_name, stem_files)
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2),
        )
        (stage / "README.md").write_text(_build_readme(song, manifest))

        # Materialise — zip or folder.
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() == ".zip":
            _zip_bundle(stage, output_path)
        else:
            if output_path.exists():
                shutil.rmtree(output_path)
            shutil.copytree(stage, output_path)
        return output_path


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _track_label_for_channel(song: ResolvedSong, channel_0idx: int) -> str:
    """Pick the inst/kit name for this channel, fallback to 'ch{N}'."""
    channel_1idx = channel_0idx + 1
    if channel_1idx == 10:
        return "drums"
    for inst in song.setup.instruments.values():
        if inst.channel == channel_1idx:
            return inst.name
    for kit in song.setup.kits.values():
        if kit.channel == channel_1idx:
            return kit.name
    return f"ch{channel_1idx}"


def _channel_program(song: ResolvedSong, channel_0idx: int) -> int | None:
    """Return the GM program that's active on this channel (from the
    first gen that emits there)."""
    for gen in song.gens.values():
        if gen.instrument is not None and gen.instrument.channel - 1 == channel_0idx:
            return _program_for_gen(gen)
    return None


def _safe_filename(name: str) -> str:
    """Strip / lowercase for filesystem-safe filenames."""
    cleaned = "".join(c if c.isalnum() else "_" for c in name.lower())
    return cleaned.strip("_") or "track"


def _filter_midi_to_channel(
    full_mid: mido.MidiFile,
    target_channel_0idx: int,
) -> mido.MidiFile:
    """Build a new MidiFile preserving the tempo map + only the
    target channel's note/CC/program_change events. Other channels'
    tracks are dropped entirely so fluidsynth only renders the one.
    """
    out = mido.MidiFile(ticks_per_beat=full_mid.ticks_per_beat, type=full_mid.type)

    # Track 0: tempo map + meta. No channel events here, keep as-is.
    if full_mid.tracks:
        out.tracks.append(full_mid.tracks[0])

    # Tracks 1..N: pick the one matching our target channel.
    for tr in full_mid.tracks[1:]:
        for msg in tr:
            if hasattr(msg, "channel") and msg.channel == target_channel_0idx:
                out.tracks.append(tr)
                break
    return out


def _build_manifest(
    song: ResolvedSong,
    midi_name: str,
    stems: list[dict],
) -> dict:
    """Build the JSON manifest of the bundle."""
    tempo_map = build_tempo_map(song)
    segments = []
    cursor_bar = 0
    for idx, part_name in enumerate(song.arrangement):
        part = song.parts[part_name]
        bars = part.bars
        segments.append({
            "part": part_name,
            "role": part.role,
            "start_bar": cursor_bar,
            "end_bar": cursor_bar + bars,
            "bars": bars,
            "tempo": part.tempo,
            "key": part.key,
        })
        cursor_bar += bars

    return {
        "generator": "slackbeatz",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "song_name": song.name,
        "song_key": song.parts[song.arrangement[0]].key if song.arrangement else "Am",
        "total_bars": cursor_bar,
        "duration_seconds": tempo_map.time_at(tempo_map.end_tick),
        "midi_file": midi_name,
        "arrangement": segments,
        "stems": stems,
    }


def _build_readme(song: ResolvedSong, manifest: dict) -> str:
    """Render a Markdown README from the manifest."""
    lines = [
        f"# {song.name}",
        "",
        f"Generated by slackbeatz on {manifest['generated']}.",
        f"Total: {manifest['total_bars']} bars, "
        f"{manifest['duration_seconds']:.1f} seconds.",
        "",
        "## Files",
        "",
        f"- `{manifest['midi_file']}` — full multi-track MIDI. "
        "Drag onto MIDI tracks in your DAW to edit notes; one track "
        "per slackbeatz channel (named after the inst).",
        "- `stems/` — per-channel rendered audio. Drag onto audio "
        "tracks in your DAW for the rendered sound.",
        "- `manifest.json` — machine-readable metadata.",
        "",
        "## Stems",
        "",
    ]
    for stem in manifest["stems"]:
        prog = stem.get("program")
        prog_str = f"GM {prog}" if prog is not None else "auto (drum kit)"
        lines.append(
            f"- `{stem['stem']}` — channel {stem['channel']} "
            f"({stem['name']}), {prog_str}"
        )
    lines.extend([
        "",
        "## Arrangement",
        "",
        "| Part | Role | Bars | Tempo | Key |",
        "|---|---|---|---|---|",
    ])
    for seg in manifest["arrangement"]:
        lines.append(
            f"| {seg['part']} | {seg['role']} | "
            f"{seg['start_bar']}-{seg['end_bar']} | "
            f"{seg['tempo']} BPM | {seg['key']} |"
        )
    lines.extend([
        "",
        "## How to import",
        "",
        "### Ableton Live",
        "",
        "1. Drag the `.mid` into the Arrangement View.",
        "2. Ableton creates one MIDI track per slackbeatz channel "
        "(named lead / bass / pad / drums / …).",
        "3. Drag each `stems/NN_*.wav` onto its own audio track.",
        "4. The MIDI's tempo map already includes per-part BPM changes.",
        "",
        "### Bitwig Studio / Studio One / Cubase Pro",
        "",
        "Same as Ableton — drag MIDI + stems. These DAWs also accept "
        "`.dawproject` if you want the bundle as a single file; "
        "that's a future feature.",
        "",
        "### Reaper",
        "",
        "Insert → Media File for the stems, Insert → MIDI for the .mid. "
        "Or just drag everything into the timeline.",
        "",
    ])
    return "\n".join(lines)


def _zip_bundle(staging_dir: Path, output_zip: Path) -> None:
    """Zip the staging directory into output_zip."""
    if output_zip.exists():
        output_zip.unlink()
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in staging_dir.rglob("*"):
            if path.is_file():
                arcname = path.relative_to(staging_dir.parent)
                zf.write(path, arcname)
