"""Surge factory-patch picker dialog — opened from the Mixer.

Wraps :func:`slackbeatz.surge_host.list_factory_patches` +
``SurgeInstance.load_patch`` in a small modal Toplevel so the user
can browse the factory patch tree from any context (drilldown is
inline; this dialog is what the mixer strip uses).

Same Role/All toggle as the drilldown's inline picker — defaults to
role-filtered (e.g. lead → Leads/) and toggles to all categories
on demand.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING

from slackbeatz.surge_host import (
    list_factory_patches, patch_category_for_role, resolve_factory_patch,
)

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


class PatchPickerDialog:
    def __init__(self, app: "GuiApp", surge_instance) -> None:
        self.app = app
        self.inst = surge_instance
        self.win = tk.Toplevel(app.root)
        self.win.title(
            f"Patch — {surge_instance.config.role} "
            f"(ch {surge_instance.config.channel_1idx})"
        )
        self.win.transient(app.root)
        self.win.grab_set()
        self._mode = tk.StringVar(value="role")
        self._patches_by_display: dict[str, str] = {}
        self._build()

    def _build(self) -> None:
        body = tk.Frame(self.win)
        body.pack(fill="both", expand=True, padx=12, pady=10)

        role = self.inst.config.role
        category = patch_category_for_role(role)

        ctrl = tk.Frame(body)
        ctrl.pack(fill="x", pady=(0, 6))
        tk.Label(ctrl, text="Patch:", font=("TkDefaultFont", 10, "bold")
                 ).pack(side="left", padx=(0, 4))

        self.patch_var = tk.StringVar(value="")
        combo = ttk.Combobox(
            ctrl, textvariable=self.patch_var, state="readonly",
            values=[], width=46,
        )
        combo.pack(side="left", padx=4)

        def _load_current(*_a):
            display = self.patch_var.get()
            rel = self._patches_by_display.get(display)
            if rel is None:
                return
            path = resolve_factory_patch(rel)
            if path is None:
                return
            try:
                self.inst.load_patch(path)
            except Exception:
                pass
            # Kill + re-trigger held notes so pad / drone voices
            # switch to the new patch without waiting for the next
            # natural note_off (which can be many bars away).
            try:
                ch = self.inst.config.channel_1idx
                player = getattr(self.app, "player", None)
                if player is not None:
                    player.retrigger_held_notes_on_channel(ch)
            except Exception:
                pass

        def _step(delta: int):
            choices = list(self._patches_by_display.keys())
            if not choices:
                return
            try:
                idx = choices.index(self.patch_var.get())
            except ValueError:
                idx = 0
            new_idx = (idx + delta) % len(choices)
            self.patch_var.set(choices[new_idx])
            _load_current()

        ttk.Button(ctrl, text="↑", width=2,
                   command=lambda: _step(-1)).pack(side="left", padx=0)
        ttk.Button(ctrl, text="↓", width=2,
                   command=lambda: _step(1)).pack(side="left", padx=0)

        def _refresh(*_a):
            chosen_cat = category if self._mode.get() == "role" else None
            patches = list_factory_patches(chosen_cat)
            self._patches_by_display = {d: rel for d, rel in patches}
            choices = list(self._patches_by_display.keys())
            combo["values"] = choices
            cur = self.patch_var.get()
            if cur not in self._patches_by_display:
                rel = self.inst.current_patch_rel or ""
                stem = Path(rel).stem if rel else ""
                if stem in self._patches_by_display:
                    self.patch_var.set(stem)
                elif rel and rel[:-4] in self._patches_by_display:
                    self.patch_var.set(rel[:-4])
                elif choices:
                    self.patch_var.set(choices[0])
                else:
                    self.patch_var.set("")

        _refresh()
        combo.bind("<<ComboboxSelected>>", _load_current)

        def _toggle_mode():
            self._mode.set("all" if self._mode.get() == "role" else "role")
            mode_btn.config(text=("All" if self._mode.get() == "all" else "Role"))
            _refresh()
        mode_btn = ttk.Button(
            ctrl, text="Role" if self._mode.get() == "role" else "All",
            width=4, command=_toggle_mode,
        )
        mode_btn.pack(side="left", padx=2)

        tk.Label(
            body,
            text="Selecting a patch loads it live on this channel's "
                 "surge-xt-cli (audible immediately). Closing the "
                 "dialog leaves your selection active.",
            fg="gray", wraplength=460, justify="left",
        ).pack(fill="x", pady=(8, 0))

        btn_row = tk.Frame(body)
        btn_row.pack(fill="x", pady=(10, 0))
        ttk.Button(btn_row, text="Close", command=self.win.destroy
                   ).pack(side="right")
