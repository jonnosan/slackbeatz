"""Setup editor screen — instruments + kits + mode picker.

Reads the resolved song's setup, lets the user inspect channel
routing, and exposes the mode choice (external / surge-standalone /
ableton) as a radio. Edit-and-save round-trip uses
:func:`slackbeatz.setup.serialize.emit_setup` — but the actual writes
go via the GUI's Save action on the Arrangement screen (which writes
the whole .sb).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from slackbeatz.setup.serialize import emit_setup

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


class SetupScreen(tk.Frame):
    def __init__(self, app: "GuiApp") -> None:
        super().__init__(app.root)
        self.app = app
        self._build()

    def _build(self) -> None:
        bar = tk.Frame(self, relief="ridge", borderwidth=1)
        bar.pack(fill="x")
        ttk.Button(bar, text="← Arrangement", command=self._back).pack(side="left")
        tk.Label(bar, text="Setup", font=("TkDefaultFont", 12, "bold")).pack(
            side="left", padx=12,
        )

        body = tk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        resolved = self._resolved()
        if resolved is None:
            tk.Label(body, text="(no song loaded)", fg="gray").pack(pady=40)
            return

        setup = resolved.setup
        tk.Label(body, text=f"Setup name: {setup.name}",
                 font=("TkDefaultFont", 11, "bold"),
                 anchor="w").pack(fill="x")

        # Mode picker.
        mode_row = tk.Frame(body)
        mode_row.pack(fill="x", pady=8)
        tk.Label(mode_row, text="Mode:").pack(side="left")
        current = getattr(setup, "mode", "external")
        self.mode_var = tk.StringVar(value=current)
        for label, value in (
            ("external", "external"),
            ("surge-standalone", "surge-standalone"),
            ("ableton", "ableton"),
        ):
            ttk.Radiobutton(
                mode_row, text=label, value=value,
                variable=self.mode_var,
                command=self._on_mode_change,
            ).pack(side="left", padx=4)
        tk.Label(
            mode_row,
            text=" (takes effect on next Play)",
            fg="gray",
        ).pack(side="left", padx=8)

        # "Open Ableton template" — only meaningful in ableton mode.
        # Picks Slackbeatz-<style>.als first (per the current song's
        # style_override) then falls back to Slackbeatz.als.
        if current == "ableton":
            ableton_row = tk.Frame(body)
            ableton_row.pack(fill="x", pady=4)
            ttk.Button(
                ableton_row, text="Open Ableton template",
                command=self._open_ableton_template,
            ).pack(side="left")
            tk.Label(
                ableton_row,
                text=(
                    " — opens Slackbeatz-<style>.als if present,"
                    " else Slackbeatz.als"
                ),
                fg="gray",
            ).pack(side="left", padx=8)

        # Instruments table.
        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=8)
        tk.Label(body, text="Instruments:", font=("TkDefaultFont", 10, "bold"),
                 anchor="w").pack(fill="x")
        inst_frame = tk.Frame(body)
        inst_frame.pack(fill="x", padx=12)
        header = tk.Frame(inst_frame)
        header.pack(fill="x")
        for label, w in (("name", 16), ("ch", 6), ("note", 8)):
            tk.Label(header, text=label, width=w, anchor="w",
                     font=("TkDefaultFont", 9, "bold")).pack(side="left")
        for inst in setup.instruments.values():
            r = tk.Frame(inst_frame)
            r.pack(fill="x")
            tk.Label(r, text=inst.name, width=16, anchor="w").pack(side="left")
            tk.Label(r, text=str(inst.channel), width=6, anchor="w").pack(side="left")
            note = "" if inst.note is None else str(inst.note)
            tk.Label(r, text=note, width=8, anchor="w").pack(side="left")

        # Kits table.
        if setup.kits:
            ttk.Separator(body, orient="horizontal").pack(fill="x", pady=8)
            tk.Label(body, text="Kits:", font=("TkDefaultFont", 10, "bold"),
                     anchor="w").pack(fill="x")
            for kit in setup.kits.values():
                tk.Label(body,
                         text=f"  {kit.name}  ch={kit.channel}  "
                              f"({len(kit.drum_notes)} drum mappings)",
                         anchor="w").pack(fill="x")

        # Source preview (emitted by serialiser) — useful for power
        # users who want to copy/paste into a .sb.
        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=8)
        tk.Label(body, text="Serialised form:",
                 font=("TkDefaultFont", 10, "bold"), anchor="w").pack(fill="x")
        txt = tk.Text(body, height=10, wrap="none", font=("TkFixedFont", 9))
        txt.insert("1.0", emit_setup(setup))
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=12, pady=4)

    def _open_ableton_template(self) -> None:
        """Open the per-style Ableton template (falling back to default)."""
        from slackbeatz.ui.ableton_template import open_ableton_template
        style = (
            getattr(self.app.player, "style_override", None)
            if self.app.player is not None else None
        )
        open_ableton_template(self, style)

    def _on_mode_change(self) -> None:
        """Mutate the in-memory Setup's mode. Save action picks it up."""
        resolved = self._resolved()
        if resolved is None:
            return
        # Setup is frozen at the class level; use object.__setattr__
        # to write the new mode.
        try:
            object.__setattr__(resolved.setup, "mode", self.mode_var.get())
        except Exception:
            pass

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
