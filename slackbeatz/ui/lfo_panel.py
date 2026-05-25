"""LFO management panel — list / view / (eventually) edit named LFOs.

Reached from the Arrangement screen's menu bar. Today this is a
read-only inspector — it shows the LFOs the .sb declares and the
``apply`` lines per part that bind them. Editing lands when the
arrangement-edit story matures past algorithm + knob into structural
changes (add/delete gens, add/delete LFOs, edit applies).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


class LfoPanel(tk.Frame):
    def __init__(self, app: "GuiApp") -> None:
        super().__init__(app.root)
        self.app = app
        self._build()

    def _build(self) -> None:
        bar = tk.Frame(self, relief="ridge", borderwidth=1)
        bar.pack(fill="x")
        ttk.Button(bar, text="← Arrangement", command=self._back).pack(side="left")
        tk.Label(bar, text="LFOs", font=("TkDefaultFont", 12, "bold")).pack(
            side="left", padx=12,
        )

        body = tk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        resolved = self._resolved()
        if resolved is None or not resolved.lfos:
            tk.Label(
                body,
                text="No LFOs declared. Add `lfo NAME shape=sine bars=8` to "
                     "your .sb and re-open to wire one up.",
                fg="gray", wraplength=560, justify="left",
            ).pack(pady=40)
            self._build_help(body)
            return

        # Per-LFO table.
        tk.Label(body, text="Declared LFOs:",
                 font=("TkDefaultFont", 10, "bold"), anchor="w").pack(fill="x")
        for spec in resolved.lfos.values():
            row = tk.Frame(body, relief="ridge", borderwidth=1, padx=4, pady=4)
            row.pack(fill="x", pady=2)
            tk.Label(
                row,
                text=f"{spec.name}  —  shape={spec.shape}  "
                     f"period_bars={spec.period_bars}  "
                     f"width={spec.width}  height={spec.height}",
                anchor="w",
            ).pack(side="left")

        # Per-part applications.
        tk.Label(body, text="Applications (which parts attach which LFO):",
                 font=("TkDefaultFont", 10, "bold"), anchor="w").pack(
            fill="x", pady=(12, 4),
        )
        for part_name, part in resolved.parts.items():
            if not part.lfo_applications:
                continue
            for app in part.lfo_applications:
                row = tk.Frame(body)
                row.pack(fill="x")
                tk.Label(
                    row,
                    text=f"  {part_name}:  apply {app.lfo_name} → "
                         f"{app.target.kind}:{app.target.ref}",
                    anchor="w",
                ).pack(side="left")

        self._build_help(body)

    def _build_help(self, body: tk.Misc) -> None:
        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=8)
        tk.Label(
            body,
            text=(
                "LFO target syntax (paste into .sb apply lines):\n"
                "  midi:ch:N/cc:M       — MIDI CC on channel N, controller M\n"
                "  surge:/param/...     — Surge XT parameter (live mode only)\n"
                "  pattern:HANDLE:KNOB  — pattern knob (engine support pending)\n"
                "  feel:TYPE:KNOB       — feel knob (engine support pending)\n"
            ),
            anchor="w", justify="left", font=("TkFixedFont", 9),
        ).pack(fill="x")

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
