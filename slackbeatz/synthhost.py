"""Spawn external softsynths (Surge XT) alongside the slackbeatz GUI
so the user can tweak instrument sounds live while slackbeatz keeps
generating MIDI.

Architecture:

* slackbeatz spawns FluidSynth as the audio sink (existing behaviour).
* When ``--surge`` is enabled, we ALSO spawn N Surge XT processes —
  one per pitched MIDI channel by default. Each Surge XT listens on
  the same FluidSynth-virtual MIDI port slackbeatz already drives,
  and the user sets each Surge XT instance to filter for a specific
  MIDI channel (1 = lead, 2 = bass, 3 = pad, 4 = candy).
* For the channels covered by Surge XT, we MUTE FluidSynth via
  ``cc <ch> 7 0`` on FluidSynth's stdin shell, so we don't double up
  audio. Drum channel 10 keeps playing through FluidSynth (Surge XT
  isn't a drum machine).

This intentionally doesn't try to auto-configure Surge XT's MIDI
channel filter — Surge XT's standalone config is shared across
instances + version-fragile. Slackbeatz prints clear per-window
routing instructions instead, and the user does one-time setup
inside each Surge XT window.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# Default Mac install path for Surge XT.
_SURGE_APP = Path("/Applications/Surge XT.app")
_SURGE_BIN = _SURGE_APP / "Contents" / "MacOS" / "Surge XT"


def is_surge_installed() -> bool:
    """Detect whether Surge XT is available on this machine."""
    if sys.platform == "darwin":
        return _SURGE_BIN.is_file()
    # Linux/Windows: check PATH for surge-xt
    return shutil.which("surge-xt") is not None


def install_hint() -> str:
    """Per-platform install instruction string."""
    if sys.platform == "darwin":
        return "brew install --cask surge-xt"
    if sys.platform.startswith("linux"):
        return "Install via your distro's package manager (search 'surge-xt')"
    if sys.platform.startswith("win"):
        return "Download from https://surge-synthesizer.github.io/"
    return "Install Surge XT for your platform"


def spawn_surge_xt(channel_1idx: int) -> Optional[subprocess.Popen]:
    """Spawn one Surge XT standalone instance.

    Returns the subprocess.Popen, or None if Surge XT isn't installed.
    The channel argument isn't passed to Surge XT (it doesn't accept
    CLI args for MIDI channel filtering) — caller is responsible for
    showing the user which window to set to which channel.
    """
    if not is_surge_installed():
        return None
    if sys.platform == "darwin":
        proc = subprocess.Popen(
            [str(_SURGE_BIN)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Detach into its own process group so the user can close
            # the Surge XT window without it accidentally bringing
            # slackbeatz down (we still clean up explicitly on exit).
            start_new_session=True,
        )
        return proc
    if sys.platform.startswith("linux"):
        proc = subprocess.Popen(
            [shutil.which("surge-xt") or "surge-xt"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return proc
    return None  # Windows etc — not yet supported


def mute_fluidsynth_channels(fs_stdin, channel_0idx_list: list[int]) -> None:
    """Send ``cc <ch> 7 0`` to FluidSynth's stdin shell for each
    channel in the list. Mutes those channels so Surge XT can take
    over without doubling the audio.

    Caller passes 0-indexed MIDI channel numbers (= the channel field
    in mido.Message).
    """
    if fs_stdin is None:
        return
    try:
        for ch in channel_0idx_list:
            cmd = f"cc {ch} 7 0\n"
            fs_stdin.write(cmd.encode("utf-8"))
        fs_stdin.flush()
    except (BrokenPipeError, OSError):
        # FluidSynth gone; nothing to do.
        pass


def unmute_fluidsynth_channels(fs_stdin, channel_0idx_list: list[int]) -> None:
    """Restore CC 7 = 100 (the FluidSynth default) on the given channels."""
    if fs_stdin is None:
        return
    try:
        for ch in channel_0idx_list:
            cmd = f"cc {ch} 7 100\n"
            fs_stdin.write(cmd.encode("utf-8"))
        fs_stdin.flush()
    except (BrokenPipeError, OSError):
        pass


# Channel routing convention for the bundled `gm` setup. Adjust if
# the user is on a different setup file.
DEFAULT_SURGE_CHANNELS: dict[str, int] = {
    "lead":  1,    # melody — channel 1
    "bass":  2,
    "pad":   3,
    "candy": 4,    # FX / riser
}


def channel_routing_summary() -> str:
    """Human-readable summary of which slackbeatz channel each Surge XT
    window should be configured for."""
    lines = ["Surge XT routing — set each window's MIDI channel filter:"]
    for inst, ch in DEFAULT_SURGE_CHANNELS.items():
        lines.append(f"  window {ch}: slackbeatz channel {ch}  ({inst})")
    lines.append("(Settings → MIDI Settings → MIDI Channel inside each Surge XT)")
    return "\n".join(lines)
