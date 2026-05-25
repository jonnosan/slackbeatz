"""Modal dialog for creating a new song from a title phrase.

Opened from the Welcome screen's "+ New from title…" button. Carries:

* Title field (free text; pre-filled with a random keyword from the
  compose keyword banks so the user can hit Generate immediately).
* Style dropdown (``"Auto (derive from title)"`` plus the 9 explicit
  styles). Auto uses :func:`slackbeatz.compose.compose_from_text` to
  pick a style from keywords.
* Setup dropdown (bundled setups, defaulting to ``last_setup``).
* Seed field (optional int).
* Generate button — calls the ``on_generate`` callback.
"""

from __future__ import annotations

import random
import tkinter as tk
from tkinter import ttk
from typing import Callable, TYPE_CHECKING

from slackbeatz.compose import _STYLE_KEYWORDS  # module-private but stable
from slackbeatz.setup.loader import list_bundled_setups

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


# Explicit style picks the user can override "Auto" with.
_AUTO_LABEL = "Auto (derive from title)"
_EXPLICIT_STYLES = (
    "euclid", "deep_techno", "psytrance", "vaporwave", "acid",
    "dub_techno", "drum_and_bass", "garage", "lofi", "warm_analogue",
)


def _random_title() -> str:
    """Pick a random keyword pair from the style banks for a starting title."""
    try:
        words: list[str] = []
        for bank in _STYLE_KEYWORDS.values():
            # Each bank is {keyword: weight}; we just want the keys.
            words.extend(bank.keys())
        if len(words) < 2:
            return "Untitled song"
        a, b = random.sample(words, 2)
        return f"{a} {b}".replace("_", " ").title()
    except Exception:
        return "Untitled song"


class NewSongDialog:
    """Toplevel modal — collect title / style / setup / seed then call
    *on_generate(title, style_or_None, setup_name, seed)*.

    ``style_or_None`` is ``None`` when the user picked the Auto option
    (composer picks the style from title keywords).
    """

    def __init__(
        self,
        app: "GuiApp",
        *,
        on_generate: Callable[[str, str | None, str, int], None],
    ) -> None:
        self.app = app
        self.on_generate = on_generate
        self.win = tk.Toplevel(app.root)
        self.win.title("New song")
        self.win.transient(app.root)
        self.win.grab_set()
        self._build()

    def _build(self) -> None:
        row = 0

        # Title.
        tk.Label(self.win, text="Title:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=10, pady=8,
        )
        self.title_var = tk.StringVar(value=_random_title())
        title_entry = ttk.Entry(self.win, textvariable=self.title_var, width=40)
        title_entry.grid(row=row, column=1, padx=10, pady=8, sticky="ew")
        row += 1

        # Style.
        tk.Label(self.win, text="Style:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=10, pady=8,
        )
        self.style_var = tk.StringVar(value=_AUTO_LABEL)
        style_combo = ttk.Combobox(
            self.win, textvariable=self.style_var, state="readonly",
            values=(_AUTO_LABEL,) + _EXPLICIT_STYLES,
            width=37,
        )
        style_combo.grid(row=row, column=1, padx=10, pady=8, sticky="ew")
        row += 1

        # Setup.
        tk.Label(self.win, text="Setup:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=10, pady=8,
        )
        bundled = list_bundled_setups() or ["surge"]
        last = self.app.session.last_setup if self.app.session.last_setup in bundled else bundled[0]
        self.setup_var = tk.StringVar(value=last)
        setup_combo = ttk.Combobox(
            self.win, textvariable=self.setup_var, state="readonly",
            values=bundled, width=37,
        )
        setup_combo.grid(row=row, column=1, padx=10, pady=8, sticky="ew")
        row += 1

        # Seed.
        tk.Label(self.win, text="Seed:", anchor="w").grid(
            row=row, column=0, sticky="w", padx=10, pady=8,
        )
        self.seed_var = tk.StringVar(value="0")
        seed_entry = ttk.Entry(self.win, textvariable=self.seed_var, width=10)
        seed_entry.grid(row=row, column=1, padx=10, pady=8, sticky="w")
        row += 1

        # Buttons.
        btn_frame = tk.Frame(self.win)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=15, sticky="e", padx=10)
        ttk.Button(btn_frame, text="Cancel", command=self.win.destroy).pack(
            side="right", padx=5,
        )
        ttk.Button(btn_frame, text="Generate", command=self._on_generate).pack(
            side="right", padx=5,
        )

        self.win.columnconfigure(1, weight=1)
        title_entry.focus_set()
        # Enter key from any field triggers Generate.
        self.win.bind("<Return>", lambda _e: self._on_generate())

    def _on_generate(self) -> None:
        title = self.title_var.get().strip()
        if not title:
            return
        style_pick = self.style_var.get()
        style: str | None = None if style_pick == _AUTO_LABEL else style_pick
        setup_name = self.setup_var.get().strip()
        try:
            seed = int(self.seed_var.get())
        except ValueError:
            seed = 0
        self.win.destroy()
        self.on_generate(title, style, setup_name, seed)
