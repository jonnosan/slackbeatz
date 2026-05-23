"""Command-line interface for slackbeatz."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import subprocess
import tempfile
import time

import slackbeatz.generators  # noqa: F401 — trigger algorithm registrations
from slackbeatz.audio import (
    MissingToolError,
    SoundfontError,
    find_soundfont,
    render_audio,
    require_tool,
)
from slackbeatz.compose import compose_from_text
from slackbeatz.dsl.parser import ParseError, parse_file
from slackbeatz.engine.clock_source import ClockSource, ExternalClock, InternalClock
from slackbeatz.engine.midifile import write_midifile
from slackbeatz.engine.scheduler import Scheduler, build_tempo_map
from slackbeatz.generators.registry import list_generators
from slackbeatz.setup.loader import (
    list_bundled_setups,
    load_setup,
    setup_from_ast,
    SetupError,
)
from slackbeatz.setup.model import Setup
from slackbeatz.setup.resolve import ResolveError, resolve_song
from slackbeatz.sinks.midifile import MidiFileSink
from slackbeatz.sinks.realtime import NoMidiPortError, RealtimeSink, available_ports


# --------------------------------------------------------------------------
# Shared loading helpers
# --------------------------------------------------------------------------

def _load_setup_for_song(
    song_path: Path,
    file_ast,
    cli_setup: str | None,
) -> Setup:
    """Apply the resolution priority for which setup to bind a song against.

    Priority: ``--setup`` flag → ``setup "..."`` line in the song →
    inline ``setup`` block in the same file → empty setup (sketch mode).
    """
    if cli_setup is not None:
        return load_setup(cli_setup, base_path=song_path.parent)
    song = file_ast.song
    if song is not None and song.setup_ref is not None:
        return load_setup(song.setup_ref, base_path=song_path.parent)
    if file_ast.setup is not None:
        return setup_from_ast(file_ast.setup)
    return Setup(name="(empty)")


def _build_clock(args, tempo_map) -> ClockSource:
    if args.clock == "internal":
        return InternalClock(tempo_map)
    elif args.clock == "external":
        if args.clock_port is None:
            print(
                "error: --clock external requires --clock-port <name>",
                file=sys.stderr,
            )
            sys.exit(2)
        clock = ExternalClock(args.clock_port)
        clock.open()  # raises NotImplementedError in v1
        return clock
    raise ValueError(f"unknown clock mode: {args.clock!r}")


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------

def cmd_play(args) -> int:
    song_path = Path(args.song_file)
    try:
        file_ast = parse_file(song_path)
        if file_ast.song is None:
            print(f"error: {song_path}: no song block found", file=sys.stderr)
            return 2
        setup = _load_setup_for_song(song_path, file_ast, args.setup)
        resolved = resolve_song(file_ast.song, setup, cli_seed=args.seed)
    except (ParseError, ResolveError, SetupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        sink = RealtimeSink(port_name=args.port)
        tempo_map = build_tempo_map(resolved)
        clock = _build_clock(args, tempo_map)
        Scheduler(resolved, sink, clock).run()
    except NoMidiPortError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except NotImplementedError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    return 0


def cmd_check(args) -> int:
    song_path = Path(args.song_file)
    try:
        file_ast = parse_file(song_path)
        if file_ast.song is None:
            print(f"error: {song_path}: no song block found", file=sys.stderr)
            return 2
        setup = _load_setup_for_song(song_path, file_ast, args.setup)
        resolved = resolve_song(file_ast.song, setup, cli_seed=0)
    except (ParseError, ResolveError, SetupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    n_bars = sum(resolved.parts[p].bars for p in resolved.arrangement)
    print(
        f"{song_path}: ok — {len(resolved.gens)} gens, "
        f"{len(resolved.parts)} parts, {len(resolved.arrangement)} arrangement "
        f"slots, {n_bars} bars total"
    )
    return 0


def cmd_list_generators(_args) -> int:
    for type_, style in list_generators():
        print(f"{type_}\t{style}")
    return 0


def cmd_list_setups(_args) -> int:
    for name in list_bundled_setups():
        print(name)
    return 0


def cmd_list_ports(_args) -> int:
    ports = available_ports()
    if not ports:
        print("(no MIDI output ports available)")
        if sys.platform == "darwin":
            print(
                "On macOS, enable the IAC Driver in Audio MIDI Setup → "
                "MIDI Studio to create a virtual port.",
                file=sys.stderr,
            )
        return 1
    for p in ports:
        print(p)
    return 0


def cmd_audio(args) -> int:
    song_path = Path(args.song_file)
    try:
        file_ast = parse_file(song_path)
        if file_ast.song is None:
            print(f"error: {song_path}: no song block found", file=sys.stderr)
            return 2
        setup = _load_setup_for_song(song_path, file_ast, args.setup)
        resolved = resolve_song(file_ast.song, setup, cli_seed=args.seed)
    except (ParseError, ResolveError, SetupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        soundfont = find_soundfont(args.soundfont)
    except SoundfontError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    # Write the MIDI to a temp file so fluidsynth can read it.
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
        tmp_mid = Path(tmp.name)
    try:
        write_midifile(resolved, tmp_mid)
        render_audio(
            tmp_mid,
            output_path,
            soundfont,
            sample_rate=args.sample_rate,
            bitrate=args.bitrate,
        )
    except MissingToolError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"error: subprocess failed (exit {e.returncode}): {e.cmd[0]}", file=sys.stderr)
        return 1
    finally:
        tmp_mid.unlink(missing_ok=True)

    size_kb = output_path.stat().st_size // 1024
    print(f"wrote {output_path} ({size_kb} KB)")
    return 0


def cmd_live(args) -> int:
    """Play a song via a spawned FluidSynth — single command, audio out
    of the speakers, no DAW required."""
    # Resolve source: explicit .sb file or compose from text.
    if args.text:
        sb_content = compose_from_text(args.text)
        with tempfile.NamedTemporaryFile(
            suffix=".sb", delete=False, mode="w", encoding="utf-8",
        ) as tf:
            tf.write(sb_content)
            song_path = Path(tf.name)
        cleanup_song = True
    elif args.song_file:
        song_path = Path(args.song_file)
        cleanup_song = False
    else:
        print("error: pass either a .sb file or --text \"phrase\"", file=sys.stderr)
        return 2

    try:
        file_ast = parse_file(song_path)
        if file_ast.song is None:
            print(f"error: {song_path}: no song block found", file=sys.stderr)
            return 2
        setup = _load_setup_for_song(song_path, file_ast, args.setup)
        resolved = resolve_song(file_ast.song, setup, cli_seed=args.seed)
    except (ParseError, ResolveError, SetupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        soundfont = find_soundfont(args.soundfont)
        fluidsynth_bin = require_tool("fluidsynth")
    except (SoundfontError, MissingToolError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Snapshot existing MIDI output ports so we can identify the new one
    # FluidSynth registers when it starts.
    before_ports = set(available_ports())
    fs_proc = subprocess.Popen(
        [
            fluidsynth_bin,
            "-a", "coreaudio",
            "-m", "coremidi",
            "-o", f"synth.gain={args.gain}",
            "-o", f"synth.reverb.room-size={args.reverb}",
            "-o", "synth.chorus.active=1",
            "-q",
            "-ni",
            str(soundfont),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Poll for the new MIDI port (up to ~4 seconds).
    new_port = None
    for _ in range(40):
        time.sleep(0.1)
        if fs_proc.poll() is not None:
            print("error: fluidsynth exited before opening its MIDI port", file=sys.stderr)
            return 1
        diff = set(available_ports()) - before_ports
        if diff:
            new_port = next(iter(diff))
            break

    if new_port is None:
        fs_proc.terminate()
        fs_proc.wait(timeout=2)
        print("error: fluidsynth started but didn't expose a MIDI port", file=sys.stderr)
        return 1

    print(f"slackbeatz: streaming to FluidSynth on {new_port!r} — press Ctrl+C to stop")
    try:
        sink = RealtimeSink(port_name=new_port)
        tempo_map = build_tempo_map(resolved)
        clock = InternalClock(tempo_map)
        Scheduler(resolved, sink, clock).run()
    except KeyboardInterrupt:
        print("\nslackbeatz: interrupted")
    except NoMidiPortError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        fs_proc.terminate()
        try:
            fs_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            fs_proc.kill()
        if cleanup_song:
            song_path.unlink(missing_ok=True)
    return 0


def cmd_from_text(args) -> int:
    """Compose a `.sb` from an arbitrary input string.

    Lands the composed file on disk; optionally also renders an audio
    file (.wav / .mp3) when ``-o`` ends in an audio extension.
    """
    sb_content = compose_from_text(args.text)
    out = Path(args.output) if args.output else None
    if out is None:
        # No --output → print the .sb to stdout (compose-only mode).
        print(sb_content)
        return 0
    ext = out.suffix.lower()
    if ext in (".sb", ""):
        out.write_text(sb_content)
        print(f"wrote {out}")
        return 0
    if ext in (".wav", ".mp3"):
        # Compose + render in one step. Stash the .sb next to the audio
        # so the user can re-render / tweak.
        sb_path = out.with_suffix(".sb")
        sb_path.write_text(sb_content)
        try:
            file_ast = parse_file(sb_path)
            assert file_ast.song is not None
            setup = _load_setup_for_song(sb_path, file_ast, None)
            resolved = resolve_song(file_ast.song, setup, cli_seed=0)
        except (ParseError, ResolveError, SetupError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        try:
            soundfont = find_soundfont(None)
        except SoundfontError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
            tmp_mid = Path(tmp.name)
        try:
            from slackbeatz.engine.midifile import write_midifile
            write_midifile(resolved, tmp_mid)
            render_audio(tmp_mid, out, soundfont)
        except MissingToolError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as e:
            print(f"error: subprocess failed (exit {e.returncode}): {e.cmd[0]}", file=sys.stderr)
            return 1
        finally:
            tmp_mid.unlink(missing_ok=True)
        print(f"wrote {sb_path} + {out}")
        return 0
    print(f"error: unsupported output extension {ext!r} (use .sb / .wav / .mp3)", file=sys.stderr)
    return 2


def cmd_render(args) -> int:
    song_path = Path(args.song_file)
    try:
        file_ast = parse_file(song_path)
        if file_ast.song is None:
            print(f"error: {song_path}: no song block found", file=sys.stderr)
            return 2
        setup = _load_setup_for_song(song_path, file_ast, args.setup)
        resolved = resolve_song(file_ast.song, setup, cli_seed=args.seed)
    except (ParseError, ResolveError, SetupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    sink = MidiFileSink(args.output)
    try:
        sink.open()
    except NotImplementedError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    _ = resolved  # silence unused warning until phase-2 wires this up
    return 0


# --------------------------------------------------------------------------
# Parser construction
# --------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slackbeatz",
        description="Chance-driven MIDI song generator with a tiny DSL.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # play
    sp = sub.add_parser("play", help="render a song to a MIDI output port")
    sp.add_argument("song_file", help="path to a .sb song file")
    sp.add_argument("--setup", help="bundled name or path to a setup file")
    sp.add_argument("--port", help="MIDI output port (default: first available)")
    sp.add_argument("--seed", type=int, default=0,
                    help="fallback seed when the song doesn't set one (default 0)")
    sp.add_argument("--clock", choices=("internal", "external"), default="internal",
                    help="clock mode (default internal; external is v2)")
    sp.add_argument("--clock-port",
                    help="MIDI input port for external clock (required if --clock external)")
    sp.add_argument("--emit-clock", action="store_true",
                    help="emit MIDI Clock 0xF8 ticks downstream (internal clock only; v2)")
    sp.set_defaults(func=cmd_play)

    # check
    sp = sub.add_parser("check", help="parse and validate a song file without playing")
    sp.add_argument("song_file")
    sp.add_argument("--setup")
    sp.set_defaults(func=cmd_check)

    # list-generators
    sp = sub.add_parser("list-generators", help="show all registered (type, style) pairs")
    sp.set_defaults(func=cmd_list_generators)

    # list-setups
    sp = sub.add_parser("list-setups", help="show bundled setup names")
    sp.set_defaults(func=cmd_list_setups)

    # list-ports
    sp = sub.add_parser("list-ports", help="show available MIDI output ports")
    sp.set_defaults(func=cmd_list_ports)

    # render
    sp = sub.add_parser("render", help="render a song to a .mid file (phase 2)")
    sp.add_argument("song_file")
    sp.add_argument("--setup")
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("-o", "--output", required=True, help="output .mid path")
    sp.set_defaults(func=cmd_render)

    # live — single-command realtime audio via spawned FluidSynth
    sp = sub.add_parser(
        "live",
        help="play a song to audio via spawned FluidSynth — no DAW required",
    )
    sp.add_argument(
        "song_file", nargs="?",
        help="path to a .sb file (omit if using --text)",
    )
    sp.add_argument(
        "--text",
        help="compose from text and play directly — alternative to song_file",
    )
    sp.add_argument("--setup", help="bundled name or path to a setup file")
    sp.add_argument(
        "--soundfont",
        help="path to a .sf2/.sf3 (default: auto-discover or download)",
    )
    sp.add_argument(
        "--gain", type=float, default=0.6,
        help="FluidSynth output gain 0.0–1.0 (default 0.6)",
    )
    sp.add_argument(
        "--reverb", type=float, default=0.8,
        help="FluidSynth reverb room-size 0.0–1.0 (default 0.8)",
    )
    sp.add_argument(
        "--seed", type=int, default=0,
        help="fallback seed when the song doesn't set one (default 0)",
    )
    sp.set_defaults(func=cmd_live)

    # from-text
    sp = sub.add_parser(
        "from-text",
        help="compose a .sb (or render audio) from an arbitrary input string",
    )
    sp.add_argument("text", help="input string — first phrase becomes the title")
    sp.add_argument(
        "-o", "--output",
        help="output path. .sb writes the source file; .wav/.mp3 composes + renders. Omit to print the .sb to stdout.",
    )
    sp.set_defaults(func=cmd_from_text)

    # audio
    sp = sub.add_parser(
        "audio",
        help="render a song to a .wav or .mp3 audio file via FluidSynth + ffmpeg",
    )
    sp.add_argument("song_file")
    sp.add_argument("--setup")
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument(
        "--soundfont",
        help="path to a .sf2/.sf3 file (default: auto-discover or download)",
    )
    sp.add_argument(
        "--bitrate", default="192k", help="MP3 bitrate when output is .mp3 (default 192k)"
    )
    sp.add_argument(
        "--sample-rate", type=int, default=44100, help="audio sample rate (default 44100)"
    )
    sp.add_argument(
        "-o", "--output", required=True,
        help="output path; .wav stops after FluidSynth, .mp3 (or other ffmpeg fmts) goes through ffmpeg",
    )
    sp.set_defaults(func=cmd_audio)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    main()
