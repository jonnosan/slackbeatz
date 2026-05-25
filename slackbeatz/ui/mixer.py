"""Mixer screen — per-channel volume / mute / solo strips.

Shown when the user clicks the Mixer button in the Arrangement
header. The mixer reads the resolved song's gens to enumerate active
channels and wires each strip to ``Player.toggle_mute`` /
``Player.toggle_solo``. Volume / pan / program are read-only displays
today — write-through wiring lands when the scheduler-side per-channel
CC7/CC10/program tracking is added (a follow-up to Phase D).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


class MixerScreen(tk.Frame):
    def __init__(self, app: "GuiApp") -> None:
        super().__init__(app.root)
        self.app = app
        self._build()

    def _build(self) -> None:
        bar = tk.Frame(self, relief="ridge", borderwidth=1)
        bar.pack(fill="x")
        ttk.Button(bar, text="← Arrangement", command=self._back).pack(side="left")
        tk.Label(bar, text="Mixer", font=("TkDefaultFont", 12, "bold")).pack(
            side="left", padx=12,
        )

        body = tk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        resolved = self._resolved()
        if resolved is None:
            tk.Label(body, text="(no song loaded)", fg="gray").pack(pady=40)
            return

        # Collect distinct channels in gen-order, with a label per
        # channel (handle list of that channel).
        channels: dict[int, list[str]] = {}
        for handle, gen in resolved.gens.items():
            if gen.instrument is None:
                continue
            channels.setdefault(gen.instrument.channel, []).append(handle)

        for ch in sorted(channels):
            self._build_strip(body, ch, channels[ch])

        # Master + back row.
        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=4)
        foot = tk.Frame(self)
        foot.pack(fill="x", padx=12, pady=8)
        tk.Label(foot, text="(volume / pan / program editing lands with the "
                            "per-channel CC tracking follow-up)",
                 fg="gray", anchor="w").pack(side="left")

    def _build_strip(self, parent: tk.Misc, channel: int, handles: list[str]) -> None:
        strip = tk.Frame(parent, relief="raised", borderwidth=1, padx=4, pady=4)
        strip.pack(side="left", padx=4, fill="y")
        tk.Label(strip, text=f"ch {channel}", font=("TkDefaultFont", 10, "bold")).pack()
        tk.Label(strip, text="\n".join(handles), font=("TkDefaultFont", 9),
                 fg="gray").pack()

        # Mute / Solo toggles wired to Player state.
        player = self.app.player
        muted = player is not None and channel in player._user_mutes
        soloed = player is not None and channel in player._solo

        mute_var = tk.BooleanVar(value=muted)
        solo_var = tk.BooleanVar(value=soloed)
        tk.Checkbutton(
            strip, text="Mute", variable=mute_var,
            command=lambda c=channel, v=mute_var: self._on_mute(c, v),
        ).pack()
        tk.Checkbutton(
            strip, text="Solo", variable=solo_var,
            command=lambda c=channel, v=solo_var: self._on_solo(c, v),
        ).pack()

    # ----- actions -----------------------------------------------------

    def _on_mute(self, channel: int, var: tk.BooleanVar) -> None:
        if self.app.player is None:
            return
        self.app.player.toggle_mute(channel)
        var.set(channel in self.app.player._user_mutes)

    def _on_solo(self, channel: int, var: tk.BooleanVar) -> None:
        if self.app.player is None:
            return
        self.app.player.toggle_solo(channel)
        var.set(channel in self.app.player._solo)

    def _back(self) -> None:
        from slackbeatz.ui.arrangement import ArrangementScreen
        self.app.transition_to(ArrangementScreen)

    def _resolved(self):
        if self.app.player is None:
            return None
        try:
            self.app.player._resolve_current()
        except Exception:
            return None
        return self.app.player.current_resolved
