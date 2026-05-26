"""Shared live-playback bootstrap — spawns FluidSynth + Surge XT.

Both the CLI (``slackbeatz live``) and the GUI (``slackbeatz``
Welcome → Arrangement Play button) need the same setup-aware
spawn chain to make audio:

* ``backend surge`` setups — spawn one ``surge-xt-cli`` subprocess
  per pitched channel (via :mod:`slackbeatz.surge_host`), plus a
  ``FluidSynth`` subprocess for the ch10 drums, plus the optional
  in-process sampler for voice / fx channels.
* Other (GM-style) setups — open a single MIDI output port that
  the user has wired to a synth externally.

Pre-redesign this whole chain only existed inline in
:mod:`slackbeatz.cli`. The new GUI (:mod:`slackbeatz.ui.launcher`)
needs the same logic so its Play button actually makes sound —
this module is the extracted, reusable form.

Usage:

.. code-block:: python

    runtime = build_live_runtime(song_path, setup_arg="surge")
    runtime.player.play()
    # ... later
    runtime.shutdown()  # idempotent — stops everything cleanly
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from slackbeatz.dsl.parser import parse_file, ParseError
from slackbeatz.setup.loader import SetupError
from slackbeatz.setup.resolve import resolve_song, ResolveError
from slackbeatz.sinks.realtime import available_ports
from slackbeatz.audio import SoundfontError, find_soundfont


class LiveRuntimeError(RuntimeError):
    """Raised when the live runtime can't bring up the audio chain."""


@dataclass
class LiveRuntime:
    """Owned process / port resources for one live playback session.

    Always construct via :func:`build_live_runtime`; :meth:`shutdown`
    is idempotent + safe to call from any thread.

    When ``_transferred`` is True, :meth:`shutdown` short-circuits —
    used by :func:`build_live_runtime` to mark a runtime whose
    surge_instances + fs_proc + sampler + MultiPortSink have been
    handed off to a successor runtime (song-switch reuse path).
    """

    player: object  # forward-declared (slackbeatz.player.Player)
    setup: object  # Setup
    backend: str
    fs_proc: Optional[subprocess.Popen] = None
    surge_instances: list = field(default_factory=list)
    sampler: object | None = None
    transport_listener: object | None = None  # TransportListener in ableton mode
    _down: bool = False
    _transferred: bool = False

    def shutdown(self) -> None:
        if self._down:
            return
        self._down = True
        # Always stop the player thread — it's per-runtime and never
        # transferred. The surge/fluidsynth/sampler children may be
        # owned by a successor runtime via _transferred, in which case
        # we leave them alone.
        try:
            self.player.stop()
        except Exception:
            pass
        if self._transferred:
            return
        if self.transport_listener is not None:
            try:
                self.transport_listener.stop()
            except Exception:
                pass
        for inst in self.surge_instances:
            try:
                inst.shutdown()
            except Exception:
                pass
        if self.sampler is not None:
            try:
                self.sampler.stop()
            except Exception:
                pass
        if self.fs_proc is not None:
            try:
                self.fs_proc.terminate()
                try:
                    self.fs_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.fs_proc.kill()
            except Exception:
                pass


def _setup_has_drum_channel(setup) -> bool:
    if any(i.channel == 10 for i in setup.instruments.values()):
        return True
    if any(k.channel == 10 for k in setup.kits.values()):
        return True
    return False


def _spawn_fluidsynth_port(
    *, gain: float = 0.6, reverb: float = 0.8,
    audio_device: Optional[str] = None,
) -> tuple[subprocess.Popen, str]:
    """Spawn a CoreAudio + CoreMIDI FluidSynth and return its MIDI port.

    Raises :class:`LiveRuntimeError` if the soundfont can't be found
    or FluidSynth doesn't expose a port within the wait window.

    Extracted from ``cli._spawn_fluidsynth`` so the GUI's live
    bootstrap can share it. Identical wait + diff-the-port-list
    technique — FluidSynth doesn't name its CoreMIDI port up front
    so we snapshot before/after and take the new entry.

    *audio_device* selects the CoreAudio output device. ``None`` uses
    the system default (today's behaviour, matches surge-standalone
    mode). Pass ``"BlackHole 16ch"`` in ableton-blackhole mode so the
    drum bus lands on BlackHole channels 1/2 — which Ableton's Audio
    track 1 reads from to apply DAW FX.
    """
    import time
    from slackbeatz.audio import MissingToolError, require_tool

    try:
        soundfont = find_soundfont(None)
    except SoundfontError as e:
        raise LiveRuntimeError(str(e)) from e
    try:
        fluidsynth_bin = require_tool("fluidsynth")
    except MissingToolError as e:
        raise LiveRuntimeError(str(e)) from e

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
            raise LiveRuntimeError(msg)
        diff = set(available_ports()) - before_ports
        if diff:
            return proc, next(iter(diff))

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
    raise LiveRuntimeError(
        "fluidsynth started but didn't expose a MIDI port"
    )


def build_live_runtime(
    song_path: Path,
    *,
    setup_arg: Optional[str] = None,
    seed_offset: int = 0,
    on_progress: Callable[[str], None] = print,
    reuse_from: Optional[LiveRuntime] = None,
) -> LiveRuntime:
    """Resolve *song_path*, spawn the backend processes, return a runtime.

    Honors the song's (or *setup_arg*'s) backend:

    * ``backend surge`` — spawns FluidSynth on a fresh CoreMIDI
      port (for ch10 drums) and one ``surge-xt-cli`` subprocess
      per pitched channel. ``osc_routing=True`` on the Player so
      the scheduler routes pitched channels to the per-Surge
      virtual ports.
    * other backends — picks the first available MIDI output port
      (typically IAC on macOS once enabled). When none exist,
      tries to create a virtual ``slackbeatz`` port via rtmidi.

    Raises :class:`LiveRuntimeError` with a human-readable message
    when the chain can't be brought up (e.g. soundfont missing).
    """
    # Resolve to get the setup — we need the backend before deciding
    # how to spawn anything.
    file_ast = parse_file(song_path)
    if file_ast.song is None:
        raise LiveRuntimeError(f"{song_path}: no song block found")
    try:
        from slackbeatz.cli import _load_setup_for_song
        setup = _load_setup_for_song(song_path, file_ast, setup_arg)
        # Touch resolve_song so errors surface here, not at first Play.
        resolve_song(file_ast.song, setup, cli_seed=0)
    except (ParseError, ResolveError, SetupError) as e:
        raise LiveRuntimeError(str(e)) from e

    mode = setup.mode
    backend = setup.backend  # derived: "external" or "surge"
    osc_routing_enabled = backend == "surge"
    # FluidSynth spawns for ch10 drums only in surge-standalone mode.
    # In ableton-blackhole, drums emit on the slackbeatz-drums virtual
    # MIDI port for an Ableton Drum Rack to subscribe to (the user
    # picks the kit, not us).
    need_fluidsynth = (
        osc_routing_enabled
        and mode != "ableton-blackhole"
        and _setup_has_drum_channel(setup)
    )
    fluidsynth_device = None  # FluidSynth never targets BlackHole now

    # Reuse path — skip the FluidSynth + surge-xt-cli spawn and
    # transfer the previous runtime's children. Triggered when the
    # caller hands us a compatible ``reuse_from`` (same setup name +
    # same mode) — typical when the user opens a different .sb
    # file with the same bundled setup. Saves ~5s of surge-xt-cli boot
    # per song switch. Reuse keys on mode (not backend) so a switch
    # between surge-standalone and ableton-blackhole respawns surge
    # with the new audio routing flags.
    if (
        reuse_from is not None
        and not reuse_from._transferred
        and not reuse_from._down
        and getattr(reuse_from.setup, "name", None) == getattr(setup, "name", None)
        and getattr(reuse_from.setup, "mode", None) == mode
    ):
        on_progress(
            f"slackbeatz: reusing existing surge instances "
            f"(setup={setup.name!r}, {len(reuse_from.surge_instances)} surge + "
            f"{'fluidsynth' if reuse_from.fs_proc else 'no-fluidsynth'} + "
            f"{'sampler' if reuse_from.sampler else 'no-sampler'})"
        )
        from slackbeatz.player import Player

        prev_player = reuse_from.player
        prev_port = getattr(prev_player, "port_name", None)
        prev_sink = getattr(prev_player, "_shared_routing_sink", None)
        # Stop the OLD player's worker thread (audio dispatch). The
        # surge / fluidsynth / sampler PROCESSES keep running because
        # _transferred=True short-circuits their shutdown.
        try:
            prev_player.stop()
        except Exception:
            pass
        reuse_from._transferred = True

        player = Player(
            port_name=prev_port,
            setup_arg=setup_arg,
            osc_routing=osc_routing_enabled,
        )
        player.seed_offset = seed_offset
        player.mode = mode  # mode-aware routing must match the reuse setup
        # Inherit the shared MultiPortSink so the new Player writes to
        # the SAME virtual MIDI ports the existing Surge instances are
        # subscribed to. ensure_osc_routing_ready short-circuits when
        # this is already set.
        if prev_sink is not None:
            player._shared_routing_sink = prev_sink
        # Carry the transport plumbing across the song-switch reuse so
        # Ableton stays bound to the same virtual ports + the existing
        # TransportListener thread keeps running.
        if reuse_from.transport_listener is not None:
            player.transport_port_name = "slackbeatz-transport-out"
            player.transport_listener = reuse_from.transport_listener
            player.emit_clock = True
        player.load_file(song_path)

        return LiveRuntime(
            player=player,
            setup=setup,
            backend=backend,
            fs_proc=reuse_from.fs_proc,
            surge_instances=list(reuse_from.surge_instances),
            sampler=reuse_from.sampler,
            transport_listener=reuse_from.transport_listener,
        )

    fs_proc: Optional[subprocess.Popen] = None
    port_name: Optional[str] = None

    if need_fluidsynth:
        on_progress("slackbeatz: spawning FluidSynth for ch10 drums…")
        fs_proc, port_name = _spawn_fluidsynth_port(audio_device=fluidsynth_device)
        device_note = f" → {fluidsynth_device} ch 1/2" if fluidsynth_device else ""
        on_progress(f"  ch10 → FluidSynth on {port_name!r}{device_note}")
    else:
        # Non-surge backend, or surge with no drums. Use first
        # available MIDI port — or create a virtual one.
        ports = available_ports()
        if ports:
            port_name = ports[0]
        else:
            # No port and no IAC — try to create a virtual one. On
            # macOS / Linux this is supported via rtmidi's
            # virtual=True flag. On Windows it'll raise; the
            # caller surfaces the error.
            port_name = "slackbeatz"
            try:
                import mido
                _probe = mido.open_output(port_name, virtual=True)
                _probe.close()
            except Exception as e:
                raise LiveRuntimeError(
                    "no MIDI output ports available and couldn't create "
                    f"a virtual one ({e}). On macOS enable the IAC Driver "
                    "in Audio MIDI Setup → MIDI Studio, or use a setup "
                    "with backend=surge to spawn an in-process synth."
                ) from e

    from slackbeatz.player import Player

    player = Player(
        port_name=port_name,
        setup_arg=setup_arg,
        osc_routing=osc_routing_enabled,
    )
    player.seed_offset = seed_offset
    # Mode drives mode-aware routing decisions inside Player
    # (e.g. ch10 drums → MIDI in ableton-blackhole vs FluidSynth in
    # surge-standalone). Must be set BEFORE load_file so the first
    # render uses the right channel routing.
    player.mode = mode
    player.load_file(song_path)

    # Bidirectional MIDI transport in ableton-blackhole mode. SB stays
    # the clock master (emit_clock=True → ClockEmitter on
    # slackbeatz-transport-out); a TransportListener on
    # slackbeatz-transport-in lets Ableton drive Start/Stop/Continue/SPP.
    # Echo suppression is wired via Player.transport_listener so the
    # emitter notes outbound events before the listener sees them
    # reflected. Configure Ableton: Live → Settings → Link/MIDI →
    # MIDI input: Sync = On for slackbeatz-transport-out; MIDI output:
    # Sync = On for slackbeatz-transport-in.
    transport_listener = None
    if mode == "ableton-blackhole":
        from slackbeatz.sinks.transport_in import TransportListener

        def _on_play(from_tick: int) -> None:
            try:
                player.play(from_tick=from_tick)
            except Exception:
                pass

        def _on_stop() -> None:
            try:
                player.stop()
            except Exception:
                pass

        def _on_seek(tick: int) -> None:
            if player.is_playing:
                try:
                    player.seek_to_tick(tick)
                except Exception:
                    pass

        transport_listener = TransportListener(
            on_play=_on_play, on_stop=_on_stop, on_seek=_on_seek,
        )
        transport_listener.start()
        player.transport_port_name = "slackbeatz-transport-out"
        player.transport_listener = transport_listener
        player.emit_clock = True
        on_progress(
            "slackbeatz: transport sync wired (Ableton: Sync IN from "
            "slackbeatz-transport-out, Sync OUT to slackbeatz-transport-in)"
        )

    runtime = LiveRuntime(
        player=player,
        setup=setup,
        backend=backend,
        fs_proc=fs_proc,
        transport_listener=transport_listener,
    )

    if osc_routing_enabled:
        player.ensure_osc_routing_ready()
        from slackbeatz.surge_host import (
            install_hint, is_surge_cli_installed, spawn_surge_instances,
        )
        if not is_surge_cli_installed():
            on_progress(
                f"warning: setup {setup.name!r} wants backend=surge but "
                f"surge-xt-cli isn't installed.\n  install with: "
                f"{install_hint()}\n  continuing without Surge XT — "
                f"pitched channels will be silent."
            )
        else:
            on_progress(
                f"slackbeatz: spawning headless surge-xt-cli (mode={mode})…"
            )
            runtime.surge_instances = spawn_surge_instances(
                mode=mode,
                on_progress=on_progress,
            )
        # Sampler enables the voice / fx channels regardless of
        # whether surge-xt-cli is up.
        from slackbeatz.cli import _start_sampler_if_enabled
        runtime.sampler = _start_sampler_if_enabled(osc_routing_enabled)

    return runtime
