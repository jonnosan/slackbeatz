"""Headless surge-xt-cli instances driven via OSC.

Architecture:

* slackbeatz creates one virtual MIDI port per pitched channel (lead /
  bass / pad / candy) via :class:`MultiPortSink`.
* For each pitched channel we spawn one ``surge-xt-cli`` subprocess
  bound to its dedicated MIDI port via ``--midi-input=<index>`` (no
  channel filter setup needed — each port carries only one channel).
* Each instance gets its own OSC IN port for parameter writes + an
  OSC OUT destination back to slackbeatz so we can read current
  values, doc metadata, and patch info.
* The slackbeatz Tk GUI's "Sound" tab uses these handles to drive
  cutoff / resonance / ADSR / osc-type / volume in real time.

Why surge-xt-cli instead of the GUI standalone? Surge XT Standalone
stores its MIDI input choice in a single shared settings file
(``~/Library/Application Support/Surge XT.settings``) — multiple
spawned GUI instances can't have independent MIDI inputs. The
headless CLI has no settings file: every choice is per-process
command-line state, so N instances are properly isolated.

The legacy ``--surge-gui`` flag in :mod:`slackbeatz.cli` keeps the
old GUI-spawn behaviour available for deep patch editing.
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# python-osc is an optional dep — caller is expected to have it
# installed when using --surge. We import lazily inside spawn() so
# `import slackbeatz.surge_host` works for users without it (e.g.
# when running just `slackbeatz play`).


# Standalone GUI binary (legacy --surge-gui path).
_SURGE_GUI_BIN = Path("/Applications/Surge XT.app/Contents/MacOS/Surge XT")
# Headless CLI binary (default --surge path).
_SURGE_CLI_BIN = Path("/Applications/Surge XT.app/Contents/MacOS/surge-xt-cli")

# Factory patch library root (system-wide on macOS).
_SURGE_FACTORY = Path("/Library/Application Support/Surge XT/patches_factory")


# --------------------------------------------------------------------------
# Per-role config — what we spawn for each pitched slackbeatz channel.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SynthRoleConfig:
    """Static config for one pitched-channel headless-synth instance.

    Today the only backend slackbeatz spawns is ``surge-xt-cli`` (see
    :class:`SurgeInstance`); the role-config schema is kept synth-
    agnostic so future OSC-controllable headless synths (ZynAddSubFX,
    dexed-cli, …) can reuse the same table by carrying their own
    initial-patch path format. The OSC ports are pre-allocated so they
    don't clash with anything else; pairs of (in, out) sit two apart
    per role.
    """

    role: str                  # 'lead' / 'bass' / 'pad' / 'candy' / 'sub'
    channel_1idx: int          # MIDI channel (1-indexed) slackbeatz emits on
    midi_port_name: str        # the virtual port MultiPortSink creates
    initial_patch: str         # path relative to _SURGE_FACTORY for now
    osc_in_port: int           # slackbeatz → headless synth
    osc_recv_port: int         # headless synth → slackbeatz (we listen here)


# Default role assignments — match the ``gm`` setup's channel layout.
# Each entry becomes one spawned surge-xt-cli process today; the name
# is kept synth-agnostic (``SYNTH_ROLES`` rather than ``SURGE_ROLES``)
# because the *concept* — "list of pitched roles that get a dedicated
# headless synth instance" — outlives the specific backend.
SYNTH_ROLES: tuple[SynthRoleConfig, ...] = (
    SynthRoleConfig(
        role="lead",
        channel_1idx=1,
        midi_port_name="slackbeatz-lead",
        initial_patch="Leads/Classic Lead 1.fxp",
        osc_in_port=53001,
        osc_recv_port=53002,
    ),
    SynthRoleConfig(
        role="bass",
        channel_1idx=2,
        midi_port_name="slackbeatz-bass",
        initial_patch="Basses/Bass 1.fxp",
        osc_in_port=53011,
        osc_recv_port=53012,
    ),
    SynthRoleConfig(
        role="pad",
        channel_1idx=3,
        midi_port_name="slackbeatz-pad",
        initial_patch="Pads/MKS-70 Warm Pad.fxp",
        osc_in_port=53021,
        osc_recv_port=53022,
    ),
    SynthRoleConfig(
        role="candy",
        channel_1idx=4,
        midi_port_name="slackbeatz-candy",
        initial_patch="Sequences/Bell Seq.fxp",
        osc_in_port=53031,
        osc_recv_port=53032,
    ),
    SynthRoleConfig(
        role="sub",
        channel_1idx=6,
        midi_port_name="slackbeatz-sub",
        initial_patch="Basses/Sub 1.fxp",
        osc_in_port=53041,
        osc_recv_port=53042,
    ),
)


# --------------------------------------------------------------------------
# Detection / introspection
# --------------------------------------------------------------------------


def is_surge_cli_installed() -> bool:
    """True if the headless surge-xt-cli is available on this machine."""
    if sys.platform == "darwin":
        return _SURGE_CLI_BIN.is_file()
    # Linux/Windows: assume on PATH as surge-xt-cli or surge_xt_cli.
    return any(shutil.which(name) for name in ("surge-xt-cli", "surge_xt_cli"))


def is_surge_gui_installed() -> bool:
    """True if the standalone GUI Surge XT is available."""
    if sys.platform == "darwin":
        return _SURGE_GUI_BIN.is_file()
    return shutil.which("surge-xt") is not None


def install_hint() -> str:
    """Per-platform install instruction string."""
    if sys.platform == "darwin":
        return "brew install --cask surge-xt"
    if sys.platform.startswith("linux"):
        return "Install via your distro's package manager (search 'surge-xt')"
    return "Download from https://surge-synthesizer.github.io/"


def resolve_factory_patch(relpath: str) -> Optional[Path]:
    """Return the absolute path to a factory patch, or None if missing."""
    candidate = _SURGE_FACTORY / relpath
    return candidate if candidate.is_file() else None


# Per-(role, style) Surge factory patch. Mirrors the FluidSynth
# :data:`slackbeatz.engine.midifile._GM_PROGRAM_DEFAULTS` table — FS
# already picks a style-appropriate GM program for each pitched
# channel, so the Surge path should do the same.
#
# Lookup: ``_STYLE_PATCH_FOR_ROLE[(role, style)]``. Missing entries
# fall back to :attr:`SynthRoleConfig.initial_patch` so an unmapped
# style still gets *some* sound rather than silence. Roles match the
# entries in :data:`SYNTH_ROLES`. The gen-type → role mapping used
# by :func:`apply_song_patches` is:
#
#   bass    → bass    (channel 2)
#   melody  → lead    (channel 1)
#   chords  → pad     (channel 3)
#   candy   → candy   (channel 4)
#   subbass → sub     (channel 6)
#
# Picks lean on factory-patch character: 303-y for acid, FM-punchy
# for psytrance, deep/atmospheric for dub-techno, Rhodes-leaning for
# lofi / vaporwave, and so on. The user can always override any
# instance via the Sound tab patch picker — these are starting
# defaults that change automatically when the song's style changes.
_STYLE_PATCH_FOR_ROLE: dict[tuple[str, str], str] = {
    # ----- bass -----
    ("bass", "rolling"):        "Basses/Bass 1.fxp",
    # warm_analogue's bass — same algorithm as rolling but routed
    # to the smoother "Smoothie" patch (warmer / less aggressive
    # filter character) for the DMX Krew / Breakin Records sound.
    ("bass", "warm_sub"):       "Basses/Smoothie.fxp",
    ("bass", "acid_303"):          "Basses/Mmm... Pointy!.fxp",
    ("bass", "gallop"):     "Basses/FM Bass 1.fxp",
    ("bass", "subdrone"):   "Basses/Sub 2.fxp",
    ("bass", "sustain_drone"):    "Basses/Sub 3.fxp",
    ("bass", "mellow_pick"):     "Basses/E-Bass.fxp",
    ("bass", "reese"): "Basses/Lord Sawtooth.fxp",
    ("bass", "two_step_sub"):        "Basses/Rubber Bass.fxp",
    ("bass", "acoustic_walk"):          "Basses/Piano Bass.fxp",

    # ----- lead (melody) -----
    ("lead", "euclid_riff"):        "Leads/Classic Lead 1.fxp",
    ("lead", "acid_stab"):          "Leads/Acidofil.fxp",
    # Iteration 1.6 — sequenced lead (superseded by sh101_arp in 1.7,
    # kept for hand-written .sb compatibility).
    ("lead", "acid_lead"):          "Leads/Acidofil.fxp",
    # Iteration 1.7 — SH-101-style euclidean-clocked arp. Uses the
    # Acidofil patch for its resonant 303 character.
    ("lead", "sh101_arp"):          "Leads/Acidofil.fxp",
    ("lead", "psy_lead"):     "Leads/Square.fxp",
    ("lead", "sparse_pad_lead"):   "Leads/Etwas.fxp",
    ("lead", "distant_lead"):    "Leads/Fluff.fxp",
    ("lead", "lazy_sax"):     "Leads/Cottage.fxp",
    ("lead", "atmos_lead"): "Leads/Bee.fxp",
    ("lead", "vocal_chop"):        "Leads/Cell.fxp",
    ("lead", "rhodes_phrase"):          "Keys/EP 1.fxp",

    # ----- pad (chords) -----
    ("pad", "triad_sustain"):         "Pads/MKS-70 Warm Pad.fxp",
    ("pad", "sustained_dyad"):           "Keys/House Organ.fxp",
    ("pad", "psy_swell"):      "Pads/Communication.fxp",
    ("pad", "pad_drift"):    "Pads/Ghost Pad.fxp",
    ("pad", "offbeat_stab"):     "Pads/Pad 3.fxp",
    ("pad", "arp_walk"):      "Keys/DX EP.fxp",
    ("pad", "atmos_pad"):  "Pads/Distant.fxp",
    ("pad", "wurli_chop"):         "Keys/EP 2.fxp",
    ("pad", "rhodes_chord"):           "Keys/EP 1.fxp",
    # New for the authenticity-tuning pass: filter-enveloped stab on
    # the pad channel mirrors the melody-side acid_stab's character —
    # same resonant 303-style patch so the stab and the bass speak the
    # same language.
    ("pad", "acid_stab"):       "Leads/Acidofil.fxp",

    # ----- candy (sequences/FX) -----
    ("candy", "euclid_riser"):        "Sequences/Bell Seq.fxp",
    ("candy", "acid_sweep"):          "Sequences/Acid Seq 1.fxp",
    ("candy", "psy_sweep"):     "Sequences/Acid Seq 2.fxp",
    ("candy", "slow_lfo"):   "Sequences/Phase 1.fxp",
    ("candy", "drone_lfo"):    "Sequences/Burial Ground.fxp",
    ("candy", "bell_lfo"):     "Sequences/Bell Seq.fxp",
    ("candy", "atmos_lfo"): "Sequences/Phase 2.fxp",
    ("candy", "minimal_lfo"):        "Sequences/Step Phaser.fxp",
    ("candy", "crackle_lfo"):          "Sequences/Sine Sequencer 1.fxp",
    # Iteration 1.12 — fast top-arp layer for warm_analogue. Uses
    # a brighter bell/seq patch so it adds sparkle above the lead
    # rather than fighting it.
    ("candy", "sh101_top"):     "Sequences/Bell Seq.fxp",

    # ----- sub (subbass) -----
    ("sub", "euclid"):          "Basses/Sub 1.fxp",
    ("sub", "acid"):            "Basses/Sub 1.fxp",
    ("sub", "psytrance"):       "Basses/Sub Square.fxp",
    ("sub", "deep_techno"):     "Basses/Sub 2.fxp",
    ("sub", "dub_techno"):      "Basses/Sub 3.fxp",
    ("sub", "vaporwave"):       "Basses/Sub 2.fxp",
    ("sub", "drum_and_bass"):   "Basses/Sub 1.fxp",
    ("sub", "garage"):          "Basses/Sub 1.fxp",
    ("sub", "lofi"):            "Basses/Sub 4.fxp",
}


# Pitched gen type → SYNTH_ROLES role. Used by
# :func:`apply_song_patches` to pick the right (role, style) key for
# each spawned Surge instance.
_GEN_TYPE_TO_ROLE: dict[str, str] = {
    "bass":    "bass",
    "melody":  "lead",
    "chords":  "pad",
    "candy":   "candy",
    "subbass": "sub",
}


def style_patch_for_role(role: str, style: str) -> Optional[Path]:
    """Absolute path to the (role, style) factory patch, or None if
    no mapping exists (caller falls back to the role's default
    :attr:`SynthRoleConfig.initial_patch`)."""
    relpath = _STYLE_PATCH_FOR_ROLE.get((role, style))
    if relpath is None:
        return None
    return resolve_factory_patch(relpath)


def apply_song_patches(surge_instances, resolved_song) -> int:
    """Reload each Surge instance's patch to match the current song's
    style for that role.

    For every :class:`SurgeInstance` in *surge_instances*, find the
    first gen in *resolved_song* whose ``(type, channel)`` maps to
    that instance's role, look up the (role, style) patch, and
    :meth:`SurgeInstance.load_patch` it if it differs from the
    currently-loaded patch.

    Returns the number of instances whose patch was reloaded.
    Idempotent — repeat calls with the same song are no-ops.
    """
    if not surge_instances or resolved_song is None:
        return 0

    # Build channel → first matching gen (sorted by handle for
    # determinism) so we can find one gen per Surge instance.
    by_channel: dict[int, object] = {}
    for handle in sorted(resolved_song.gens.keys()):
        gen = resolved_song.gens[handle]
        if gen.instrument is None:
            continue
        ch = gen.instrument.channel
        if ch in by_channel:
            continue
        by_channel[ch] = gen

    reloaded = 0
    for inst in surge_instances:
        role = inst.config.role
        gen = by_channel.get(inst.config.channel_1idx)
        if gen is None:
            continue
        # Verify the gen's type maps to this role — otherwise the
        # song is using the channel for something the (role, style)
        # table doesn't cover, and we leave the current patch alone.
        if _GEN_TYPE_TO_ROLE.get(gen.type_) != role:
            continue
        patch_path = style_patch_for_role(role, gen.style)
        if patch_path is None:
            continue
        target_rel = str(patch_path.relative_to(_SURGE_FACTORY))
        if inst.current_patch_rel == target_rel:
            continue
        try:
            inst.load_patch(patch_path)
            reloaded += 1
        except Exception:
            # Best-effort — a failed patch load shouldn't tear down
            # the whole song-state-change pipeline.
            continue
    return reloaded


# Per-role default category under _SURGE_FACTORY. Drives the
# Instruments-tab dropdown so each Surge channel shows a role-
# appropriate subset of factory patches (~50-130 each) rather than the
# full ~500-patch flat list. Users who want cross-category patches
# can always swap via the legacy --surge-gui window or
# SurgeInstance.load_patch() directly.
_PATCH_CATEGORY_FOR_ROLE: dict[str, str] = {
    "lead":  "Leads",
    "bass":  "Basses",
    "pad":   "Pads",
    "candy": "Sequences",
    "sub":   "Basses",
}


def list_factory_patches(category: Optional[str] = None) -> list[tuple[str, str]]:
    """Return ``[(display_name, relpath), ...]`` for the Surge XT
    factory patches under *category* (a top-level directory like
    ``Basses`` / ``Leads`` / ``Pads`` — see :data:`_PATCH_CATEGORY_FOR_ROLE`).

    *category* of ``None`` returns every factory patch, prefixed with
    its category directory. Sorted alphabetically by display name.
    Returns ``[]`` if the factory dir isn't present (stripped install)."""
    if not _SURGE_FACTORY.is_dir():
        return []
    out: list[tuple[str, str]] = []
    if category is not None:
        root = _SURGE_FACTORY / category
        if not root.is_dir():
            return []
        for fxp in sorted(root.rglob("*.fxp")):
            # Strip the .fxp suffix for the display label; keep the
            # full relpath so resolve_factory_patch() can find it.
            display = fxp.stem
            rel = str(fxp.relative_to(_SURGE_FACTORY))
            out.append((display, rel))
        return out
    for fxp in sorted(_SURGE_FACTORY.rglob("*.fxp")):
        rel = str(fxp.relative_to(_SURGE_FACTORY))
        display = rel[:-4] if rel.endswith(".fxp") else rel
        out.append((display, rel))
    return out


def patch_category_for_role(role: str) -> Optional[str]:
    """The default factory-patch subdirectory for a slackbeatz role
    (see :data:`_PATCH_CATEGORY_FOR_ROLE`). Returns ``None`` for
    unknown roles so callers can skip the patch dropdown rather than
    showing every factory patch in the world."""
    return _PATCH_CATEGORY_FOR_ROLE.get(role)


def _list_devices_raw() -> str:
    """Single shared --list-devices invocation; returns stdout+stderr."""
    if not is_surge_cli_installed():
        return ""
    try:
        result = subprocess.run(
            [str(_SURGE_CLI_BIN), "--list-devices"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    return result.stdout + result.stderr


def list_midi_device_indices() -> dict[str, int]:
    """Query surge-xt-cli for its current MIDI input list and return a
    mapping of port name → device index.

    surge-xt-cli's ``--list-devices`` output looks like:

        MIDI Device: [0] : slackbeatz-lead
        MIDI Device: [1] : slackbeatz-bass

    The indices here are what ``--midi-input N`` expects. Virtual ports
    must already exist (i.e. ``MultiPortSink.open()`` must have been
    called) for them to show up.
    """
    pattern = re.compile(r"MIDI Device:\s*\[(\d+)\]\s*:\s*(.+?)\s*$")
    out: dict[str, int] = {}
    for line in _list_devices_raw().splitlines():
        m = pattern.search(line)
        if m:
            out[m.group(2).strip()] = int(m.group(1))
    return out


# --------------------------------------------------------------------------
# Per-instance handle — wraps the subprocess + OSC client/server.
# --------------------------------------------------------------------------


# Useful OSC paths for the v1 Sound panel. Only those that exist
# universally across patches. Add more as the panel grows.
KNOB_ADDRS: dict[str, str] = {
    "filter_cutoff":    "/param/a/filter/1/cutoff",
    "filter_resonance": "/param/a/filter/1/resonance",
    "filter_type":      "/param/a/filter/1/type",
    "osc1_type":        "/param/a/osc/1/type",
    "aeg_attack":       "/param/a/aeg/attack",
    "aeg_decay":        "/param/a/aeg/decay",
    "aeg_sustain":      "/param/a/aeg/sustain",
    "aeg_release":      "/param/a/aeg/release",
    "scene_volume":     "/param/a/amp/volume",
    # Master output of the Surge instance — used as the "channel
    # fader" by the slackbeatz Mixer tab. /param/a/amp/volume above
    # is scene-A-only; the global address is the right knob for a
    # whole-instance volume slider.
    "global_volume":    "/param/global/volume",
}


# --------------------------------------------------------------------------
# FX catalog — used by the 🎛 Mixer tab's per-Surge FX-slot dropdowns
# --------------------------------------------------------------------------
#
# Surge XT ships ~29 factory FX types. We surface a curated subset on
# the mixer (the ones a casual user would reach for during a live mix).
# Each entry maps the Surge ``fx_type`` enum value → ``FXSpec`` with
# the display label + the essential param indices to render as
# sliders. Param indices match Surge's ``/param/fx/<s>/<n>/paramX`` OSC
# tree where ``X`` is the 1-based ctrl index inside the FX type's
# parameter list.
#
# Type-ids + the "essentials" subset are pinned here so the mixer
# doesn't need to query Surge at runtime. If Surge bumps the enum
# values upstream, the catalog stays consistent until we update it.
# Issue #35 tracks replacing this with runtime /doc discovery.


@dataclass(frozen=True)
class FXSpec:
    """One FX-type catalog entry: display name + the ``param1..N``
    indices we surface as sliders, with friendly labels for each."""

    name: str
    params: tuple[tuple[str, int], ...]  # (label, 1-based param index)


# Surge XT factory FX type enum (from ``src/common/SurgeStorage.h``
# in surge-synthesizer/surge). The full enum has ~29 entries; we
# expose ~10 of the most-useful in the mixer dropdown.
FX_CATALOG: dict[int, FXSpec] = {
    0:  FXSpec("Off",        ()),  # disables the slot — slider area is empty
    1:  FXSpec("Delay",      (("time", 1), ("feedback", 2), ("mix", 3))),
    2:  FXSpec("Reverb 1",   (("size", 1), ("decay", 2), ("mix", 3))),
    3:  FXSpec("Phaser",     (("rate", 1), ("depth", 2), ("mix", 3))),
    4:  FXSpec("Rotary",     (("speed", 1), ("drive", 2), ("mix", 3))),
    5:  FXSpec("Distortion", (("drive", 1), ("tone", 2), ("mix", 3))),
    9:  FXSpec("Chorus",     (("rate", 1), ("depth", 2), ("mix", 3))),
    10: FXSpec("Vocoder",    ()),  # complex — power-only in v1
    11: FXSpec("Reverb 2",   (("size", 1), ("decay", 2), ("mix", 3))),
    12: FXSpec("Flanger",    (("rate", 1), ("depth", 2), ("mix", 3))),
    13: FXSpec("Ring Mod",   (("freq", 1), ("mix", 2))),
}

# Default load-out for FX slots A1 + A2 on every Surge instance.
# Distortion + Delay matches the "mixer with distortion + delay"
# expectation; both start powered OFF so the patch sounds dry by
# default — the user opts in via the mixer's Power toggle.
_DEFAULT_FX_TYPE_SLOT1 = 5  # Distortion
_DEFAULT_FX_TYPE_SLOT2 = 1  # Delay


# Process-wide cache of per-FX-type param names discovered at runtime
# via Surge's /doc OSC queries. Key: (type_id, param_idx 1-based).
# Populated by SurgeInstance._discover_fx_param_names which runs once
# per slackbeatz session (gated by the flag below) on the first Surge
# instance to finish booting. Surge's FX param naming is per-type,
# not per-instance, so one sweep covers every instance forever.
#
# A missing key means "we haven't observed a real name for this slot".
# Empty-string / "param N" placeholder replies from Surge are
# explicitly NOT stored — that way the GUI can use a missing key as
# the signal to hide the row rather than render an unhelpful generic.
FX_DOC_CACHE: dict[tuple[int, int], str] = {}
_FX_DOC_DISCOVERY_DONE = threading.Event()
# Acquired by whichever Surge instance starts the background sweep
# first; non-blocking, so additional instances no-op without
# spawning duplicate sweep threads.
_FX_DOC_DISCOVERY_LOCK = threading.Lock()


def fx_addr(slot: int, kind: str, param_idx: int | None = None) -> str:
    """Build a Surge FX-block OSC address for slot A1 / A2.

    *kind* ∈ ``"type"`` / ``"deactivate"`` / ``"param"`` (in which
    case *param_idx* is required, 1-based). slot is 1 or 2 — the
    scene-A FX slots that Surge processes in order.
    """
    base = f"/param/fx/a/{slot}"
    if kind == "type":
        return f"{base}/type"
    if kind == "deactivate":
        return f"{base}/deactivate"
    if kind == "param":
        if param_idx is None:
            raise ValueError("kind='param' requires param_idx")
        return f"{base}/param{param_idx}"
    raise ValueError(f"unknown fx kind {kind!r}")


@dataclass
class SurgeInstance:
    """One running surge-xt-cli + its OSC plumbing.

    Use :func:`spawn_surge_instances` to construct + start a quartet
    in one call. Each instance is independent; teardown closes the
    subprocess + OSC server.
    """

    config: SynthRoleConfig
    midi_input_index: int
    proc: Optional[subprocess.Popen] = None
    _client: object = None    # pythonosc SimpleUDPClient (lazy import)
    _server: object = None    # pythonosc ThreadingOSCUDPServer
    _server_thread: Optional[threading.Thread] = None
    # Cached values from /param replies. address → (value: float, display: str)
    _values: dict[str, tuple[float, str]] = field(default_factory=dict)
    # Cached metadata from /doc replies. address → (name, type, min, max).
    _docs: dict[str, tuple[str, str, float, float]] = field(default_factory=dict)
    _values_lock: threading.Lock = field(default_factory=threading.Lock)
    # Currently-loaded factory patch (relative path under _SURGE_FACTORY).
    # Tracked here so the GUI's Instruments tab can show the live patch
    # name + re-sync after the user picks something different. Updated
    # by :meth:`load_patch`; initialised to the role's default on spawn.
    current_patch_rel: Optional[str] = None

    # -- OSC reply handlers --

    def _on_osc(self, addr: str, *args) -> None:
        if addr.startswith("/doc/"):
            # /doc/<path> name type min max
            if len(args) >= 4 and not addr.endswith("/ext"):
                key = addr[len("/doc"):]  # → '/param/...'
                try:
                    self._docs[key] = (
                        str(args[0]), str(args[1]),
                        float(args[2]), float(args[3]),
                    )
                except (ValueError, TypeError):
                    pass
            return
        # Parameter values: /param/<path> <value> [<display>]
        if addr.startswith("/param/") and args:
            try:
                value = float(args[0])
            except (ValueError, TypeError):
                return
            display = str(args[1]) if len(args) >= 2 else ""
            with self._values_lock:
                self._values[addr] = (value, display)

    # -- lifecycle --

    def spawn(self) -> None:
        """Start the subprocess + OSC server. Blocks until the CLI is
        accepting OSC (~2s)."""
        from pythonosc import dispatcher as _disp
        from pythonosc import osc_server as _osc_server
        from pythonosc import udp_client as _udp

        if not is_surge_cli_installed():
            raise RuntimeError("surge-xt-cli not installed")

        # 1. OSC server first (so we don't miss early replies).
        disp = _disp.Dispatcher()
        disp.set_default_handler(self._on_osc)
        self._server = _osc_server.ThreadingOSCUDPServer(
            ("127.0.0.1", self.config.osc_recv_port), disp,
        )
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
        )
        self._server_thread.start()

        # 2. Client to send messages into surge-xt-cli.
        self._client = _udp.SimpleUDPClient(
            "127.0.0.1", self.config.osc_in_port,
        )

        # 3. The subprocess. surge-xt-cli's CLI11 parser only accepts
        # the ``--flag=value`` form for these options (space-separated
        # values get parsed as stray positional args and the process
        # exits with "arguments were not expected").
        args = [
            str(_SURGE_CLI_BIN),
            f"--midi-input={self.midi_input_index}",
            f"--osc-in-port={self.config.osc_in_port}",
            f"--osc-out-port={self.config.osc_recv_port}",
            "--osc-out-ipaddr=127.0.0.1",
            "--no-stdin",
        ]
        patch_path = resolve_factory_patch(self.config.initial_patch)
        if patch_path is not None:
            args.append(f"--init-patch={patch_path}")
            # Record the initial patch as the "currently loaded" one so
            # the GUI's Instruments tab knows what to show on first
            # render. :meth:`load_patch` keeps this fresh when the user
            # picks something else from the GUI.
            self.current_patch_rel = self.config.initial_patch

        self.proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

        # Belt-and-suspenders: even if slackbeatz exits without
        # running its normal `finally:` cleanup (macOS Cmd+Q on the
        # Tk window, unhandled exception in a daemon thread, plain
        # `sys.exit()` from a script, …) atexit guarantees the
        # surge-xt-cli subprocess we just spawned doesn't orphan in
        # its own session. ``self.shutdown`` is idempotent so it's
        # safe to fire twice when the normal cleanup did run.
        #
        # Caveats — atexit does NOT fire on:
        #   * os._exit() — callers using that escape hatch must do
        #     their own inst.shutdown() before exiting
        #   * `kill -9` or segfault (no signal handler can rescue
        #     those, would-be-orphans show up in Activity Monitor)
        atexit.register(self.shutdown)

        # 4. Wait for boot — surge-xt-cli prints "Starting OSC input"
        # within ~100ms but a brief grace period lets the patch load.
        time.sleep(1.5)

        # 5. Prime the value cache for the panel knobs.
        for addr in KNOB_ADDRS.values():
            self.query(addr)

        # 6. Load the default FX chain (Distortion in A1, Delay in
        # A2, both deactivated) so the Mixer tab has a consistent
        # starting state on every Surge instance. ``deactivate=1``
        # keeps the patch sounding dry until the user opts in via the
        # Mixer's Power toggle.
        self._load_default_fx_slots()

    def _load_default_fx_slots(self) -> None:
        """Send the FX-slot type + deactivate writes for the v1 mixer
        default chain, plus the per-param /doc queries that populate
        the live-discovered label cache used by the Mixer GUI. The
        first instance to reach this point ALSO sweeps every FX type
        in :data:`FX_CATALOG` and records the per-type param names
        into the process-wide :data:`FX_DOC_CACHE` — so the GUI has
        real labels available the moment the user picks any FX type,
        without waiting for an OSC round-trip."""
        # Default chain first — the discovery sweep that follows uses
        # slot 1 as scratch space, so getting the user-visible default
        # chain in place beforehand isn't possible. Instead the sweep
        # itself ends by restoring slot 1 to the default before we
        # query its docs.
        self.set_param(fx_addr(2, "type"), float(_DEFAULT_FX_TYPE_SLOT2))
        self.set_param(fx_addr(2, "deactivate"), 1.0)

        # Load the real default in slot 1 + query its docs. Done
        # BEFORE kicking off discovery so the user-visible FX state
        # is correct immediately — discovery happens in the
        # background and only writes to FX_DOC_CACHE, never to a
        # user-visible slot.
        self.set_param(fx_addr(1, "type"), float(_DEFAULT_FX_TYPE_SLOT1))
        self.set_param(fx_addr(1, "deactivate"), 1.0)
        # Surge needs ~50ms to settle the new FX type's param tree
        # before /doc replies are meaningful.
        time.sleep(0.1)
        self.query_fx_slot_docs(1)
        self.query_fx_slot_docs(2)

        # Discovery sweep — once per process, in a background thread
        # so we don't pile ~3s onto slackbeatz startup. The first
        # Surge instance to reach this point owns the sweep;
        # subsequent instances no-op. The Sound tab renders with
        # the catalog fallback while discovery is in flight + auto-
        # swaps to real labels on the next /doc repoll (the
        # _FX_DOC_DISCOVERY_DONE event flips when the sweep
        # finishes, which the GUI's _label_for_param checks each
        # render).
        if _FX_DOC_DISCOVERY_LOCK.acquire(blocking=False):
            def _sweep_then_release() -> None:
                try:
                    # The sweep uses slot 2 as scratch space (not
                    # slot 1) so the user-visible default in slot 1
                    # stays put even if a Sound-tab repaint races
                    # with the sweep. Final write at the end of
                    # the sweep restores slot 2 to its default.
                    self._discover_fx_param_names_into_cache()
                    # Re-install the slot 2 default once the sweep
                    # is done (the sweep left slot 2 at whatever
                    # the last visited type was).
                    self.set_param(
                        fx_addr(2, "type"), float(_DEFAULT_FX_TYPE_SLOT2),
                    )
                    self.set_param(fx_addr(2, "deactivate"), 1.0)
                    time.sleep(0.1)
                    self.query_fx_slot_docs(2)
                finally:
                    _FX_DOC_DISCOVERY_DONE.set()
                    _FX_DOC_DISCOVERY_LOCK.release()

            threading.Thread(
                target=_sweep_then_release,
                name=f"surge-fx-doc-discovery-{self.config.role}",
                daemon=True,
            ).start()

    def _discover_fx_param_names_into_cache(self) -> None:
        """Sweep every FX type in :data:`FX_CATALOG`, load it into
        slot 2 transiently, query Surge for each param's /doc reply,
        and stash real names into :data:`FX_DOC_CACHE`. About 300ms
        per FX type, ~3s total — runs once per slackbeatz session on
        whichever Surge instance reaches it first, in a background
        thread so it doesn't block startup. Slot 2 is used as
        scratch space (not slot 1) so the user-visible default
        Distortion in slot 1 isn't disturbed if the GUI repaints
        mid-sweep."""
        scratch_slot = 2
        for type_id in FX_CATALOG:
            if type_id == 0:
                continue  # "Off" — no params to label
            # Load type, give Surge time to settle the param tree,
            # fire /doc queries for all 12 slots.
            self.set_param(fx_addr(scratch_slot, "type"), float(type_id))
            self.set_param(fx_addr(scratch_slot, "deactivate"), 1.0)
            time.sleep(0.12)
            self.query_fx_slot_docs(scratch_slot)
            # Replies arrive via the UDP server thread. We need to
            # wait until they've actually been parsed into
            # ``self._docs`` before we read; localhost UDP is
            # sub-ms but Surge can batch up to a dozen replies
            # serially. 200ms is comfortable headroom.
            time.sleep(0.2)
            for p_idx in range(1, 13):
                addr = fx_addr(scratch_slot, "param", p_idx)
                doc = self._docs.get(addr)
                if doc is None:
                    continue
                name = (doc[0] or "").strip()
                # Skip placeholders — they mean Surge regards this
                # param as unused inside the current FX type, which
                # is exactly what we want the GUI to hide rather
                # than label generically.
                if not name or name.lower().startswith("param "):
                    continue
                # First real observation wins. (If two FX types
                # share a param index but different names, the
                # later type can't overwrite — but since the cache
                # key includes type_id this never collides.)
                FX_DOC_CACHE[(type_id, p_idx)] = name

    def shutdown(self) -> None:
        """Terminate the subprocess + OSC server. Safe to call twice."""
        if self.proc is not None:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            except Exception:
                pass
            self.proc = None
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    # -- knob ops --

    def set_param(self, addr: str, value: float) -> None:
        """Send a parameter change to this instance via OSC.

        Surge XT doesn't auto-push outbound on changes (only replies
        to explicit ``/q`` queries), so we follow up with a query to
        refresh the cached display string. Cheap (~1ms round-trip).
        """
        if self._client is None:
            return
        try:
            self._client.send_message(addr, float(value))
            # Optimistic local cache update so reads don't lag the
            # send; the real display string lands ~1ms later via the
            # /q reply.
            with self._values_lock:
                prev = self._values.get(addr, (0.0, ""))
                self._values[addr] = (float(value), prev[1])
        except OSError:
            return
        self.query(addr)

    def query(self, addr: str) -> None:
        """Request the current value of *addr*. The reply lands in the
        cache asynchronously; read via :meth:`get_value`."""
        if self._client is None:
            return
        from pythonosc.osc_message_builder import OscMessageBuilder
        try:
            msg = OscMessageBuilder(f"/q{addr}").build()
            self._client.send(msg)
        except OSError:
            pass

    def query_doc(self, addr: str) -> None:
        """Request the doc metadata (name, type, min, max) for *addr*.

        Surge replies asynchronously via the existing ``/doc/...``
        handler which populates :attr:`_docs`. Read the result back
        via :meth:`get_param_doc`. Used by the 🎛 Mixer tab to discover
        the real per-FX param names (which Surge knows but our
        hardcoded :data:`FX_CATALOG` only approximates)."""
        if self._client is None:
            return
        from pythonosc.osc_message_builder import OscMessageBuilder
        try:
            msg = OscMessageBuilder(f"/doc{addr}").build()
            self._client.send(msg)
        except OSError:
            pass

    def query_fx_slot_docs(self, slot: int) -> None:
        """Convenience: fire ``/doc`` queries for every param of FX
        slot ``slot`` (1 or 2). Use after :meth:`set_param` of
        ``/param/fx/a/<slot>/type`` — Surge swaps the FX type and the
        params now have new meanings. Replies trickle into
        :attr:`_docs` over the next ~50-100 ms; the Mixer GUI reads
        whichever labels are present at render time + falls back to
        :data:`FX_CATALOG` for ones not yet known."""
        for i in range(1, 13):
            self.query_doc(fx_addr(slot, "param", i))

    def get_value(self, addr: str) -> Optional[float]:
        """Most recent cached value at *addr*, or None if never seen."""
        with self._values_lock:
            entry = self._values.get(addr)
        return entry[0] if entry else None

    def get_display(self, addr: str) -> str:
        """Most recent cached display string at *addr*, or ''."""
        with self._values_lock:
            entry = self._values.get(addr)
        return entry[1] if entry else ""

    def get_param_doc(self, addr: str) -> Optional[tuple[str, str, float, float]]:
        """Doc tuple ``(name, type, min, max)`` for *addr*, or ``None``
        if Surge hasn't replied yet. Lock-free read — :attr:`_docs` is
        only mutated by the OSC server thread via :meth:`_on_osc`."""
        return self._docs.get(addr)

    def audition_note(
        self, *, pitch: int = 60, velocity: int = 100, duration_s: float = 0.6,
    ) -> None:
        """Play one note through this Surge instance for *duration_s*.

        Used by the patch picker's Audition button so the user can
        hear the currently-loaded patch even when the song isn't
        playing. Opens a transient mido output on the instance's
        :attr:`SynthRoleConfig.midi_port_name` (the virtual port the
        surge-xt-cli is subscribed to), fires note_on, schedules
        note_off in a background timer thread, then returns
        immediately so the GUI stays responsive.

        Channel is fixed to 0 (the surge instance listens on every
        channel of its single MIDI input port, so any channel
        works). Pitch defaults to middle C; velocity 100 is a
        sensible mid-loudness default.
        """
        import threading
        import time as _time
        import mido

        port_name = self.config.midi_port_name
        try:
            out = mido.open_output(port_name)
        except Exception:
            return
        try:
            out.send(mido.Message(
                "note_on", channel=0, note=int(pitch), velocity=int(velocity),
            ))
        except Exception:
            out.close()
            return

        def _release():
            try:
                _time.sleep(duration_s)
                out.send(mido.Message(
                    "note_off", channel=0, note=int(pitch), velocity=0,
                ))
            except Exception:
                pass
            finally:
                try:
                    out.close()
                except Exception:
                    pass
        threading.Thread(target=_release, daemon=True).start()

    def load_patch(self, patch_path: Path) -> None:
        """Load *patch_path* (an .fxp file) in this instance.

        Updates :attr:`current_patch_rel` so the GUI's Sound tab
        knows what's loaded right now. The stored value is the path
        relative to :data:`_SURGE_FACTORY` when the patch comes from
        the factory tree; absolute path otherwise (user-saved
        presets).

        Wire-format gotcha: Surge XT's ``/patch/load`` OSC handler
        expects the **absolute path WITHOUT the .fxp extension**
        (see ``resources/surge-shared/oscspecification.html`` in
        surge-synthesizer/surge, plus the reference call in
        ``scripts/osc-tests/OSC_test_hang2.py``). Sending the path
        with the extension is silently ignored — patch stays
        unchanged. We strip the suffix here so callers can pass
        plain ``Path("…/Bass 1.fxp")`` instances and have them work."""
        if self._client is None:
            return
        # Strip the .fxp suffix before sending — Surge expects the
        # extensionless path. Doing this on str(path) rather than
        # Path.with_suffix("") guarantees we only ever touch the
        # trailing ".fxp" (some patch names contain dots).
        osc_arg = str(patch_path)
        if osc_arg.endswith(".fxp"):
            osc_arg = osc_arg[:-4]
        try:
            self._client.send_message("/patch/load", osc_arg)
        except OSError:
            pass
        # Record the new patch as currently-loaded. Try to express it
        # relative to the factory root for nicer GUI labels; fall back
        # to the absolute path for user presets that live elsewhere.
        try:
            rel = str(Path(patch_path).relative_to(_SURGE_FACTORY))
        except ValueError:
            rel = str(patch_path)
        self.current_patch_rel = rel
        # After load, re-prime the value cache for our panel knobs.
        for addr in KNOB_ADDRS.values():
            self.query(addr)


# --------------------------------------------------------------------------
# Top-level spawn helper
# --------------------------------------------------------------------------


def spawn_surge_instances(
    *,
    mode: str = "surge-standalone",
    on_progress: Optional[Callable[[str], None]] = None,
) -> list[SurgeInstance]:
    """Spawn the default quartet of surge-xt-cli instances bound to
    slackbeatz's virtual MIDI ports.

    The caller must have already opened the virtual ports (i.e.
    :class:`MultiPortSink.open` has run) so the ports show up in
    surge-xt-cli's ``--list-devices`` output.

    Only ``surge-standalone`` mode spawns Surge instances — pure-MIDI
    modes (``external``, ``ableton``) skip this entirely. The *mode*
    parameter is accepted for symmetry but doesn't change spawn
    behaviour today; surge writes to the OS default audio output.

    Returns the list of running :class:`SurgeInstance`. On failure
    (CLI not installed, port name missing) the failed instances are
    omitted from the result and a message is sent via *on_progress*.
    """
    instances: list[SurgeInstance] = []
    if not is_surge_cli_installed():
        if on_progress:
            on_progress(
                f"surge backend requested but surge-xt-cli not found. "
                f"Install: {install_hint()}"
            )
        return instances

    # Discover indices for our virtual ports.
    device_indices = list_midi_device_indices()
    if on_progress:
        on_progress(
            f"surge-xt-cli sees {len(device_indices)} MIDI input(s): "
            f"{sorted(device_indices.keys())}"
        )

    for cfg in SYNTH_ROLES:
        idx = device_indices.get(cfg.midi_port_name)
        if idx is None:
            if on_progress:
                on_progress(
                    f"  skipping {cfg.role}: virtual port "
                    f"{cfg.midi_port_name!r} not visible to surge-xt-cli"
                )
            continue
        inst = SurgeInstance(config=cfg, midi_input_index=idx)
        try:
            inst.spawn()
        except Exception as e:  # noqa: BLE001 — surface to caller
            if on_progress:
                on_progress(f"  failed to spawn {cfg.role}: {e}")
            continue
        if on_progress:
            on_progress(
                f"  {cfg.role}: midi-input={idx} ({cfg.midi_port_name!r}), "
                f"osc-in={cfg.osc_in_port}, patch={cfg.initial_patch}"
            )
        instances.append(inst)
    return instances


# --------------------------------------------------------------------------
# Legacy: GUI standalone spawn — kept for --surge-gui.
# --------------------------------------------------------------------------


def spawn_surge_gui(
    *,
    initial_patch: Optional[Path] = None,
) -> Optional[subprocess.Popen]:
    """Spawn one Surge XT GUI standalone window for deep patch editing.

    Unlike the headless CLI quartet, only ONE GUI window makes sense
    here (multiple share global MIDI config) — the user does sound
    design in the window, then ``surge_instance.load_patch()`` reloads
    the saved .fxp into the relevant CLI instance.
    """
    if not is_surge_gui_installed():
        return None
    args = [str(_SURGE_GUI_BIN)]
    if initial_patch is not None and Path(initial_patch).is_file():
        args.append(f"--init-patch={initial_patch}")
    if sys.platform == "darwin":
        return subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return None  # Linux/Windows GUI spawn not wired in this module
