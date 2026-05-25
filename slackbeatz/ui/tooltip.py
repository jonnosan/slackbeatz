"""Tiny tooltip helper — Tk has none built in.

Used by the scope-dot widgets in :mod:`slackbeatz.ui.scope_drilldown`
to show the full cascade chain on hover. Generic enough to reuse on
any other widget that wants explanation-on-hover.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional


class Tooltip:
    """Bind a text tooltip to a widget.

    Shows a small Toplevel window near the widget on ``<Enter>``,
    destroys it on ``<Leave>``. Multi-line text supported (``\\n``).
    """

    def __init__(self, widget: tk.Widget, text: str, *, delay_ms: int = 350) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._scheduled: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def set_text(self, text: str) -> None:
        """Update the tooltip text — useful when the underlying value
        the tooltip describes changes (e.g. scope dots re-render)."""
        self.text = text
        if self._tip is not None:
            # Refresh visible tooltip in place.
            for child in self._tip.winfo_children():
                child.destroy()
            tk.Label(
                self._tip, text=self.text, justify="left",
                background="#ffffe0", relief="solid", borderwidth=1,
                font=("TkDefaultFont", 9), padx=6, pady=3,
            ).pack()

    def _on_enter(self, _event) -> None:
        self._cancel_scheduled()
        self._scheduled = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, _event) -> None:
        self._cancel_scheduled()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _cancel_scheduled(self) -> None:
        if self._scheduled is not None:
            try:
                self.widget.after_cancel(self._scheduled)
            except Exception:
                pass
            self._scheduled = None

    def _show(self) -> None:
        self._scheduled = None
        if self._tip is not None:
            return
        # Position near the cursor, offset down-right.
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)  # no decorations
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tip, text=self.text, justify="left",
            background="#ffffe0", relief="solid", borderwidth=1,
            font=("TkDefaultFont", 9), padx=6, pady=3,
        ).pack()
        self._tip = tip
