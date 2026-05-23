"""Command-line interface for slackbeatz."""

from __future__ import annotations

import argparse
import os
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
    except SoundfontError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        fs_proc, new_port, spawn_err = _spawn_fluidsynth(
            soundfont, gain=args.gain, reverb=args.reverb,
        )
    except MissingToolError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if spawn_err is not None:
        print(f"error: {spawn_err}", file=sys.stderr)
        return 1
    assert fs_proc is not None and new_port is not None

    print(f"slackbeatz: streaming to FluidSynth on {new_port!r} — press Ctrl+C to stop")

    # Optional --surge: spawn one Surge XT window per pitched channel.
    surge_procs: list[subprocess.Popen] = []
    surge_muted_channels: list[int] = []
    if getattr(args, "surge", False):
        from slackbeatz.synthhost import (
            DEFAULT_SURGE_CHANNELS, channel_routing_summary,
            install_hint, is_surge_installed,
            mute_fluidsynth_channels, spawn_surge_xt,
        )
        if not is_surge_installed():
            print(
                f"--surge requested but Surge XT isn't installed. "
                f"Install with:\n  {install_hint()}\n"
                f"Continuing without Surge XT.",
                file=sys.stderr,
            )
        else:
            print(f"\nslackbeatz: spawning {len(DEFAULT_SURGE_CHANNELS)} Surge XT windows.")
            print(channel_routing_summary())
            for inst, ch_1idx in DEFAULT_SURGE_CHANNELS.items():
                proc = spawn_surge_xt(ch_1idx)
                if proc is not None:
                    surge_procs.append(proc)
                    surge_muted_channels.append(ch_1idx - 1)
            print(
                f"\nIn each Surge XT window: Settings → MIDI Settings → "
                f"MIDI Input = {new_port!r}, then set MIDI Channel to "
                f"the channel number shown above.",
            )
            mute_fluidsynth_channels(fs_proc.stdin, surge_muted_channels)

    try:
        sink = RealtimeSink(port_name=new_port)
        tempo_map = build_tempo_map(resolved)
        clock = InternalClock(tempo_map)
        scheduler = Scheduler(resolved, sink, clock)
        # Optional MIDI Clock emitter (driven by --emit-clock).
        clock_emitter = None
        clock_stop_event = None
        if getattr(args, "emit_clock", False):
            import threading as _threading
            from slackbeatz.clock_emitter import ClockEmitter
            clock_stop_event = _threading.Event()
            clock_emitter = ClockEmitter(
                port_name=new_port,
                tempo_map=tempo_map,
                stop_event=clock_stop_event,
            )
            clock_emitter.start()
        if args.gui:
            # Tk needs the main thread, so run playback in a daemon
            # thread and open the tweak window here. Closing the window
            # triggers fluidsynth teardown via the finally block; the
            # daemon thread is then collected by the interpreter.
            import threading

            from slackbeatz.gui import run_tweak_gui

            stop_event = threading.Event()

            def _play() -> None:
                try:
                    scheduler.run()
                except Exception as exc:  # noqa: BLE001 — surface via stderr
                    if not stop_event.is_set():
                        print(f"playback error: {exc}", file=sys.stderr)

            play_thread = threading.Thread(target=_play, daemon=True)
            play_thread.start()
            run_tweak_gui(
                fs_proc.stdin,
                initial_gain=args.gain,
                initial_reverb_room=args.reverb,
                initial_programs=_program_map(resolved),
                surge_port_name=new_port if surge_procs else None,
                on_close=stop_event.set,
            )
        else:
            scheduler.run()
    except KeyboardInterrupt:
        print("\nslackbeatz: interrupted")
    except NoMidiPortError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        if clock_emitter is not None:
            if clock_stop_event is not None:
                clock_stop_event.set()
            clock_emitter.stop()
        # Unmute the Surge-handled channels in FluidSynth so a later
        # run doesn't inherit silent channels.
        if surge_muted_channels:
            from slackbeatz.synthhost import unmute_fluidsynth_channels
            unmute_fluidsynth_channels(fs_proc.stdin, surge_muted_channels)
        # Kill Surge XT instances.
        for sp_proc in surge_procs:
            try:
                sp_proc.terminate()
            except Exception:
                pass
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


def _spawn_fluidsynth(soundfont: Path, *, gain: float, reverb: float) -> tuple[subprocess.Popen[bytes] | None, str | None, str | None]:
    """Spawn a CoreAudio + CoreMIDI FluidSynth and wait for its MIDI port.

    Returns ``(proc, port_name, None)`` on success, or
    ``(None, None, error_message)`` on failure. Shared by ``cmd_live``
    and ``cmd_repl`` — both need the same "spawn FS, find its new port"
    incantation.

    See the inline comment about ``-n`` / ``-i`` for why we use
    ``stdin=PIPE`` with no flag suppression.
    """
    fluidsynth_bin = require_tool("fluidsynth")
    before_ports = set(available_ports())
    proc = subprocess.Popen(
        [
            fluidsynth_bin,
            "-a", "coreaudio",
            "-m", "coremidi",
            "-o", f"synth.gain={gain}",
            "-o", f"synth.reverb.room-size={reverb}",
            "-o", "synth.chorus.active=1",
            "-q",
            str(soundfont),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for _ in range(40):
        time.sleep(0.1)
        if proc.poll() is not None:
            err = ""
            if proc.stderr is not None:
                try:
                    err = proc.stderr.read().decode("utf-8", "replace").strip()
                except Exception:
                    pass
            msg = "fluidsynth exited before opening its MIDI port"
            if err:
                msg += f" (exit {proc.returncode}): {err}"
            return None, None, msg
        diff = set(available_ports()) - before_ports
        if diff:
            return proc, next(iter(diff)), None
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
    return None, None, "fluidsynth started but didn't expose a MIDI port"


def _knob_list_overrides(player) -> str:
    """Show every knob override currently in effect."""
    overrides = player.get_knob_overrides()
    if not overrides:
        return "no knob overrides set — try `/knob HANDLE` to see a gen's knobs"
    lines = ["knob overrides:"]
    for h, k in sorted(overrides.items()):
        for name, val in sorted(k.items()):
            lines.append(f"  {h}.{name} = {val}")
    return "\n".join(lines)


def _knob_list_all_specs() -> str:
    """Show the full registry of available knobs, organised by gen type."""
    from slackbeatz.player import KNOB_CHOICES, KNOB_SPECS

    lines = ["knobs by gen type (range shown for /knob HANDLE NAME VALUE):"]
    for gen_type in ("rhythm", "drums", "bass", "melody", "chords", "candy"):
        lines.append(f"\n  {gen_type}:")
        for name, lo, hi, default, kind in KNOB_SPECS.get(gen_type, []):
            range_str = (
                f"{int(lo)}..{int(hi)}" if kind == "int" else f"{lo}..{hi}"
            )
            lines.append(f"    {name:14s}  {range_str:>14s}  default {default}")
        # String-valued knobs for this gen type, if any.
        for name, choices in KNOB_CHOICES.get(gen_type, {}).items():
            lines.append(f"    {name:14s}  {'|'.join(choices)}")
    lines.append(
        "\n  Type `/knobs gens` to see your current song's gens, "
        "or `/knob HANDLE` for one gen's knobs + values."
    )
    return "\n".join(lines)


def _knob_show_gen(player, handle: str) -> str:
    """Show the available knobs + current values for one gen."""
    from slackbeatz.player import KNOB_CHOICES, KNOB_SPECS

    resolved = player.current_resolved
    if resolved is None:
        return "no song loaded yet — type a phrase to load one"
    if handle not in resolved.gens:
        avail = ", ".join(resolved.gens.keys()) or "(no gens)"
        return f"unknown gen {handle!r}. available: {avail}"
    gen = resolved.gens[handle]
    specs = KNOB_SPECS.get(gen.type_, [])
    choices = KNOB_CHOICES.get(gen.type_, {})
    overrides = player.get_knob_overrides().get(handle, {})

    lines = [f"{handle} ({gen.type_} / {gen.style}):"]
    if not specs and not choices:
        lines.append("  (no tweakable knobs for this gen type)")
        return "\n".join(lines)
    for name, lo, hi, default, kind in specs:
        range_str = (
            f"{int(lo)}..{int(hi)}" if kind == "int" else f"{lo}..{hi}"
        )
        # Current effective value: override > gen.knobs > default
        if name in overrides:
            current = overrides[name]
            tag = "(override)"
        elif name in gen.knobs:
            current = gen.knobs[name]
            tag = "(from .sb)"
        else:
            current = default
            tag = "(default)"
        lines.append(
            f"  {name:14s}  {range_str:>14s}  = {current!s:<8s} {tag}"
        )
    # String-valued knobs (progression / voicing).
    for name, valid_values in choices.items():
        if name in overrides:
            current = overrides[name]
            tag = "(override)"
        elif name in gen.knobs:
            current = gen.knobs[name]
            tag = "(from .sb)"
        else:
            current = "(style default)"
            tag = ""
        lines.append(
            f"  {name:14s}  {'|'.join(valid_values)}\n"
            f"  {'':14s}  current = {current} {tag}"
        )
    lines.append(
        f"\nUsage: /knob {handle} <name> <value> | /knob {handle} <name> (clear)"
    )
    return "\n".join(lines)


def _knob_list_song_gens(player) -> str:
    """List the gens in the currently-loaded song."""
    resolved = player.current_resolved
    if resolved is None:
        return "no song loaded yet — type a phrase to load one"
    lines = ["gens in current song:"]
    for handle, gen in resolved.gens.items():
        lines.append(f"  {handle:12s}  ({gen.type_} / {gen.style})")
    lines.append("\nUse `/knob HANDLE` to see that gen's knobs.")
    return "\n".join(lines)


def _program_map(resolved) -> dict[int, int]:
    """Return ``{channel_1_indexed: gm_program}`` for the song's pitched
    gens. Mirrors what ``scheduler._initial_program_changes`` sends —
    the GUI uses it to pre-populate the per-channel program dropdowns
    so the displayed patches match what slackbeatz is about to send.

    The first gen on each channel wins (matches the scheduler's own
    de-dup rule). Drum gens are skipped.
    """
    from slackbeatz.engine.midifile import _program_for_gen

    out: dict[int, int] = {}
    for gen in resolved.gens.values():
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


def _handle_tweak_command(line: str, fs_stdin) -> str | None:
    """If *line* is a recognised /tweak command, send the matching
    FluidSynth shell command and return a one-line status string.
    Returns ``None`` if the line isn't a tweak — the caller should
    treat it as a compose phrase.

    Supported (the subset most useful at the prompt):

        /gain N             master gain
        /reverb N           reverb room-size
        /reverb on|off      reverb on/off
        /chorus N           chorus depth
        /chorus on|off      chorus on/off
    """
    parts = line.split()
    cmd = parts[0]
    if cmd not in ("/gain", "/reverb", "/chorus"):
        return None
    if len(parts) != 2:
        return f"usage: {cmd} <value>"

    arg = parts[1]
    on_off = arg.lower() in ("on", "off")
    if on_off:
        value: float | int = 1 if arg.lower() == "on" else 0
    else:
        try:
            value = float(arg)
        except ValueError:
            return f"usage: {cmd} <number> or {cmd} on|off"

    def send(text: str) -> None:
        try:
            fs_stdin.write((text + "\n").encode("utf-8"))
            fs_stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    if cmd == "/gain":
        if on_off:
            return f"usage: /gain <number 0–2>"
        send(f"gain {value:.2f}")
        return f"gain → {value:.2f}"
    if cmd == "/reverb":
        if on_off:
            send(f"set synth.reverb.active {value}")
            return f"reverb {'on' if value else 'off'}"
        send(f"set synth.reverb.room-size {value:.2f}")
        return f"reverb room-size → {value:.2f}"
    if cmd == "/chorus":
        if on_off:
            send(f"set synth.chorus.active {value}")
            return f"chorus {'on' if value else 'off'}"
        send(f"set synth.chorus.depth {value:.1f}")
        return f"chorus depth → {value:.1f}"
    return None  # unreachable


def cmd_repl(args) -> int:
    """Interactive REPL: each line of input becomes a song, played to
    completion (or interrupted with Ctrl+C). One FluidSynth lives for
    the whole session — no per-song spawn cost, no re-downloading the
    soundfont, and the optional ``--gui`` window stays open across
    songs so slider positions persist.

    Commands inside the REPL:

    * any plain text                — compose + play that phrase
    * ``/quit`` or empty EOF (Ctrl+D) — end the session
    * ``/seed N``                    — set the seed offset
      (added to the per-phrase hash; same phrase, different seed → new song)
    * ``/help``                     — print this list
    """
    try:
        soundfont = find_soundfont(args.soundfont)
    except SoundfontError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    fs_proc, port_name, spawn_err = _spawn_fluidsynth(
        soundfont, gain=args.gain, reverb=args.reverb,
    )
    if spawn_err is not None:
        print(f"error: {spawn_err}", file=sys.stderr)
        return 1
    assert fs_proc is not None and port_name is not None

    print(
        f"slackbeatz repl — streaming to {port_name!r}. "
        f"Type a phrase + Enter to play. /help, /quit.",
    )

    # Optional --surge: spawn one Surge XT window per pitched channel
    # and mute those channels in FluidSynth so audio comes from Surge
    # XT (with tweakable knobs) instead of FluidSynth's GM patch.
    # Drums (channel 10) keep playing through FluidSynth.
    surge_procs: list[subprocess.Popen] = []
    surge_muted_channels: list[int] = []
    if getattr(args, "surge", False):
        from slackbeatz.synthhost import (
            DEFAULT_SURGE_CHANNELS, channel_routing_summary,
            install_hint, is_surge_installed,
            mute_fluidsynth_channels, spawn_surge_xt,
        )
        if not is_surge_installed():
            print(
                f"--surge requested but Surge XT isn't installed. "
                f"Install with:\n  {install_hint()}\n"
                f"Continuing without Surge XT (FluidSynth still handles audio).",
                file=sys.stderr,
            )
        else:
            print(
                f"\nslackbeatz: spawning {len(DEFAULT_SURGE_CHANNELS)} Surge XT "
                f"windows alongside FluidSynth — one per pitched channel.",
            )
            print(channel_routing_summary())
            for inst, ch_1idx in DEFAULT_SURGE_CHANNELS.items():
                proc = spawn_surge_xt(ch_1idx)
                if proc is not None:
                    surge_procs.append(proc)
                    surge_muted_channels.append(ch_1idx - 1)
            # Once Surge XT windows are up + listening on the FluidSynth
            # port (= the user picks it in MIDI Settings), silence the
            # corresponding FluidSynth channels so we don't double up.
            print(
                f"\nIn each Surge XT window: Settings → MIDI Settings → "
                f"MIDI Input = {port_name!r}, then set MIDI Channel to "
                f"the channel number shown above.",
            )
            mute_fluidsynth_channels(fs_proc.stdin, surge_muted_channels)

    def cleanup_fs() -> None:
        # Restore FluidSynth volumes on the way out so a later run
        # (re-using the cached FluidSynth via, say, IAC Bus) doesn't
        # have silent channels.
        if surge_muted_channels:
            from slackbeatz.synthhost import unmute_fluidsynth_channels
            unmute_fluidsynth_channels(fs_proc.stdin, surge_muted_channels)
        fs_proc.terminate()
        try:
            fs_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            fs_proc.kill()
        # Terminate Surge XT instances so closing slackbeatz cleans up.
        for sp_proc in surge_procs:
            try:
                sp_proc.terminate()
            except Exception:
                pass

    # macOS Tk constraint: NSWindow must be created + driven from the
    # main thread (AppKit raises 'NSWindow should only be instantiated
    # on the main thread!' otherwise). So when --gui is set + Tk is
    # available, the threading flips: REPL input loop on a daemon
    # thread, Tk.mainloop on main. When either side decides to quit,
    # it triggers process exit via _stop_now (which terminates
    # FluidSynth and os._exits — os._exit because sys.exit from a
    # daemon thread would only kill that thread, leaving Tk's mainloop
    # hung in the parent).
    #
    # Probe Tk *before* spawning the REPL daemon thread: if Tk import
    # fails, we fall back to running the REPL on the main thread,
    # which avoids two competing input() loops racing for stdin.
    tk_available = False
    if args.gui:
        try:
            import tkinter  # noqa: F401 — probe-only import
            tk_available = True
            # NOTE: we used to create a tkinter.Tk() here as a thread-
            # safety probe before deciding whether to launch the real
            # GUI. That probe was the cause of an EXC_BREAKPOINT trap
            # in Tk_MacOSXGetTkWindow → objc_opt_respondsToSelector
            # later in the actual mainloop: destroying the probe Tk
            # root left Tcl-internal idle callbacks queued referencing
            # the now-freed NSWindow, and the real mainloop's
            # TclServiceIdle picked them up and crashed.
            #
            # Replaced with a simple import check. If a user's Tcl
            # really is unsafe (Homebrew non-threaded), they'll hit
            # the original Tcl_WaitForEvent abort once playback
            # starts — same outcome they got before the empirical
            # probe existed.
        except ImportError as e:
            py_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
            print(
                f"(gui unavailable — falling back to /gain /reverb /chorus "
                f"REPL commands)\n"
                f"  {e}. On macOS: brew install python-tk@{py_minor}",
                file=sys.stderr,
            )

    if args.gui and tk_available:
        import threading

        from slackbeatz.gui import run_tweak_gui
        from slackbeatz.player import Player

        # Shared Player — REPL slash commands and GUI widgets both
        # mutate the same transport state.
        player = Player(port_name=port_name, setup_arg=args.setup)
        player.seed_offset = args.seed
        if getattr(args, "emit_clock", False):
            player.emit_clock = True

        def _stop_now() -> None:
            player.stop()
            cleanup_fs()
            os._exit(0)

        repl_thread = threading.Thread(
            target=_repl_input_loop,
            args=(fs_proc, port_name, args, _stop_now),
            kwargs={"player": player},
            daemon=True,
        )
        repl_thread.start()
        try:
            run_tweak_gui(
                fs_proc.stdin,
                initial_gain=args.gain,
                initial_reverb_room=args.reverb,
                player=player,
                surge_port_name=port_name if surge_procs else None,
                on_close=_stop_now,
            )
        except RuntimeError as e:
            # GUI refused to launch (e.g. non-threaded Tcl detected
            # at startup). The REPL daemon thread is already running
            # — but with the GUI gone, it ought to live on the main
            # thread instead. Tell the daemon to exit, then run a
            # fresh REPL on main with the same Player instance.
            print(f"\n{e}\n\nFalling through to REPL-only mode.", file=sys.stderr)
            # The daemon REPL might already be blocked in input(); we
            # can't cleanly interrupt it cross-thread. Easiest is to
            # let it co-exist as a no-op (it's daemon, dies when we
            # exit) and run a new input loop on main. But two input()
            # calls racing for stdin is broken, so instead we just
            # exit — the user re-runs with the install-Tk hint.
            cleanup_fs()
            return 1
        except Exception as e:  # noqa: BLE001 — surface unexpected Tk runtime errors
            print(f"(gui error: {e})", file=sys.stderr)
        cleanup_fs()
        return 0

    # Fall-through path: --gui not set, OR Tk import failed. The REPL
    # runs on the main thread (no Tk involved).
    try:
        _repl_input_loop(fs_proc, port_name, args, None)
    finally:
        cleanup_fs()
    return 0


def _repl_input_loop(
    fs_proc: subprocess.Popen,
    port_name: str,
    args,
    on_quit,
    player=None,
) -> None:
    """The REPL's read-eval-play loop. Used directly on the main thread
    in the no-GUI case, or on a daemon thread when ``--gui`` is set
    (because macOS Tk needs main).

    *player* is an optional :class:`slackbeatz.player.Player`. When
    provided, the REPL shares it with the GUI so transport commands
    typed at the prompt affect the same playback the sliders do. When
    omitted (no-GUI plain REPL), a fresh Player is created locally.

    *on_quit* is called when the loop exits normally (user typed
    ``/quit`` or hit EOF). The GUI path uses it to terminate the
    process so the Tk mainloop on the main thread stops too.
    """
    from slackbeatz.player import KNOWN_STYLES, Player

    if player is None:
        player = Player(port_name=port_name, setup_arg=args.setup)
        player.seed_offset = args.seed
        if getattr(args, "emit_clock", False):
            player.emit_clock = True

    # IMPORTANT: this loop is called from the REPL daemon thread when
    # --gui is active. Using ``input()`` here fires CPython's
    # ``PyOS_InputHook`` on the calling thread; with _tkinter loaded,
    # that hook is wired to ``Tcl_DoOneEvent`` to pump Tk events while
    # input is pending. Tcl_DoOneEvent on a non-main thread crashes
    # the process with "Tcl_WaitForEvent: Notifier not initialized"
    # because it bypasses _tkinter's Python-level apartment guard
    # (the hook is a C function pointer, not a Python wrapper).
    #
    # ``sys.stdin.readline()`` doesn't fire the input hook, so we use
    # that instead and write the prompt ourselves. The cost is losing
    # GNU readline's line-editing (history, arrow keys) — acceptable
    # for the GUI mode, and the on-main-thread path below keeps
    # input() so non-GUI users get readline back.
    use_readline = on_quit is None  # main-thread path → safe to use input()
    try:
        while True:
            try:
                if use_readline:
                    line = input("slackbeatz> ").strip()
                else:
                    sys.stdout.write("slackbeatz> ")
                    sys.stdout.flush()
                    raw = sys.stdin.readline()
                    if not raw:
                        raise EOFError
                    line = raw.rstrip("\r\n").strip()
            except EOFError:
                print()  # newline after Ctrl+D
                break
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line == "/help":
                print(
                    "  <phrase>            compose + play that phrase\n"
                    "  /play, /stop        transport\n"
                    "  /status             show current transport state\n"
                    "  /seek BAR[:BEAT]    jump playhead to bar (1-indexed)\n"
                    "  /tempo N | auto     override BPM\n"
                    "  /style NAME | auto  force style\n"
                    f"                       known: {', '.join(KNOWN_STYLES)}\n"
                    "  /seed N             set seed offset\n"
                    "  /reroll             pick a random seed + restart\n"
                    "  /loop on|off        loop the current song on end\n"
                    "  /preserve on|off    keep current bar across param changes\n"
                    "  /reset              clear style/tempo/seed overrides\n"
                    "  /save PATH.sb       export current state to a .sb file\n"
                    "  /knobs              show all knob options (by gen type)\n"
                    "  /knobs gens         list gens in the current song\n"
                    "  /knob H             show knobs + ranges + values for gen H\n"
                    "  /knob H N V         set knob N on gen H to V (e.g. /knob kick humanize 5)\n"
                    "  /knob H N           clear knob N override on gen H\n"
                    "  /knob               show currently-active knob overrides\n"
                    "  /knob-clear H       clear all knob overrides on gen H\n"
                    "  /clock on|off       send MIDI Clock (0xF8 + Start/Stop) for downstream gear\n"
                    "  /mute N             mute channel N (1-16)\n"
                    "  /unmute N | all     unmute channel(s)\n"
                    "  /solo N             toggle solo on channel N (additive)\n"
                    "  /solo off           clear all solos\n"
                    "  /gain N             master gain (0–2; default 0.6)\n"
                    "  /reverb N | on|off  reverb room (0–1) or active toggle\n"
                    "  /chorus N | on|off  chorus depth (0–50) or active toggle\n"
                    "  /quit               end session"
                )
                continue

            # Transport + parameter slash commands.
            transport_result = _handle_transport_command(line, player)
            if transport_result is not None:
                print(f"  {transport_result}")
                continue

            # FluidSynth shell tweaks (gain/reverb/chorus).
            tweak_handled = _handle_tweak_command(line, fs_proc.stdin)
            if tweak_handled is not None:
                print(f"  {tweak_handled}")
                continue

            # Everything else: load the phrase and play.
            player.load_phrase(line)
            try:
                result = player.play()
            except KeyboardInterrupt:
                player.stop()
                print("  (skipped)")
                continue
            print(f"  {result}")
    except KeyboardInterrupt:
        print()  # newline after Ctrl+C at the prompt
        player.stop()

    # Make sure playback is stopped before we exit so notes don't hang
    # on the synth.
    player.stop()
    if on_quit is not None:
        on_quit()


def _handle_transport_command(line: str, player) -> str | None:
    """If *line* is a transport / parameter slash command, dispatch to
    *player* and return a one-line status string. Otherwise return
    ``None`` (caller treats the line as something else)."""
    parts = line.split(maxsplit=1)
    cmd = parts[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/play":
        return player.play()
    if cmd == "/stop":
        return player.stop()
    if cmd == "/status":
        return player.status()
    if cmd == "/reroll":
        return player.reroll_seed()
    if cmd == "/reset":
        return player.reset_overrides()
    if cmd == "/tempo":
        if not arg or arg == "auto":
            return player.set_tempo(None)
        try:
            return player.set_tempo(int(arg))
        except ValueError:
            return "usage: /tempo <integer-bpm> | /tempo auto"
    if cmd == "/style":
        if not arg or arg == "auto":
            return player.set_style(None)
        return player.set_style(arg)
    if cmd == "/seed":
        if not arg:
            return "usage: /seed <integer>"
        try:
            return player.set_seed_offset(int(arg))
        except ValueError:
            return "usage: /seed <integer>"
    if cmd == "/loop":
        if arg.lower() in ("on", "true", "1"):
            return player.set_loop(True)
        if arg.lower() in ("off", "false", "0"):
            return player.set_loop(False)
        return "usage: /loop on|off"
    if cmd == "/seek":
        # /seek <bar>           — bar N, beat 0
        # /seek <bar>:<beat>    — bar N, fractional beat
        if not arg:
            return "usage: /seek <bar> | /seek <bar>:<beat>"
        if ":" in arg:
            bar_s, beat_s = arg.split(":", 1)
            try:
                bar = int(bar_s)
                beat = float(beat_s)
            except ValueError:
                return "usage: /seek <bar> | /seek <bar>:<beat>"
        else:
            try:
                bar = int(arg)
                beat = 0.0
            except ValueError:
                return "usage: /seek <bar> | /seek <bar>:<beat>"
        return player.seek(bar=bar, beat=beat)
    if cmd == "/mute":
        if not arg:
            return f"muted channels: {sorted(player.muted_channels) or 'none'}"
        try:
            return player.mute(int(arg))
        except ValueError:
            return "usage: /mute <1-16>"
    if cmd == "/unmute":
        if not arg or arg.lower() == "all":
            return player.unsolo()
        try:
            return player.unmute(int(arg))
        except ValueError:
            return "usage: /unmute <1-16> | /unmute all"
    if cmd == "/solo":
        if not arg or arg.lower() == "off":
            return player.unsolo()
        try:
            # /solo N toggles — if already solo'd, remove from set;
            # otherwise add. DAW convention: clicking a solo button
            # that's already lit unlights it.
            return player.toggle_solo(int(arg))
        except ValueError:
            return "usage: /solo <1-16> | /solo off"
    if cmd == "/preserve":
        if arg.lower() in ("on", "true", "1"):
            return player.set_preserve_position(True)
        if arg.lower() in ("off", "false", "0"):
            return player.set_preserve_position(False)
        return "usage: /preserve on|off"
    if cmd == "/save":
        if not arg:
            return "usage: /save <path.sb>"
        return player.save_state(arg)
    if cmd == "/knob":
        # /knob HANDLE NAME VALUE     — set
        # /knob HANDLE NAME           — clear that knob's override
        # /knob HANDLE                — show that gen's available knobs
        #                               + ranges + current values
        # /knob (no args)             — list active overrides
        bits = arg.split() if arg else []
        if not bits:
            return _knob_list_overrides(player)
        if len(bits) == 1:
            return _knob_show_gen(player, bits[0])
        if len(bits) == 2:
            return player.unset_knob(bits[0], bits[1])
        if len(bits) == 3:
            return player.set_knob(bits[0], bits[1], bits[2])
        return "usage: /knob HANDLE NAME VALUE | /knob HANDLE | /knob HANDLE NAME"
    if cmd == "/knobs":
        # /knobs        — table of every knob slackbeatz exposes,
        #                 organised by gen type
        # /knobs HANDLE — same as /knob HANDLE
        # /knobs gens   — list the gens in the currently-loaded song
        if not arg:
            return _knob_list_all_specs()
        if arg == "gens":
            return _knob_list_song_gens(player)
        return _knob_show_gen(player, arg)
    if cmd == "/knob-clear":
        # Explicit "clear all overrides on this gen" — split out from
        # /knob HANDLE because that now shows info instead.
        if not arg:
            return "usage: /knob-clear HANDLE"
        return player.unset_knob(arg)
    if cmd == "/clock":
        if arg.lower() in ("on", "true", "1"):
            return player.set_emit_clock(True)
        if arg.lower() in ("off", "false", "0"):
            return player.set_emit_clock(False)
        return "usage: /clock on|off"
    return None


def cmd_export(args) -> int:
    """Export a song as a stems bundle: MIDI + per-channel WAVs +
    README + manifest. Drop into a folder or zip and drag into any
    DAW that accepts audio + MIDI tracks (Ableton, Bitwig, Logic,
    Reaper, Studio One, Cubase, FL, …)."""
    from slackbeatz.export import export_bundle
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
        soundfont = find_soundfont(args.soundfont) if args.soundfont else find_soundfont(None)
    except SoundfontError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    output_path = Path(args.output)
    print(f"slackbeatz export → {output_path}")
    # Count distinct channels (= one stem each) rather than gens —
    # multiple drum gens share channel 10, bass + drone share ch 2, etc.
    n_channels = len({
        gen.instrument.channel for gen in resolved.gens.values()
        if gen.instrument is not None
    } | {
        gen.kit.channel for gen in resolved.gens.values()
        if gen.kit is not None
    })
    print(f"  rendering {n_channels} stem(s) (one FluidSynth call each — takes a few seconds)...")
    try:
        export_bundle(resolved, output_path, soundfont=soundfont,
                      sample_rate=args.sample_rate)
    except MissingToolError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"error: subprocess failed (exit {e.returncode}): {e.cmd[0]}",
              file=sys.stderr)
        return 1
    print(f"wrote {output_path}")
    return 0


def cmd_render(args) -> int:
    """Render a song to a Standard MIDI File. Drag the resulting .mid
    into Ableton / Logic / Reaper / etc — each MIDI channel lands on
    its own track, with the GM program_change events intact so the
    DAW picks the right instruments out of the box."""
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
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_midifile(resolved, output_path)
    print(f"wrote {output_path}")
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
    sp = sub.add_parser("render", help="render a song to a .mid file")
    sp.add_argument("song_file")
    sp.add_argument("--setup")
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("-o", "--output", required=True, help="output .mid path")
    sp.set_defaults(func=cmd_render)

    # export — stems bundle (MIDI + per-channel WAVs + README + manifest)
    sp = sub.add_parser(
        "export",
        help="export a song as a stems bundle (MIDI + per-channel WAVs)",
    )
    sp.add_argument("song_file")
    sp.add_argument("--setup")
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument(
        "-o", "--output", required=True,
        help="output path — folder if no .zip suffix, zip file if .zip",
    )
    sp.add_argument("--soundfont", help="path to .sf2/.sf3 for stem rendering")
    sp.add_argument(
        "--sample-rate", type=int, default=44100,
        help="WAV sample rate (default 44100)",
    )
    sp.set_defaults(func=cmd_export)

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
    sp.add_argument(
        "--gui", action="store_true",
        help="open a Tk tweak window with sliders for gain / reverb / chorus",
    )
    sp.add_argument(
        "--emit-clock", action="store_true",
        help="broadcast MIDI Clock (0xF8 + Start/Stop) so external gear can sync",
    )
    sp.add_argument(
        "--surge", action="store_true",
        help="spawn one Surge XT window per pitched channel for live sound design; "
             "FluidSynth mutes those channels and continues to handle drums",
    )
    sp.set_defaults(func=cmd_live)

    # repl — interactive text → audio loop
    sp = sub.add_parser(
        "repl",
        help="interactive REPL: type a phrase, hear a song, repeat",
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
        help="seed offset (added to the per-phrase hash; default 0)",
    )
    sp.add_argument(
        "--gui", action="store_true",
        help="also open the live tweak window in the background",
    )
    sp.add_argument(
        "--emit-clock", action="store_true",
        help="broadcast MIDI Clock (0xF8 + Start/Stop) so external gear can sync",
    )
    sp.add_argument(
        "--surge", action="store_true",
        help="spawn one Surge XT window per pitched channel for live sound design; "
             "FluidSynth mutes those channels and continues to handle drums",
    )
    sp.set_defaults(func=cmd_repl)

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
