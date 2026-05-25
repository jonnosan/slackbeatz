"""Welcome screen — the first thing shown when bare ``slackbeatz`` runs.

Two big buttons (New from title / Open .sb…) plus a recents list.
Picking any of them transitions to the Arrangement screen with a
loaded Player.

Layout (mirrors the redesign plan's ASCII mockup):

    +---------------------------------------------------------------+
    |  slackbeatz                                                   |
    |                                                               |
    |     [  + New from title  ]   [  Open .sb...  ]                |
    |                                                               |
    |  Recent:                                                      |
    |   - dusty_swing_in_amber.sb   2026-05-22                      |
    |   - techno_at_4am.sb          2026-05-19                      |
    |                                                               |
    |                                          [ Quit ]             |
    +---------------------------------------------------------------+
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


class WelcomeScreen(tk.Frame):
    """Welcome content. Reads recents / last_setup from
    :class:`~slackbeatz.ui.state.SessionState`.
    """

    def __init__(self, app: "GuiApp") -> None:
        super().__init__(app.root)
        self.app = app
        self._build()

    # ----- layout ------------------------------------------------------

    def _build(self) -> None:
        title = tk.Label(self, text="slackbeatz", font=("TkDefaultFont", 24, "bold"))
        title.pack(pady=(40, 30))

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=(0, 30))

        new_btn = ttk.Button(
            btn_frame, text="+ New from title…",
            command=self._on_new, width=22,
        )
        new_btn.grid(row=0, column=0, padx=10)

        open_btn = ttk.Button(
            btn_frame, text="Open .sb…",
            command=self._on_open, width=22,
        )
        open_btn.grid(row=0, column=1, padx=10)

        # Recents list — only shown if there are any.
        if self.app.session.recents:
            rec_label = tk.Label(self, text="Recent:", anchor="w",
                                 font=("TkDefaultFont", 11, "bold"))
            rec_label.pack(fill="x", padx=40, pady=(20, 5))
            rec_frame = tk.Frame(self)
            rec_frame.pack(fill="both", expand=True, padx=40)
            for path_str in self.app.session.recents:
                p = Path(path_str)
                row = tk.Frame(rec_frame)
                row.pack(fill="x", pady=2)
                btn = tk.Label(
                    row, text=f"  • {p.name}", anchor="w",
                    fg="blue", cursor="hand2",
                )
                btn.pack(side="left", fill="x", expand=True)
                # late-bind path via default-arg trick
                btn.bind("<Button-1>", lambda _e, sp=path_str: self._open_path(Path(sp)))
                meta = tk.Label(row, text=str(p.parent), fg="gray", anchor="e")
                meta.pack(side="right")
        else:
            empty = tk.Label(
                self,
                text="No recent files. Create or open one above to get started.",
                fg="gray",
            )
            empty.pack(pady=20)

        # Bottom: Quit button.
        bot = tk.Frame(self)
        bot.pack(side="bottom", fill="x", padx=20, pady=20)
        quit_btn = ttk.Button(bot, text="Quit", command=self.app.root.destroy)
        quit_btn.pack(side="right")

    # ----- actions -----------------------------------------------------

    def _on_new(self) -> None:
        from slackbeatz.ui.new_song_dialog import NewSongDialog
        NewSongDialog(self.app, on_generate=self._open_composed_song)

    def _on_open(self) -> None:
        path_str = filedialog.askopenfilename(
            title="Open slackbeatz file",
            filetypes=[("slackbeatz", "*.sb"), ("All files", "*.*")],
        )
        if not path_str:
            return
        self._open_path(Path(path_str))

    def _open_path(self, path: Path) -> None:
        from slackbeatz.ui.arrangement import ArrangementScreen
        self.app.remember_opened(path)
        self._build_player_from_file(path)
        self.app.transition_to(ArrangementScreen)

    def _open_composed_song(self, title: str, style: str | None,
                            setup_name: str, seed: int) -> None:
        from slackbeatz.compose import compose_from_text
        from slackbeatz.ui.arrangement import ArrangementScreen

        # Compose to a temp .sb so the Player has a path to load.
        import tempfile
        sb_content = compose_from_text(
            title,
            style_override=style,
            seed_offset=seed,
        )
        with tempfile.NamedTemporaryFile(
            suffix=".sb", delete=False, mode="w", encoding="utf-8",
        ) as tf:
            tf.write(sb_content)
            tmp_path = Path(tf.name)
        # Remember the chosen setup for next launch.
        self.app.session.last_setup = setup_name
        self._build_player_from_file(tmp_path, setup_arg=setup_name)
        # Switch the Player into PHRASE mode so Song → Reroll +
        # the style picker re-compose from the title with a new
        # seed_offset / style_override instead of re-reading the
        # frozen temp file. The temp file's only job was to let
        # build_live_runtime detect the song's setup + spawn the
        # right backends; subsequent resolves recompose live.
        if self.app.player is not None:
            self.app.player.current_phrase = title
            self.app.player.current_song_path = None
            self.app.player.style_override = style
            self.app.player.seed_offset = seed
        self.app.transition_to(ArrangementScreen)

    def _build_player_from_file(self, path: Path, *,
                                setup_arg: str | None = None) -> None:
        """Bring up the live audio runtime + bind to ``app.player``.

        Uses :func:`slackbeatz.live_runtime.build_live_runtime` so the
        same setup-aware spawn chain as the CLI runs: when the song's
        setup has ``backend surge``, FluidSynth + one surge-xt-cli per
        pitched channel are spawned automatically. Otherwise we open
        a single external MIDI port (auto-falls-back to a virtual
        ``slackbeatz`` port when none exist).

        Shutting down the previous runtime first means switching songs
        cleanly releases ports + subprocesses — no orphaned synth
        instances stacking up across loads.
        """
        from slackbeatz.live_runtime import build_live_runtime, LiveRuntimeError
        prev = getattr(self.app, "live_runtime", None)
        try:
            # Pass the previous runtime so build_live_runtime can
            # reuse its surge-xt-cli + FluidSynth + sampler when the
            # new song uses the same setup (the common case for
            # switching .sb files). Saves the multi-second surge
            # spawn delay per song change. ``reuse_from`` marks the
            # previous runtime as transferred on success so the
            # subsequent prev.shutdown() below short-circuits the
            # child-process teardown.
            runtime = build_live_runtime(
                path,
                setup_arg=setup_arg or self.app.session.last_setup,
                reuse_from=prev,
            )
        except LiveRuntimeError as e:
            # Surface to the user via a dialog — but still attempt a
            # bare-MIDI Player so the GUI can at least browse the song.
            from tkinter import messagebox
            messagebox.showerror(
                "slackbeatz: audio setup error",
                f"Couldn't bring up the live audio chain.\n\n{e}\n\n"
                "Play will be disabled. You can still browse + edit "
                "the song.",
            )
            from slackbeatz.player import Player
            self.app.player = Player(
                port_name="slackbeatz",
                setup_arg=setup_arg or self.app.session.last_setup,
                osc_routing=False,
            )
            self.app.player.load_file(path)
            self.app.live_runtime = None
            return
        # Now tear down the previous runtime. If build_live_runtime
        # reused it, ``_transferred`` is True so this only stops the
        # OLD player thread (which build_live_runtime already did) —
        # surge/fluidsynth/sampler stay alive under the new runtime.
        if prev is not None and prev is not runtime:
            try:
                prev.shutdown()
            except Exception:
                pass
        self.app.player = runtime.player
        self.app.live_runtime = runtime
