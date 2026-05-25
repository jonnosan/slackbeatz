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

import sys
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
        # Live runtime — owns spawned FluidSynth + surge-xt-cli processes
        # for the current song. Set by welcome._build_player_from_file
        # via live_runtime.build_live_runtime; torn down on _on_close +
        # when switching songs.
        self.live_runtime: object | None = None
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
        """Open the Welcome screen and run the Tk main loop.

        Installs SIGINT / SIGTERM handlers + an atexit hook so any
        exit path — Ctrl+C in the terminal, ``kill`` from outside,
        crash inside Tk callbacks, etc. — still runs
        :meth:`_on_close` and tears down the live runtime (kills
        surge-xt-cli + FluidSynth subprocesses, releases virtual
        MIDI ports). Without this, ^C in the launching terminal
        leaves Surge instances running and squatting on their OSC
        ports, blocking the next launch with "Address already in use".
        """
        import atexit
        import signal
        from slackbeatz.ui.welcome import WelcomeScreen

        def _emergency_shutdown(*_a) -> None:
            # Idempotent — _on_close guards against double-shutdown
            # via the LiveRuntime._down flag.
            try:
                self._on_close()
            except Exception:
                pass

        atexit.register(_emergency_shutdown)
        # SIGINT lands on the Python main thread when ^C hits the
        # terminal; raising SystemExit from the handler unwinds Tk
        # cleanly and the atexit hook does the rest. SIGTERM is
        # what `kill <pid>` sends — same flow.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_a: (_emergency_shutdown(), sys.exit(0)))
            except (OSError, ValueError):
                # Some embedding contexts (non-main threads on macOS)
                # don't permit signal installation. Skip silently —
                # atexit + WM_DELETE_WINDOW still cover the GUI-quit
                # paths.
                pass

        self.transition_to(WelcomeScreen)
        # Poll Tk on a short interval so SIGINT can interrupt the
        # event loop on Python 3.10+ where signal handlers don't
        # always wake mainloop() until the next event. Without this,
        # ^C only fires after the next mouse move / key press.
        def _signal_pump():
            self.root.after(100, _signal_pump)
        self.root.after(100, _signal_pump)
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
        # Shut down the live runtime (kills surge-xt-cli + FluidSynth
        # subprocesses, closes virtual MIDI ports). Idempotent — safe
        # to call from WM_DELETE_WINDOW, SIGINT/SIGTERM handlers,
        # and atexit, in any order.
        if self.live_runtime is not None:
            try:
                self.live_runtime.shutdown()
            except Exception:
                pass
            # Null out so a subsequent call (atexit after WM_DELETE)
            # short-circuits cleanly instead of double-firing the
            # shutdown.
            self.live_runtime = None
        elif self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass
            self.player = None
        try:
            save_session(self.session)
        except OSError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            # Already destroyed (re-entrant call via signal handler).
            pass


def launch() -> int:
    """Bare-``slackbeatz`` entry point. Opens Welcome, blocks until close."""
    app = GuiApp()
    app.run()
    return 0
