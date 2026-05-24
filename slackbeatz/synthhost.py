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

# Surge XT's factory patch library lives at /Library/Application
# Support/Surge XT/patches_factory/ on macOS (system-wide). Surge XT
# also accepts a ``--init-patch=<path>`` argument that loads the
# specified ``.fxp`` file on startup — we use it to seed each window
# with a role-appropriate sound so "spawn the synths" is a one-shot
# rather than "spawn + pick a patch in each window".
_SURGE_FACTORY: Path = Path(
    "/Library/Application Support/Surge XT/patches_factory"
)


# Synth-agnostic per-role MIDI routing. Each pitched channel gets
# its own slackbeatz virtual MIDI port, named uniquely so any OSC-
# controllable headless synth (surge-xt-cli, ZynAddSubFX, dexed-cli,
# …) can subscribe to one role's traffic without needing a channel
# filter. The synth-specific bits (Surge XT factory patch paths,
# OSC ports) live in :mod:`slackbeatz.surge_host` — the third
# tuple element below is a Surge XT default that other backends are
# free to ignore.
#
# Entry shape: ``role -> (channel_1idx, virtual_port_name,
# default_surge_patch_relpath_or_None)``. ``None`` in the patch
# slot means "this role is not Surge-backed" (e.g. the ``voice`` and
# ``fx`` roles are driven by the in-process :class:`Sampler` instead
# of surge-xt-cli). :func:`spawn_surge_instances` skips ``None``
# entries; :class:`MultiPortSink` still creates their virtual MIDI
# port so the sampler can subscribe.
OSC_CHANNELS: dict[str, tuple[int, str, str | None]] = {
    "lead":  (1,  "slackbeatz-lead",  "Leads/Classic Lead 1.fxp"),
    "bass":  (2,  "slackbeatz-bass",  "Basses/Bass 1.fxp"),
    "pad":   (3,  "slackbeatz-pad",   "Pads/MKS-70 Warm Pad.fxp"),
    "candy": (4,  "slackbeatz-candy", "Sequences/Bell Seq.fxp"),
    # Sampler-backed (TTS phrases on ch 5, FX one-shots on ch 11).
    # See ``docs/design-tts-sampler.md`` + slackbeatz/sampler.py.
    "voice": (5,  "slackbeatz-voice", None),
    "fx":    (11, "slackbeatz-fx",    None),
}


def sampler_port_banks(roles: tuple[str, ...] = ("voice", "fx")) -> dict[str, dict]:
    """Build an empty ``{port_name: {}}`` map for the sampler-backed
    roles. Used by :func:`cmd_repl` / :func:`cmd_live` to construct a
    fresh :class:`Sampler` instance — generators populate the bank
    entries at resolve time via :meth:`Sampler.set_sample`."""
    return {
        OSC_CHANNELS[role][1]: {}
        for role in roles
        if role in OSC_CHANNELS
    }


def _resolve_factory_patch(relpath: Optional[str]) -> Optional[Path]:
    """Return the absolute path to a factory patch, or None if it's
    missing (e.g. user has a stripped Surge XT install) or if
    *relpath* is None (= role not backed by a Surge XT factory patch)."""
    if relpath is None:
        return None
    candidate = _SURGE_FACTORY / relpath
    return candidate if candidate.is_file() else None


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


def spawn_surge_xt(
    channel_1idx: int,
    *,
    initial_patch: Optional[Path] = None,
) -> Optional[subprocess.Popen]:
    """Spawn one Surge XT standalone instance.

    Returns the subprocess.Popen, or None if Surge XT isn't installed.
    Surge XT's standalone build doesn't accept CLI args for MIDI input
    selection — the user picks the dedicated port via the in-app MIDI
    Settings dropdown (one click; persists across launches).

    If *initial_patch* is given and the file exists, slackbeatz passes
    ``--init-patch=<path>`` to Surge XT so the window opens already
    loaded with a role-appropriate sound (lead / bass / pad / candy).
    """
    if not is_surge_installed():
        return None

    extra_args: list[str] = []
    if initial_patch is not None and Path(initial_patch).is_file():
        extra_args.append(f"--init-patch={initial_patch}")

    if sys.platform == "darwin":
        return subprocess.Popen(
            [str(_SURGE_BIN), *extra_args],
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
            [shutil.which("surge-xt") or "surge-xt", *extra_args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return None  # Windows etc — not yet supported


def channel_routing_summary() -> str:
    """Human-readable summary of which port to pick + which patch is
    pre-loaded in each Surge XT window. Used by the CLI banner and
    the GUI tab. Sampler-backed roles (patch=None) are noted but
    skipped — they don't get a Surge XT window."""
    lines = ["Surge XT routing — pick this MIDI input in each window:"]
    for inst, (ch, port, patch_rel) in OSC_CHANNELS.items():
        if patch_rel is None:
            lines.append(
                f"  ch {ch:>2} ({inst}):  {port!r}   "
                f"[sampler — no Surge window]"
            )
            continue
        patch_name = Path(patch_rel).stem
        lines.append(
            f"  ch {ch:>2} ({inst}):  MIDI Input → {port!r}   "
            f"[preloaded: {patch_name}]"
        )
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
