"""Setup editor screen — instruments + kits + backend picker.

Reads the resolved song's setup, lets the user inspect channel
routing, and exposes the backend choice (`surge` vs `external`) as a
radio. Edit-and-save round-trip uses
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

        # Backend picker.
        backend_row = tk.Frame(body)
        backend_row.pack(fill="x", pady=8)
        tk.Label(backend_row, text="Backend:").pack(side="left")
        # Setup may not have `backend` (older bundled sets); default
        # to "external".
        current = getattr(setup, "backend", "external")
        self.backend_var = tk.StringVar(value=current)
        for label, value in (("surge", "surge"), ("external", "external")):
            ttk.Radiobutton(
                backend_row, text=label, value=value,
                variable=self.backend_var,
                command=self._on_backend_change,
            ).pack(side="left", padx=4)
        tk.Label(
            backend_row,
            text=" (takes effect on next Play)",
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

    def _on_backend_change(self) -> None:
        """Mutate the in-memory Setup's backend. Save action picks it up."""
        resolved = self._resolved()
        if resolved is None:
            return
        # Setup is frozen at the class level; use object.__setattr__
        # to write the new backend.
        try:
            object.__setattr__(resolved.setup, "backend", self.backend_var.get())
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
