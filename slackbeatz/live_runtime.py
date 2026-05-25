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
    """

    player: object  # forward-declared (slackbeatz.player.Player)
    setup: object  # Setup
    backend: str
    fs_proc: Optional[subprocess.Popen] = None
    surge_instances: list = field(default_factory=list)
    sampler: object | None = None
    _down: bool = False

    def shutdown(self) -> None:
        if self._down:
            return
        self._down = True
        try:
            self.player.stop()
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
) -> tuple[subprocess.Popen, str]:
    """Spawn a CoreAudio + CoreMIDI FluidSynth and return its MIDI port.

    Raises :class:`LiveRuntimeError` if the soundfont can't be found
    or FluidSynth doesn't expose a port within the wait window.

    Extracted from ``cli._spawn_fluidsynth`` so the GUI's live
    bootstrap can share it. Identical wait + diff-the-port-list
    technique — FluidSynth doesn't name its CoreMIDI port up front
    so we snapshot before/after and take the new entry.
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

    backend = setup.backend
    osc_routing_enabled = backend == "surge"
    need_fluidsynth = osc_routing_enabled and _setup_has_drum_channel(setup)

    fs_proc: Optional[subprocess.Popen] = None
    port_name: Optional[str] = None

    if need_fluidsynth:
        on_progress("slackbeatz: spawning FluidSynth for ch10 drums…")
        fs_proc, port_name = _spawn_fluidsynth_port()
        on_progress(f"  ch10 → FluidSynth on {port_name!r}")
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
    player.load_file(song_path)

    runtime = LiveRuntime(
        player=player,
        setup=setup,
        backend=backend,
        fs_proc=fs_proc,
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
            on_progress("slackbeatz: spawning headless surge-xt-cli…")
            runtime.surge_instances = spawn_surge_instances(
                on_progress=on_progress,
            )
        # Sampler enables the voice / fx channels regardless of
        # whether surge-xt-cli is up.
        from slackbeatz.cli import _start_sampler_if_enabled
        runtime.sampler = _start_sampler_if_enabled(osc_routing_enabled)

    return runtime
