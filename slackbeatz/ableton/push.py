"""High-level push: (role, style) → Ableton track macros.

Glues :mod:`.macro_presets` to :mod:`.osc_client`. The user clicks
"Set Ableton patches for style" on the Setup page; this module
figures out which track is which role, samples the preset registry
(with per-song variance), and pushes 8 macro values per role-track.

Track identification: case-insensitive substring match. A track
named "Bass — Acid 303" matches role "bass". This lets the user
name their tracks descriptively without breaking the SB lookup.

The macro indices on a Live Instrument Rack run 1..8 (parameter
index 0 = rack on/off). Macros 1..8 are always present even if the
user hasn't mapped all 8 — unmapped macros do nothing, which is
fine for our purposes (we always push all 8 in case the user maps
them later).
"""

from __future__ import annotations

from typing import Callable, Optional

from .macro_presets import (
    DEFAULT_PRESET, MacroPreset, apply_variance, preset_for,
)
from .osc_client import AbletonOscClient


# Conventional macro parameter indices on an Ableton Instrument Rack:
# index 0 is the rack on/off toggle; indices 1..8 are the macros.
_MACRO_PARAM_INDICES = tuple(range(1, 9))

# Roles SB pushes presets for. Drums are excluded — drum-rack
# parameter wiring is too instrument-specific to share a contract,
# and SB has no kit-shaping presets today.
_PITCHED_ROLES = ("lead", "bass", "pad", "candy", "sub")

# First device on each role-track is conventionally the rack itself.
# If the user has chained other devices first (rare), SB sends to
# device 0 — which might be an EQ or whatever. Worst-case: the
# wrong macros get tweaked; user reorders devices.
_RACK_DEVICE_INDEX = 0


def find_role_track(track_names: list[str], role: str) -> Optional[int]:
    """Return the index of the first track whose name contains *role*
    (case-insensitive substring), or ``None`` if no match."""
    needle = role.lower()
    for idx, name in enumerate(track_names):
        if needle in name.lower():
            return idx
    return None


def push_macro_presets(
    *,
    client: AbletonOscClient,
    style: str,
    song_seed: int,
    on_progress: Callable[[str], None] = lambda _msg: None,
) -> tuple[int, int, list[str]]:
    """Push macro presets for *style* to the running Ableton's tracks.

    Returns ``(pushed_count, skipped_count, warnings)``:
      * pushed_count — number of role-tracks successfully written to
      * skipped_count — roles with no matching track OR no preset
      * warnings — human-readable notes about skipped roles, missing
        AbletonOSC, etc.

    Caller passes the live song's ``style_override`` and ``song.seed``
    (or any deterministic integer per song) so the variance jitter
    is reproducible — clicking the button twice on the same song
    produces the same sound.
    """
    warnings: list[str] = []
    client.connect()
    track_names = client.get_track_names(timeout_s=1.5)
    if not track_names:
        warnings.append(
            "couldn't reach AbletonOSC — check Live is running and "
            "AbletonOSC is selected in Live → Preferences → "
            "Link, Tempo & MIDI → Control Surface. Live should display "
            "\"AbletonOSC: Listening for OSC on port 11000\" in its "
            "status bar when it's active."
        )
        return 0, 0, warnings
    on_progress(f"Ableton sees {len(track_names)} tracks")

    pushed = 0
    skipped = 0
    for role in _PITCHED_ROLES:
        track_idx = find_role_track(track_names, role)
        if track_idx is None:
            warnings.append(f"no Ableton track matched role {role!r} — skipping")
            skipped += 1
            continue
        preset = preset_for(role, style)
        if preset is None:
            # Fall back to the neutral default so unmapped styles
            # still get some sensible initial sound.
            preset = DEFAULT_PRESET
        values = apply_variance(preset, song_seed=song_seed, role=role)
        for macro_idx_zero_based, value in enumerate(values):
            param_idx = _MACRO_PARAM_INDICES[macro_idx_zero_based]
            client.set_device_parameter(
                track=track_idx,
                device=_RACK_DEVICE_INDEX,
                parameter=param_idx,
                value=value,
            )
        on_progress(
            f"  {role} → track {track_idx} ({track_names[track_idx]!r}): "
            f"macros = {[round(v, 2) for v in values]}"
        )
        pushed += 1
    return pushed, skipped, warnings
