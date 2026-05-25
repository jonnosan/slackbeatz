"""Arrangement screen — the primary working surface for the redesign.

Phase E.2 wires up the screen frame, header, voice×part grid, detail
pane host, and persistent transport. The three-tier Algorithm /
Pattern / Feel drilldown (Phase E.3) plugs into the detail pane host;
add-voice picker (E.5) plugs into the grid's "+ Voice" affordance.

Layout (matches the redesign plan's ASCII mockup, simplified for
Tk + ttk widgets — no fancy CSS):

    +---------------------------------------------------------------+
    | File  Song  View                          [Mixer] [Setup]     |
    +---------------------------------------------------------------+
    | <title>   Style: <s>   Key: <k>   BPM: <b>   Seed: <n>        |
    +---------------------------------------------------------------+
    | VOICE   | intro | verse | chorus | bridge | outro | + Part    |
    +---------+-------+-------+--------+--------+-------+-----------+
    | rhythm  |  X    |  X    |   X    |        |   X   |           |
    | bass    |       |  X    |   X    |   X    |       |           |
    | ...                                                           |
    | + Voice                                                       |
    +---------------------------------------------------------------+
    | Selected: bass @ verse        Scope: ( ) Part  Voice  Song    |
    +---------------------------------------------------------------+
    | <detail pane host — drilldown widget lands here>              |
    +---------------------------------------------------------------+
    | [Play] [Stop]    00:00 / 01:48    bar 1/32   BPM 128          |
    +---------------------------------------------------------------+
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


class ArrangementScreen(tk.Frame):
    """Top-level frame holding the arrangement surface.

    On creation, reads ``app.player.current_resolved`` (the resolved
    song the Player loaded) and binds widgets to its parts + gens.
    Edits trigger ``app.player.set_*`` calls; the Player owns the
    save-state round-trip.
    """

    def __init__(self, app: "GuiApp") -> None:
        super().__init__(app.root)
        self.app = app
        self.selected: tuple[str, str] | None = None  # (voice_handle, part_name)
        self.scope_var = tk.StringVar(value="part")
        self._build()

    # ----- layout ------------------------------------------------------

    def _build(self) -> None:
        self._build_menubar()
        self._build_warnings_banner()
        self._build_header()
        self._build_grid()
        self._build_detail_host()
        self._build_transport()

    def _build_warnings_banner(self) -> None:
        """Phase F — surface any non-fatal load warnings as a banner.

        Uses :mod:`slackbeatz.ui.diagnostics` to walk the parsed AST
        for known issues (unknown style / setup / algorithm; duplicate
        handles). Banner is yellow-tinted, collapsible, and only
        appears when there's something to show.
        """
        warnings = self._collect_warnings()
        if not warnings:
            return
        from slackbeatz.ui.diagnostics import format_warning_summary
        banner = tk.Frame(self, bg="#fff8c4", relief="solid", borderwidth=1)
        banner.pack(fill="x", padx=4, pady=2)
        summary = format_warning_summary(warnings)
        tk.Label(
            banner, text=f"⚠ {summary}",
            bg="#fff8c4", anchor="w", font=("TkDefaultFont", 10, "bold"),
        ).pack(side="left", padx=8, pady=4)
        ttk.Button(
            banner, text="Details…",
            command=lambda ws=warnings: self._show_warning_details(ws),
        ).pack(side="right", padx=4, pady=2)

    def _collect_warnings(self) -> list:
        from slackbeatz.dsl.parser import parse_file
        from slackbeatz.ui.diagnostics import check_for_warnings
        if self.app.player is None or self.app.player.current_song_path is None:
            return []
        try:
            file_ast = parse_file(self.app.player.current_song_path)
        except Exception:
            return []
        return check_for_warnings(file_ast)

    def _show_warning_details(self, warnings: list) -> None:
        from tkinter import scrolledtext
        win = tk.Toplevel(self.app.root)
        win.title("Session warnings")
        win.transient(self.app.root)
        body = scrolledtext.ScrolledText(
            win, width=80, height=12, wrap="word", font=("TkFixedFont", 9),
        )
        for w in warnings:
            body.insert("end", f"line {w.line}: [{w.kind}] {w.message}\n\n")
        body.config(state="disabled")
        body.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=4)

    def _build_menubar(self) -> None:
        bar = tk.Frame(self, relief="ridge", borderwidth=1)
        bar.pack(fill="x")
        # File menu via plain buttons (avoids platform-specific menubar quirks).
        ttk.Button(bar, text="File ▾", command=self._show_file_menu).pack(side="left")
        ttk.Button(bar, text="Song ▾", command=self._show_song_menu).pack(side="left")
        ttk.Button(bar, text="View ▾", command=self._show_view_menu).pack(side="left")
        # Right-aligned screen swap buttons.
        ttk.Button(bar, text="Setup", command=self._goto_setup).pack(side="right")
        ttk.Button(bar, text="LFOs", command=self._goto_lfos).pack(side="right")
        ttk.Button(bar, text="Mixer", command=self._goto_mixer).pack(side="right")

    def _build_header(self) -> None:
        head = tk.Frame(self)
        head.pack(fill="x", padx=12, pady=8)
        resolved = self._resolved()
        title_text = f"“{resolved.name}”" if resolved else "(no song loaded)"
        tk.Label(head, text=title_text, font=("TkDefaultFont", 14, "bold")).pack(
            side="left", padx=(0, 16),
        )
        if resolved is not None:
            tk.Label(head, text=f"Key: {resolved.key}").pack(side="left", padx=4)
            tk.Label(head, text=f"BPM: {resolved.tempo}").pack(side="left", padx=4)
            tk.Label(head, text=f"Seed: {resolved.seed}").pack(side="left", padx=4)
            tk.Label(head, text=f"Setup: {resolved.setup.name}").pack(side="left", padx=4)

    def _build_grid(self) -> None:
        """Voice × Part toggle grid.

        Cells show a filled marker if the voice plays in that part. A
        dot suffix (``●``) flags a part-scope override (algorithm
        or knob) — a tiny visual cue without claiming any extra width.
        """
        wrap = tk.Frame(self, relief="sunken", borderwidth=1)
        wrap.pack(fill="both", expand=True, padx=12, pady=4)

        resolved = self._resolved()
        if resolved is None:
            tk.Label(wrap, text="(no song loaded)", fg="gray").pack(pady=20)
            return

        # Header row: blank corner + one column per part + "+ Part".
        # Each part column is a 2-row stack — part name on top, then
        # a ▶ play-from-here + 🔁 loop-this-part button row, so the
        # user can audition individual parts without scrubbing the
        # transport (a recurring ask during the redesign).
        header = tk.Frame(wrap)
        header.pack(fill="x")
        tk.Label(header, text="VOICE", width=10, anchor="w",
                 font=("TkDefaultFont", 10, "bold")).pack(side="left")
        arrangement = resolved.arrangement
        loop_idx = self._current_loop_position()
        for part_name in self._arrangement_unique(resolved):
            col = tk.Frame(header)
            col.pack(side="left", padx=1)
            tk.Label(col, text=part_name, width=10, anchor="center",
                     font=("TkDefaultFont", 10, "bold")).pack()
            # Look up the FIRST arrangement position matching this part
            # name. The Voice × Part grid dedupes; the Player's
            # jump/loop APIs take a position INDEX into the full
            # arrangement, so we resolve back here.
            try:
                pos = arrangement.index(part_name)
            except ValueError:
                pos = None
            btn_row = tk.Frame(col)
            btn_row.pack()
            ttk.Button(
                btn_row, text="▶", width=2,
                command=lambda i=pos: self._on_part_play(i),
                state=("normal" if pos is not None else "disabled"),
            ).pack(side="left", padx=0)
            loop_text = "🔁"
            ttk.Button(
                btn_row, text=loop_text, width=2,
                command=lambda i=pos: self._on_part_loop_toggle(i),
                state=("normal" if pos is not None else "disabled"),
                style=("Accent.TButton" if pos == loop_idx else "TButton"),
            ).pack(side="left", padx=0)
        ttk.Button(header, text="+ Part", width=8,
                   command=self._on_add_part).pack(side="left", padx=4)

        # One row per voice handle.
        seen_handles: list[str] = []
        for handle in resolved.gens:
            if handle in seen_handles:
                continue
            seen_handles.append(handle)
            self._build_voice_row(wrap, handle, resolved)

        # "+ Voice" row — opens the voice picker.
        addrow = tk.Frame(wrap)
        addrow.pack(fill="x")
        ttk.Button(addrow, text="+ Voice", width=10,
                   command=self._on_add_voice).pack(side="left", pady=4)

    def _build_voice_row(self, parent, handle: str, resolved) -> None:
        row = tk.Frame(parent)
        row.pack(fill="x", pady=1)
        gen = resolved.gens[handle]
        tk.Label(row, text=handle, width=10, anchor="w").pack(side="left")
        for part_name in self._arrangement_unique(resolved):
            part = resolved.parts[part_name]
            active = handle in part.gen_handles
            has_override = (
                handle in part.algorithm_overrides
                or handle in part.knob_overrides
            )
            marker = "████" if active else "░░░░"
            if has_override:
                marker += "●"  # bullet — flag override
            cell = tk.Label(
                row, text=marker, width=10, anchor="center",
                fg=("blue" if has_override else "black"),
                cursor="hand2" if active else "arrow",
            )
            cell.pack(side="left", padx=1)
            if active:
                cell.bind(
                    "<Button-1>",
                    lambda _e, h=handle, p=part_name: self._select_cell(h, p),
                )

    def _build_detail_host(self) -> None:
        """Placeholder for the detail pane. Phase E.3's
        scope-drilldown widget will mount here when a cell is
        selected."""
        sel = tk.Frame(self, relief="ridge", borderwidth=1)
        sel.pack(fill="x", padx=12, pady=(4, 4))
        self.sel_label = tk.Label(sel, text="(click a voice × part cell to edit)",
                                  fg="gray", anchor="w")
        self.sel_label.pack(side="left", padx=8, pady=4)
        # Scope picker — only relevant once something is selected.
        tk.Label(sel, text="Scope:").pack(side="left", padx=(16, 4))
        for label, value in (("Part", "part"), ("Voice", "voice"), ("Song", "song")):
            ttk.Radiobutton(
                sel, text=label, value=value, variable=self.scope_var,
                command=self._on_scope_change,
            ).pack(side="left", padx=2)

        self.detail_host = tk.Frame(self, relief="sunken", borderwidth=1)
        self.detail_host.pack(fill="both", expand=True, padx=12, pady=4)
        self.detail_widget: tk.Frame | None = None
        self._render_detail_placeholder()

    def _render_detail_placeholder(self) -> None:
        if self.detail_widget is not None:
            self.detail_widget.destroy()
        ph = tk.Frame(self.detail_host)
        ph.pack(fill="both", expand=True)
        tk.Label(ph, text="Algorithm / Pattern / Feel drilldown lands here.",
                 fg="gray").pack(pady=40)
        self.detail_widget = ph

    def _build_transport(self) -> None:
        bar = tk.Frame(self, relief="ridge", borderwidth=1)
        bar.pack(side="bottom", fill="x")
        self.play_btn = ttk.Button(bar, text="▶ Play", command=self._on_play)
        self.play_btn.pack(side="left", padx=4, pady=4)
        self.stop_btn = ttk.Button(bar, text="■ Stop", command=self._on_stop)
        self.stop_btn.pack(side="left", padx=4, pady=4)
        resolved = self._resolved()
        if resolved is not None:
            tk.Label(bar, text=f"  BPM {resolved.tempo}").pack(side="left", padx=8)
            tk.Label(bar, text=f"Setup: {resolved.setup.name}",
                     fg="gray").pack(side="right", padx=8)

    # ----- actions -----------------------------------------------------

    def _select_cell(self, handle: str, part_name: str) -> None:
        self.selected = (handle, part_name)
        self.sel_label.config(text=f"Selected: {handle} @ {part_name}")
        # Future: scope picker auto-jumps to most-specific existing override.
        self._render_drilldown()

    def _render_drilldown(self) -> None:
        """Mount the scope-drilldown widget for the current selection."""
        if self.detail_widget is not None:
            self.detail_widget.destroy()
        if self.selected is None:
            self._render_detail_placeholder()
            return
        from slackbeatz.ui.scope_drilldown import ScopeDrilldown
        handle, part_name = self.selected
        self.detail_widget = ScopeDrilldown(
            self.detail_host,
            app=self.app,
            voice_handle=handle,
            part_name=part_name,
            scope=self.scope_var.get(),
            on_change=self._on_drilldown_change,
        )
        self.detail_widget.pack(fill="both", expand=True)

    def _on_scope_change(self) -> None:
        # Re-render so the drilldown reflects the new scope.
        if self.selected is not None:
            self._render_drilldown()

    def _on_drilldown_change(self) -> None:
        # A knob / algorithm change happened — rebuild the grid so
        # override markers refresh.
        self._refresh_grid()

    def _refresh_grid(self) -> None:
        # Cheap full rebuild — the grid is small.
        for child in self.winfo_children():
            child.destroy()
        self._build()

    def _on_add_voice(self) -> None:
        from slackbeatz.ui.voice_picker import open_voice_picker
        open_voice_picker(self.app, on_added=self._refresh_grid)

    def _on_add_part(self) -> None:
        # Phase E.2 placeholder — actual part-insertion editing lands
        # with the arrangement-edit work that follows the MVP.
        tk.messagebox.showinfo(
            "Add Part",
            "Adding parts via the UI is on the roadmap — for now, edit "
            "the .sb file and re-open.",
        ) if hasattr(tk, "messagebox") else None

    def _on_play(self) -> None:
        if self.app.player is None:
            return
        try:
            self.app.player.play()
        except Exception as e:
            # Don't let synth-spawn errors crash the GUI; surface them
            # via the title for now (a proper status bar lands in F).
            self.app.root.title(f"slackbeatz — play error: {e}")

    def _on_stop(self) -> None:
        if self.app.player is None:
            return
        try:
            self.app.player.stop()
        except Exception:
            pass

    def _goto_mixer(self) -> None:
        from slackbeatz.ui.mixer import MixerScreen
        self.app.transition_to(MixerScreen)

    def _goto_setup(self) -> None:
        from slackbeatz.ui.setup_editor import SetupScreen
        self.app.transition_to(SetupScreen)

    def _goto_lfos(self) -> None:
        from slackbeatz.ui.lfo_panel import LfoPanel
        self.app.transition_to(LfoPanel)

    def _show_file_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="New from title…", command=self._new_song)
        menu.add_command(label="Open .sb…", command=self._open_file)
        menu.add_separator()
        menu.add_command(label="Save", command=self._save)
        menu.add_command(label="Save As…", command=self._save_as)
        menu.add_separator()
        menu.add_command(label="Back to Welcome", command=self._back_to_welcome)
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _show_song_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Re-roll (new seed)", command=self._reroll)
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _show_view_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Mixer", command=self._goto_mixer)
        menu.add_command(label="Setup", command=self._goto_setup)
        menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _new_song(self) -> None:
        # Stop the current player + return to Welcome to compose a new one.
        self._back_to_welcome()
        from slackbeatz.ui.new_song_dialog import NewSongDialog
        from slackbeatz.ui.welcome import WelcomeScreen
        # _back_to_welcome already transitioned; current screen is now Welcome.
        ws = self.app._current_frame
        if isinstance(ws, WelcomeScreen):
            NewSongDialog(self.app, on_generate=ws._open_composed_song)

    def _open_file(self) -> None:
        from tkinter import filedialog
        from pathlib import Path
        path_str = filedialog.askopenfilename(
            title="Open slackbeatz file",
            filetypes=[("slackbeatz", "*.sb"), ("All files", "*.*")],
        )
        if not path_str:
            return
        self._reload(Path(path_str))

    def _save(self) -> None:
        if self.app.player is None or self.app.player.current_song_path is None:
            self._save_as()
            return
        self.app.player.save_state(self.app.player.current_song_path)

    def _save_as(self) -> None:
        from tkinter import filedialog
        path_str = filedialog.asksaveasfilename(
            title="Save slackbeatz file as…",
            defaultextension=".sb",
            filetypes=[("slackbeatz", "*.sb")],
        )
        if not path_str:
            return
        if self.app.player is not None:
            self.app.player.save_state(path_str)
            from pathlib import Path
            self.app.remember_opened(Path(path_str))

    def _back_to_welcome(self) -> None:
        from slackbeatz.ui.welcome import WelcomeScreen
        if self.app.player is not None:
            try:
                self.app.player.stop()
            except Exception:
                pass
        self.app.transition_to(WelcomeScreen)

    def _reroll(self) -> None:
        if self.app.player is None:
            return
        self.app.player.seed_offset = (self.app.player.seed_offset or 0) + 1
        try:
            self.app.player._resolve_current()
        except Exception:
            pass
        self._refresh_grid()

    def _reload(self, path) -> None:
        from slackbeatz.player import Player
        if self.app.player is not None:
            try:
                self.app.player.stop()
            except Exception:
                pass
        self.app.player = Player(
            port_name="slackbeatz",
            setup_arg=self.app.session.last_setup,
            osc_routing=False,
        )
        self.app.player.load_file(path)
        self.app.remember_opened(path)
        self._refresh_grid()

    # ----- per-part transport -----------------------------------------

    def _current_loop_position(self) -> int | None:
        """Read the Player's current part-loop index (or None when off).

        Returned value drives header-button highlighting so the active
        loop-on part shows visually distinct from the others.
        """
        p = self.app.player
        if p is None:
            return None
        return getattr(p, "loop_position", None)

    def _on_part_play(self, position: int | None) -> None:
        """Click handler for the ▶ button in a part column header."""
        if position is None or self.app.player is None:
            return
        try:
            self.app.player.jump_to_part_position(position)
        except Exception:
            pass

    def _on_part_loop_toggle(self, position: int | None) -> None:
        """Click handler for the 🔁 button in a part column header.

        Toggle behaviour: if this part is currently looping, clear the
        loop; otherwise set the loop to this part. Rebuilds the grid
        after so the active 🔁 highlight refreshes.
        """
        if position is None or self.app.player is None:
            return
        current = getattr(self.app.player, "loop_position", None)
        try:
            if current == position:
                self.app.player.set_loop_position(None)
            else:
                self.app.player.set_loop_position(position)
        except Exception:
            pass
        self._refresh_grid()

    # ----- helpers -----------------------------------------------------

    def _resolved(self):
        if self.app.player is None:
            return None
        try:
            self.app.player._resolve_current()
        except Exception:
            return None
        return self.app.player.current_resolved

    def _arrangement_unique(self, resolved) -> list[str]:
        """Deduped list of part names in arrangement-source-order.

        The raw arrangement is e.g. ``["intro", "verse", "chorus",
        "verse", "outro"]`` — we want one column per unique part for
        the grid view.
        """
        seen: list[str] = []
        for p in resolved.arrangement:
            if p not in seen:
                seen.append(p)
        return seen
