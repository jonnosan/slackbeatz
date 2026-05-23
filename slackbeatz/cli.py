"""Command-line interface for slackbeatz."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import slackbeatz.generators  # noqa: F401 — trigger algorithm registrations
from slackbeatz.dsl.parser import ParseError, parse_file
from slackbeatz.engine.clock_source import ClockSource, ExternalClock, InternalClock
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

    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    main()
