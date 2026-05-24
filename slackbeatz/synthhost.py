"""Spawn external softsynths (Surge XT) alongside the slackbeatz GUI
so the user can tweak instrument sounds live while slackbeatz keeps
generating MIDI.

Architecture (with automatic MIDI routing — no channel-filter setup
inside Surge XT):

* slackbeatz spawns FluidSynth as the drum audio sink (existing
  behaviour). FluidSynth creates its own virtual MIDI port.
* When ``--surge`` is enabled, slackbeatz ADDITIONALLY creates one
  *dedicated virtual MIDI port per pitched channel*
  (``slackbeatz-lead``, ``slackbeatz-bass``, ``slackbeatz-pad``,
  ``slackbeatz-candy``) and routes channels 1-4 to those ports
  instead of to FluidSynth.
* For each pitched channel, slackbeatz spawns one Surge XT window.
  The user picks the dedicated virtual port in each window's MIDI
  Settings — Surge XT's normal MIDI input list will show
  ``slackbeatz-lead`` etc. as available inputs. One click per window.
* No channel filter needed in Surge XT: each port carries only one
  channel's traffic by construction.
* Drums (channel 10) still go to FluidSynth, so the kit keeps playing.

The user does ONE click per Surge XT (pick the named input port);
Surge XT saves that as the default for next launch, so subsequent
runs are zero-click.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# Default Mac install path for Surge XT.
_SURGE_APP = Path("/Applications/Surge XT.app")
_SURGE_BIN = _SURGE_APP / "Contents" / "MacOS" / "Surge XT"


# Channel routing convention for the bundled ``gm`` setup. Each entry
# is ``inst_name -> (channel_1idx, virtual_port_name)``. The port name
# is what shows up in Surge XT's MIDI Input dropdown.
DEFAULT_SURGE_CHANNELS: dict[str, tuple[int, str]] = {
    "lead":  (1, "slackbeatz-lead"),
    "bass":  (2, "slackbeatz-bass"),
    "pad":   (3, "slackbeatz-pad"),
    "candy": (4, "slackbeatz-candy"),
}


def is_surge_installed() -> bool:
    """Detect whether Surge XT is available on this machine."""
    if sys.platform == "darwin":
        return _SURGE_BIN.is_file()
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
    Surge XT's standalone build doesn't accept CLI args for MIDI input
    selection — the user picks the dedicated port via the in-app MIDI
    Settings dropdown (one click; persists across launches).
    """
    if not is_surge_installed():
        return None
    if sys.platform == "darwin":
        return subprocess.Popen(
            [str(_SURGE_BIN)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Own process group: closing the Surge XT window doesn't
            # accidentally tear down slackbeatz; we still clean up on
            # our own exit.
            start_new_session=True,
        )
    if sys.platform.startswith("linux"):
        return subprocess.Popen(
            [shutil.which("surge-xt") or "surge-xt"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return None  # Windows etc — not yet supported


def channel_routing_summary() -> str:
    """Human-readable summary of which port to pick in each Surge XT
    window. Used by the CLI banner and the GUI tab."""
    lines = ["Surge XT routing — pick this MIDI input in each window:"]
    for inst, (ch, port) in DEFAULT_SURGE_CHANNELS.items():
        lines.append(f"  window {ch} ({inst}):  MIDI Input → {port!r}")
    lines.append(
        "(Settings → MIDI Settings → MIDI Input. Surge XT remembers "
        "the choice across launches, so this is a one-time per-window setup.)"
    )
    return "\n".join(lines)


# -- legacy FluidSynth muting helpers (kept for backward compat with
#    callers that still rely on the OLD "Surge XT subscribes to the
#    FluidSynth virtual port" topology). New code routes via
#    MultiPortSink/CompositeSink and doesn't need these. --------------------


def mute_fluidsynth_channels(fs_stdin, channel_0idx_list: list[int]) -> None:
    """Send ``cc <ch> 7 0`` to FluidSynth's stdin for each channel."""
    if fs_stdin is None:
        return
    try:
        for ch in channel_0idx_list:
            fs_stdin.write(f"cc {ch} 7 0\n".encode("utf-8"))
        fs_stdin.flush()
    except (BrokenPipeError, OSError):
        pass


def unmute_fluidsynth_channels(fs_stdin, channel_0idx_list: list[int]) -> None:
    """Restore CC 7 = 100 on the given channels."""
    if fs_stdin is None:
        return
    try:
        for ch in channel_0idx_list:
            fs_stdin.write(f"cc {ch} 7 100\n".encode("utf-8"))
        fs_stdin.flush()
    except (BrokenPipeError, OSError):
        pass
