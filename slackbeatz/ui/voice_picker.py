"""Add-voice picker — dropdown of unused role types.

Opened from the Arrangement screen's ``+ Voice`` button. Lists the
voice types not already present in the song (one voice per role for
now; future extension allows ``bass2`` / ``lead2`` style multiple
voices per role).

Picking a type:
1. Reads the song's style to derive a default algorithm for the role.
2. Adds a new gen line to the source via Player's runtime injection
   (in-place mutation of ``current_resolved.gens``).
3. Calls back ``on_added`` so the caller refreshes its view.

Wiring into the underlying .sb (so the new voice survives Save) is on
the same follow-up path as runtime knob-override emission — Phase E
prep landed the data structures; the emit-on-save plumbing for newly
added gens needs Player tracking of "user-added gens" that doesn't
exist yet.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, TYPE_CHECKING

from slackbeatz.generators.registry import REGISTRY

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


# Voice types the picker offers. Order matches typical musical
# arrangement layering (drums → bass → harmony → melody → fx → sub →
# specials).
_OFFERABLE_TYPES: tuple[str, ...] = (
    "rhythm", "bass", "chords", "melody", "candy", "subbass",
    "speech", "sample",
)


def open_voice_picker(app: "GuiApp", *, on_added: Callable[[], None]) -> None:
    """Open a small modal that picks an unused voice type."""
    resolved = app.player.current_resolved if app.player is not None else None
    if resolved is None:
        return

    in_use = {gen.type_ for gen in resolved.gens.values()}
    available = [t for t in _OFFERABLE_TYPES if t not in in_use]

    if not available:
        messagebox.showinfo(
            "Add Voice",
            "All offerable voice types are already in use. Phase 2 of "
            "the redesign will add multiple-voices-per-role (e.g. "
            "`bass2`, `lead2`).",
        )
        return

    win = tk.Toplevel(app.root)
    win.title("Add voice")
    win.transient(app.root)
    win.grab_set()

    tk.Label(win, text="Voice type:").grid(row=0, column=0, padx=8, pady=8)
    type_var = tk.StringVar(value=available[0])
    combo = ttk.Combobox(win, textvariable=type_var, state="readonly",
                         values=available, width=22)
    combo.grid(row=0, column=1, padx=8, pady=8)

    def _on_add() -> None:
        voice_type = type_var.get()
        # Pick a sensible default algorithm: the first one registered
        # for this voice type (alphabetical). The user can change it
        # immediately via the drilldown.
        algos = sorted(a for (t, a) in REGISTRY if t == voice_type)
        if not algos:
            messagebox.showerror(
                "Add Voice",
                f"No algorithms registered for type {voice_type!r}.",
            )
            win.destroy()
            return
        default_algo = algos[0]
        # In-memory mutation only today — the underlying .sb gets the
        # new line via save_state's source-injection (a follow-up that
        # mirrors the existing _inject_part_algorithm_overrides
        # pattern). The arrangement screen will see the new voice
        # immediately because it reads from current_resolved.
        _add_voice_to_resolved(app, voice_type, default_algo)
        win.destroy()
        on_added()

    ttk.Button(win, text="Add", command=_on_add).grid(
        row=1, column=0, columnspan=2, pady=10,
    )


def _add_voice_to_resolved(app: "GuiApp", voice_type: str, algorithm: str) -> None:
    """Inject a new gen into the player's resolved song.

    Today this only mutates the in-memory song — the new gen plays as
    soon as the user hits Play, but it isn't yet round-tripped to the
    .sb source on Save. The Player tracks added gens in
    ``_added_gens`` so a future save_state injector can emit the new
    ``gen`` lines + their indented part-handle lines.
    """
    from slackbeatz.model.song import ResolvedGen
    from slackbeatz.setup.model import Instrument

    resolved = app.player.current_resolved
    # Pick a handle name — same as the type for the first voice of
    # this role (matches the existing examples' convention).
    handle = voice_type
    # Synthesise an Instrument on a channel the setup doesn't already
    # use, or fall back to the voice-type's conventional channel.
    used_channels = {
        gen.instrument.channel for gen in resolved.gens.values()
        if gen.instrument is not None
    }
    convention = {
        "rhythm": 10, "bass": 2, "chords": 3, "melody": 1,
        "candy": 4, "subbass": 6, "speech": 5, "sample": 11,
    }
    channel = convention.get(voice_type, max(used_channels, default=0) + 1)
    note = 36 if voice_type == "rhythm" else None  # GM kick for new drum voices
    inst = Instrument(name=handle, channel=channel, note=note)
    new_gen = ResolvedGen(
        handle=handle,
        type_=voice_type,
        style=algorithm,
        knobs={},
        instrument=inst,
        kit=None,
        meter=None,
    )
    resolved.gens[handle] = new_gen
    # Add the new handle to every part's gen list so it plays
    # immediately. The user can mute it per-part via the grid.
    for part in resolved.parts.values():
        if handle not in part.gen_handles:
            part.gen_handles.append(handle)
    # Track on Player for future round-trip work.
    added = getattr(app.player, "_added_gens", None)
    if added is None:
        app.player._added_gens = []
        added = app.player._added_gens
    added.append((handle, voice_type, algorithm, channel))
