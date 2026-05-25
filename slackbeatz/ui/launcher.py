"""GUI launcher — owns the Tk root and screen transitions.

Phase E entry point. The launcher creates a single Tk root with one
child :class:`tkinter.Frame` at a time (the current screen), swapping
between Welcome / Arrangement / Mixer / Setup as the user navigates.

The Player and SessionState instances live on the launcher so they
survive screen transitions: the Welcome screen creates a Player when
the user clicks Generate / Open, the Arrangement screen tweaks it, the
Setup screen swaps its setup, and so on.

The old :mod:`slackbeatz.gui` notebook GUI still exists and is the
fallback for ``--gui`` on a playing CLI session. Bare ``slackbeatz``
(no subcommand) launches *this* flow instead — see
:func:`slackbeatz.cli.cmd_gui`.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from slackbeatz.ui.state import (
    SessionState,
    load as load_session,
    note_opened,
    prune_missing_recents,
    save as save_session,
)

if TYPE_CHECKING:
    from slackbeatz.player import Player


class GuiApp:
    """Singleton-ish wrapper around the Tk root + current screen.

    Created once per ``slackbeatz`` invocation. Screens call
    :meth:`transition_to` to swap themselves out; the launcher owns
    the destruction of the previous screen so frames don't pile up
    in memory.
    """

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("slackbeatz")
        self.root.minsize(900, 600)
        self.session = load_session()
        prune_missing_recents(self.session)
        self.player: Optional["Player"] = None
        self._current_frame: Optional[tk.Frame] = None
        # Auto-save the session on close.
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def transition_to(self, screen_factory: Callable[["GuiApp"], tk.Frame]) -> None:
        """Swap the current screen for the one *screen_factory* builds.

        *screen_factory* takes the :class:`GuiApp` (so the screen can
        call :meth:`transition_to` itself and read the session) and
        returns a freshly-built :class:`tkinter.Frame`. The previous
        frame is destroyed.
        """
        if self._current_frame is not None:
            self._current_frame.destroy()
            self._current_frame = None
        frame = screen_factory(self)
        frame.pack(fill="both", expand=True)
        self._current_frame = frame

    def run(self) -> None:
        """Open the Welcome screen and run the Tk main loop."""
        from slackbeatz.ui.welcome import WelcomeScreen
        self.transition_to(WelcomeScreen)
        self.root.mainloop()

    def remember_opened(self, path: Path) -> None:
        """Record that *path* was just opened — updates recents + saves."""
        note_opened(self.session, path)
        try:
            save_session(self.session)
        except OSError:
            # State-file write failure shouldn't crash the GUI; we
            # just lose the recents entry for next launch.
            pass

    def _on_close(self) -> None:
        if self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass
        try:
            save_session(self.session)
        except OSError:
            pass
        self.root.destroy()


def launch() -> int:
    """Bare-``slackbeatz`` entry point. Opens Welcome, blocks until close."""
    app = GuiApp()
    app.run()
    return 0
