"""Command-line interface for slackbeatz."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

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
    """Stream a song to a MIDI sink.

    Backend dispatch is driven by the resolved setup's ``backend`` field
    — no per-flag opt-in. ``backend external`` (the default for
    pre-redesign setups like ``gm`` / ``808`` / ``multitimbral``) sends
    bare MIDI to an external port. ``backend surge`` (the bundled
    ``surge`` setup) spawns headless surge-xt-cli for pitched channels
    and uses FluidSynth for ch10 drums. ``--setup NAME`` overrides the
    song's embedded setup, so the same .sb can play through any rig.
    """
    # Resolve source: explicit .sb file or compose from text.
    cleanup_song = False
    text_arg = getattr(args, "text", None)
    if text_arg:
        sb_content = compose_from_text(text_arg)
        with tempfile.NamedTemporaryFile(
            suffix=".sb", delete=False, mode="w", encoding="utf-8",
        ) as tf:
            tf.write(sb_content)
            song_path = Path(tf.name)
        cleanup_song = True
    elif args.song_file:
        song_path = Path(args.song_file)
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
        if cleanup_song:
            song_path.unlink(missing_ok=True)
        return 2

    mode = setup.mode
    backend = setup.backend  # derived: "external" or "surge"
    osc_routing_enabled = backend == "surge"
    # FluidSynth still spawns for ch10 drums under any surge-* mode.
    need_fluidsynth = osc_routing_enabled and _setup_has_drum_channel(setup)
    # In ableton-blackhole, FluidSynth's stereo output goes to BlackHole
    # 1/2 so Ableton's drum track receives it; otherwise OS default.
    fluidsynth_device = "BlackHole 16ch" if mode == "ableton-blackhole" else None

    fs_proc = None
    port_name: Optional[str] = None
    if need_fluidsynth:
        try:
            soundfont = find_soundfont(None)
        except SoundfontError as e:
            print(f"error: {e}", file=sys.stderr)
            if cleanup_song:
                song_path.unlink(missing_ok=True)
            return 1
        try:
            fs_proc, new_port, spawn_err = _spawn_fluidsynth(
                soundfont, gain=0.6, reverb=0.8,
                audio_device=fluidsynth_device,
            )
        except MissingToolError as e:
            print(f"error: {e}", file=sys.stderr)
            if cleanup_song:
                song_path.unlink(missing_ok=True)
            return 1
        if spawn_err is not None:
            print(f"error: {spawn_err}", file=sys.stderr)
            if cleanup_song:
                song_path.unlink(missing_ok=True)
            return 1
        assert fs_proc is not None and new_port is not None
        port_name = new_port
        print(
            f"slackbeatz: routing ch10 drums through FluidSynth on "
            f"{port_name!r} (surge backend) — press Ctrl+C to stop",
        )
    else:
        port_name = args.port
        if port_name is None:
            ports = available_ports()
            if not ports:
                print(
                    "error: no MIDI output ports available. On macOS, "
                    "enable the IAC Driver in Audio MIDI Setup → MIDI "
                    "Studio. Or use a setup with `backend surge` to "
                    "spawn an in-process synth.",
                    file=sys.stderr,
                )
                if cleanup_song:
                    song_path.unlink(missing_ok=True)
                return 1
            port_name = ports[0]
        print(
            f"slackbeatz: streaming MIDI to {port_name!r} "
            f"(setup={setup.name!r}, backend={backend}).",
        )

    from slackbeatz.player import Player
    player = Player(
        port_name=port_name,
        setup_arg=args.setup,
        osc_routing=osc_routing_enabled,
    )
    player.seed_offset = args.seed
    if getattr(args, "emit_clock", False):
        player.emit_clock = True
    player.load_file(song_path)

    surge_instances: list = []
    sampler = None

    if osc_routing_enabled:
        player.ensure_osc_routing_ready()
        from slackbeatz.surge_host import (
            install_hint, is_surge_cli_installed, spawn_surge_instances,
        )
        if not is_surge_cli_installed():
            print(
                f"setup {setup.name!r} requested backend=surge but "
                f"surge-xt-cli isn't installed. Install with:\n  "
                f"{install_hint()}\nContinuing without Surge XT.",
                file=sys.stderr,
            )
        else:
            print(
                f"\nslackbeatz: spawning headless surge-xt-cli instances "
                f"(mode={mode}):"
            )
            surge_instances = spawn_surge_instances(mode=mode, on_progress=print)
        sampler = _start_sampler_if_enabled(osc_routing_enabled)

    player.play()

    try:
        if getattr(args, "gui", False):
            import threading

            from slackbeatz.gui import run_tweak_gui

            stop_event = threading.Event()

            def _on_close():
                player.stop()
                stop_event.set()

            run_tweak_gui(
                fs_proc.stdin if fs_proc is not None else None,
                initial_gain=0.6,
                initial_reverb_room=0.8,
                initial_programs=_program_map(resolved),
                player=player,
                show_surge_gui_routing_hint=False,
                surge_instances=surge_instances or None,
                on_close=_on_close,
            )
        else:
            while player.is_playing:
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nslackbeatz: interrupted")
        player.stop()
    except NoMidiPortError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            player.stop()
        except Exception:
            pass
        for inst in surge_instances:
            try:
                inst.shutdown()
            except Exception:
                pass
        if sampler is not None:
            try:
                sampler.stop()
            except Exception:
                pass
        if fs_proc is not None:
            fs_proc.terminate()
            try:
                fs_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                fs_proc.kill()
        if cleanup_song:
            song_path.unlink(missing_ok=True)
    return 0


def _setup_has_drum_channel(setup: Setup) -> bool:
    """True if the setup has any channel-10 (drum) routing.

    Drums on ch10 still go through FluidSynth in the surge backend; if
    the active setup doesn't define ch10 voices at all, we skip the
    FluidSynth spawn and run Surge-only.
    """
    if any(i.channel == 10 for i in setup.instruments.values()):
        return True
    if any(k.channel == 10 for k in setup.kits.values()):
        return True
    return False


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

    output_path = Path(args.output)

    # Backend dispatch is driven by the resolved setup's backend field.
    # `backend surge` → render pitched / sub via Surge XT VST3 (offline,
    # deterministic), voice / fx via the sampler bank, drums via
    # FluidSynth. `backend external` → lean FluidSynth-only path
    # (matches the pre-redesign default for the bundled `gm` setup
    # family).
    if setup.backend == "surge":
        from slackbeatz.audio_offline import (
            OfflineRenderError, render_song_with_surge,
        )
        try:
            soundfont = find_soundfont(args.soundfont)
        except SoundfontError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        try:
            render_song_with_surge(
                resolved,
                output_path,
                soundfont=soundfont,
                sample_rate=args.sample_rate,
                bitrate=args.bitrate,
                progress=print,
            )
        except OfflineRenderError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as e:
            print(
                f"error: subprocess failed (exit {e.returncode}): {e.cmd[0]}",
                file=sys.stderr,
            )
            return 1
        size_kb = output_path.stat().st_size // 1024
        print(f"wrote {output_path} ({size_kb} KB)")
        return 0

    try:
        soundfont = find_soundfont(args.soundfont)
    except SoundfontError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

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


def _spawn_fluidsynth(
    soundfont: Path, *,
    gain: float, reverb: float,
    audio_device: Optional[str] = None,
) -> tuple[subprocess.Popen[bytes] | None, str | None, str | None]:
    """Spawn a CoreAudio + CoreMIDI FluidSynth and wait for its MIDI port.

    Returns ``(proc, port_name, None)`` on success, or
    ``(None, None, error_message)`` on failure. Used by ``cmd_live`` to
    bring up a FluidSynth backend on demand for ch10 drums under any
    surge-* mode.

    *audio_device* selects the CoreAudio output device by name (e.g.
    ``"BlackHole 16ch"`` under ableton-blackhole mode). ``None`` uses
    the system default.
    """
    fluidsynth_bin = require_tool("fluidsynth")
    before_ports = set(available_ports())
    fs_args = [
        fluidsynth_bin,
        "-a", "coreaudio",
        "-m", "coremidi",
        "-o", f"synth.gain={gain}",
        "-o", f"synth.reverb.room-size={reverb}",
        "-o", "synth.chorus.active=1",
    ]
    if audio_device:
        fs_args += ["-o", f"audio.coreaudio.device={audio_device}"]
    fs_args += ["-q", str(soundfont)]
    proc = subprocess.Popen(
        fs_args,
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


def _start_sampler_if_enabled(osc_routing_enabled: bool):
    """If per-channel routing is on (i.e. ``--surge`` / ``--surge-gui``
    was set, so :class:`MultiPortSink` is open and the virtual ports
    ``slackbeatz-voice`` + ``slackbeatz-fx`` exist), construct +
    start the in-process :class:`Sampler` listening on those ports.
    Returns ``None`` when routing is off (= no sampler needed).

    Sampler banks start empty; the speech / sample gens populate them
    at resolve time. A missing ``sounddevice`` / ``soundfile`` package
    is logged but doesn't abort playback — the rest of slackbeatz
    keeps working without the sampler in that case.
    """
    if not osc_routing_enabled:
        return None
    from slackbeatz.sampler import Sampler, set_active_sampler
    from slackbeatz.synthhost import OSC_CHANNELS, sampler_port_banks
    try:
        sampler = Sampler(sampler_port_banks())
        sampler.start()
    except RuntimeError as e:
        # sounddevice / soundfile / numpy not installed.
        print(f"slackbeatz sampler disabled: {e}", file=sys.stderr)
        return None
    set_active_sampler(sampler)
    # Pre-arm the per-port FX chains so the Mixer-tab voice + fx strips
    # have a live Pedalboard to mutate. Silent no-op if pedalboard
    # isn't installed (TTS extra not selected); the strips then render
    # without FX controls and emit a one-line stderr hint.
    for role in ("voice", "fx"):
        sampler.enable_fx(OSC_CHANNELS[role][1])
    return sampler


def _build_live_sink(fluidsynth_port: str, osc_routing: bool):
    """Build the sink for cmd_live playback.

    Plain :class:`RealtimeSink` to FluidSynth when ``--surge`` is off;
    a :class:`CompositeSink` that routes channels 1-4 onto dedicated
    virtual ports for Surge XT (and keeps drums + everything else on
    FluidSynth) when on.
    """
    fs_sink = RealtimeSink(port_name=fluidsynth_port)
    if not osc_routing:
        return fs_sink
    from slackbeatz.sinks.composite import CompositeSink
    from slackbeatz.sinks.multiport import MultiPortSink
    from slackbeatz.synthhost import OSC_CHANNELS
    ch_to_port = {
        ch_1idx - 1: port
        for (ch_1idx, port, _patch) in OSC_CHANNELS.values()
    }
    multi = MultiPortSink(ch_to_port)
    overrides = {ch: multi for ch in ch_to_port}
    return CompositeSink(default=fs_sink, channel_overrides=overrides)


# Thin alias kept for backward compat — the canonical helper lives in
# engine.midifile so the GUI's Instruments-tab state-change callback
# can call it without importing from cli (which would risk circular
# import once the GUI hooks player.on_state_change).
def _program_map(resolved) -> dict[int, int]:
    from slackbeatz.engine.midifile import program_map
    return program_map(resolved)


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
    # ``required=False`` so bare ``slackbeatz`` (no subcommand) launches
    # the GUI. ``main()`` checks for that case before dispatching.
    sub = p.add_subparsers(dest="cmd", required=False)

    # play — the unified live/playback subcommand. Backend (Surge vs
    # bare-MIDI-to-external) is selected by the active setup's
    # `backend` directive, not by a flag.
    sp = sub.add_parser(
        "play",
        help="stream a song (live) — backend chosen by the setup's "
             "`backend` directive (surge or external)",
    )
    sp.add_argument(
        "song_file", nargs="?",
        help="path to a .sb file (omit if using --text)",
    )
    sp.add_argument(
        "--text",
        help="compose from text and play directly — alternative to song_file",
    )
    sp.add_argument(
        "--setup",
        help="override the song's embedded setup (e.g. 'surge', 'external', "
             "'multitimbral'); also picks the render backend",
    )
    sp.add_argument(
        "--port",
        help="MIDI output port for external-backend playback "
             "(default: first available). Ignored under backend=surge — "
             "Surge spawns its own virtual ports.",
    )
    sp.add_argument("--seed", type=int, default=0,
                    help="fallback seed when the song doesn't set one (default 0)")
    sp.add_argument("--clock", choices=("internal", "external"), default="internal",
                    help="clock mode (default internal; external is v2)")
    sp.add_argument("--clock-port",
                    help="MIDI input port for external clock (required if --clock external)")
    sp.add_argument("--emit-clock", action="store_true",
                    help="emit MIDI Clock 0xF8 ticks downstream (internal clock only)")
    sp.add_argument("--gui", action="store_true",
                    help="open the Tk control window alongside playback")
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

    # audio — offline render. Backend (Surge VST3 vs FluidSynth) is
    # selected by the active setup's `backend` directive.
    sp = sub.add_parser(
        "audio",
        help="render a song to a .wav or .mp3 audio file "
             "(backend chosen by the setup's `backend` directive)",
    )
    sp.add_argument("song_file")
    sp.add_argument(
        "--setup",
        help="override the song's embedded setup; picks the render backend",
    )
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument(
        "--soundfont",
        help="path to a .sf2/.sf3 file (default: auto-discover or download); "
             "used by FluidSynth offline render and the Surge backend's "
             "ch10 drum stem",
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


def cmd_gui(_args) -> int:
    """Launch the redesigned GUI — Welcome screen.

    Invoked when ``slackbeatz`` is called with no subcommand. The
    Welcome screen offers "+ New from title" / "Open .sb…" / Recents.
    Hands off to ``slackbeatz.ui.launcher.launch`` which owns the Tk
    root and screen transitions (Welcome → Arrangement → Mixer /
    Setup).

    The old :mod:`slackbeatz.gui` notebook GUI still exists and is
    the fallback for ``slackbeatz play --gui`` (live playback with
    knob-twiddling Tk window). It is being retired — see redesign
    plan Phase E.
    """
    from slackbeatz.ui.launcher import launch
    return launch()


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # Bare ``slackbeatz`` (no subcommand) → launch the GUI Welcome screen.
    if getattr(args, "cmd", None) is None:
        raise SystemExit(cmd_gui(args))
    raise SystemExit(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    main()
