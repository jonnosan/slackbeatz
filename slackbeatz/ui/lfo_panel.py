"""LFO management panel — add / edit / delete LFOs + their apply bindings.

Reached from the Arrangement screen's menu bar. Mutates the
underlying .sb file via :mod:`slackbeatz.ui.sb_edit` and triggers
a Player re-resolve so the new LFOs take effect immediately.

Phrase-mode songs (composed live from a title, no .sb file on disk)
can't be edited here — the panel shows a Save-As-first hint
instead. The user picks Save As from the Arrangement File menu to
materialise a .sb, after which LFO editing works normally.

Layout:

    [← Arrangement]  LFOs                        [+ New LFO]
    --------------------------------------------------------
    lead_breath  shape=sine bars=32 height=0.4   [Edit] [Delete]
      apply → ch:1/cc:74  (in main, drop)        [Remove]
      [+ Apply to part…]

    test_filter  shape=sawtooth bars=4 height=0.8 [Edit] [Delete]
      (no applications)
      [+ Apply to part…]
    --------------------------------------------------------
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from slackbeatz.ui.sb_edit import (
    SbEditError, add_apply, add_lfo, remove_apply, remove_lfo, update_lfo,
)

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


_SHAPES = ("sine", "sawtooth", "square", "pulse", "noise")


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

        editable = self._editable()
        ttk.Button(
            bar, text="+ New LFO", command=self._on_new_lfo,
            state=("normal" if editable else "disabled"),
        ).pack(side="right", padx=4)

        body = tk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        if not editable:
            tk.Label(
                body,
                text="This song was composed live from a title — no .sb "
                     "file on disk to edit.\nSave it via File → Save As… "
                     "first, then re-open this panel to add LFOs.",
                fg="gray", justify="left", wraplength=560,
            ).pack(pady=40)
            self._build_help(body)
            return

        resolved = self._resolved()
        if resolved is None:
            tk.Label(body, text="(no song loaded)", fg="gray").pack(pady=40)
            return

        if not resolved.lfos:
            tk.Label(
                body,
                text="No LFOs declared yet. Click + New LFO to add one.",
                fg="gray",
            ).pack(pady=20)
        else:
            tk.Label(
                body, text="Declared LFOs:",
                font=("TkDefaultFont", 10, "bold"), anchor="w",
            ).pack(fill="x")
            for spec in list(resolved.lfos.values()):
                self._build_lfo_row(body, spec, resolved)

        self._build_help(body)

    def _build_lfo_row(self, parent: tk.Misc, spec, resolved) -> None:
        row = tk.Frame(parent, relief="ridge", borderwidth=1, padx=4, pady=4)
        row.pack(fill="x", pady=3)

        head = tk.Frame(row)
        head.pack(fill="x")
        knob_str = (
            f"shape={spec.shape}  period_bars={spec.period_bars}  "
            f"width={spec.width}  height={spec.height}"
        )
        tk.Label(head, text=f"{spec.name}  —  {knob_str}",
                 anchor="w", font=("TkDefaultFont", 10, "bold"),
                 ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            head, text="Edit", width=6,
            command=lambda s=spec: self._on_edit_lfo(s),
        ).pack(side="left", padx=2)
        ttk.Button(
            head, text="Delete", width=8,
            command=lambda n=spec.name: self._on_delete_lfo(n),
        ).pack(side="left", padx=2)

        applies_in: list[tuple[str, str]] = []
        for part_name, part in resolved.parts.items():
            for app in part.lfo_applications:
                if app.lfo_name == spec.name:
                    applies_in.append((part_name, f"{app.target.kind}:{app.target.ref}"))

        if applies_in:
            for part_name, target in applies_in:
                app_row = tk.Frame(row)
                app_row.pack(fill="x", padx=12, pady=1)
                tk.Label(
                    app_row,
                    text=f"  apply → {target}   (in part: {part_name})",
                    anchor="w", fg="#444",
                ).pack(side="left", fill="x", expand=True)
                ttk.Button(
                    app_row, text="✕", width=2,
                    command=lambda p=part_name, n=spec.name:
                        self._on_remove_apply(p, n),
                ).pack(side="left", padx=2)
        else:
            tk.Label(
                row, text="  (no applications — not wired to any part yet)",
                fg="gray", anchor="w",
            ).pack(fill="x", padx=12)

        add_row = tk.Frame(row)
        add_row.pack(fill="x", padx=12, pady=2)
        ttk.Button(
            add_row, text="+ Apply to part…", width=18,
            command=lambda n=spec.name: self._on_add_apply(n),
        ).pack(side="left")

    def _build_help(self, body: tk.Misc) -> None:
        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=8)
        tk.Label(
            body,
            text=(
                "LFO target syntax (in the .sb apply lines):\n"
                "  midi:ch:N/cc:M       — MIDI CC on channel N, controller M\n"
                "  surge:/param/…       — Surge XT parameter (live mode only)\n"
                "  pattern:HANDLE:KNOB  — pattern knob (engine support pending)\n"
                "  feel:TYPE:KNOB       — feel knob (engine support pending)\n"
            ),
            anchor="w", justify="left", font=("TkFixedFont", 9),
        ).pack(fill="x")

    # ----- actions -----------------------------------------------------

    def _on_new_lfo(self) -> None:
        LfoEditDialog(
            self.app, on_apply=self._do_add_lfo,
            initial={"shape": "sine", "bars": "8", "height": "0.5",
                     "width": "0.5", "offset": "0.5"},
        )

    def _on_edit_lfo(self, spec) -> None:
        initial = {
            "name": spec.name,
            "shape": spec.shape,
            "bars": str(spec.period_bars),
            "width": str(spec.width),
            "height": str(spec.height),
            "offset": "0.5",  # LfoSpec doesn't carry offset; default
        }
        LfoEditDialog(
            self.app, on_apply=lambda n, k: self._do_update_lfo(spec.name, n, k),
            initial=initial, locked_name=True,
        )

    def _on_delete_lfo(self, name: str) -> None:
        ok = messagebox.askyesno(
            "Delete LFO",
            f"Delete LFO '{name}'?\n\nAll `apply {name}` bindings inside "
            f"parts will also be removed.",
        )
        if not ok:
            return
        path = self._song_path()
        if path is None:
            return
        try:
            remove_lfo(path, name)
        except SbEditError as e:
            messagebox.showerror("Delete LFO", str(e))
            return
        self._reload()

    def _on_add_apply(self, lfo_name: str) -> None:
        resolved = self._resolved()
        if resolved is None:
            return
        part_names = list(resolved.parts.keys())
        ApplyAddDialog(
            self.app, lfo_name=lfo_name, part_names=part_names,
            on_apply=lambda part, ref: self._do_add_apply(part, lfo_name, ref),
        )

    def _on_remove_apply(self, part_name: str, lfo_name: str) -> None:
        path = self._song_path()
        if path is None:
            return
        try:
            remove_apply(path, part_name, lfo_name)
        except SbEditError as e:
            messagebox.showerror("Remove apply", str(e))
            return
        self._reload()

    def _do_add_lfo(self, name: str, knobs: dict[str, str]) -> None:
        path = self._song_path()
        if path is None:
            return
        try:
            add_lfo(path, name, knobs)
        except SbEditError as e:
            messagebox.showerror("Add LFO", str(e))
            return
        self._reload()

    def _do_update_lfo(
        self, original_name: str, new_name: str, knobs: dict[str, str],
    ) -> None:
        if new_name != original_name:
            messagebox.showinfo(
                "Edit LFO",
                "Renaming an LFO isn't supported yet — delete the old "
                "one + add a new one with the desired name.",
            )
            return
        path = self._song_path()
        if path is None:
            return
        try:
            update_lfo(path, original_name, knobs)
        except SbEditError as e:
            messagebox.showerror("Edit LFO", str(e))
            return
        self._reload()

    def _do_add_apply(self, part_name: str, lfo_name: str, target_ref: str) -> None:
        path = self._song_path()
        if path is None:
            return
        try:
            add_apply(path, part_name, lfo_name, target_ref)
        except SbEditError as e:
            messagebox.showerror("Add apply", str(e))
            return
        self._reload()

    # ----- shared helpers ---------------------------------------------

    def _back(self) -> None:
        from slackbeatz.ui.arrangement import ArrangementScreen
        self.app.transition_to(ArrangementScreen)

    def _reload(self) -> None:
        """Re-read the .sb file + re-render the panel."""
        if self.app.player is not None:
            try:
                self.app.player._resolve_current()
            except Exception:
                pass
        for child in self.winfo_children():
            child.destroy()
        self._build()

    def _editable(self) -> bool:
        """True iff there's a .sb file on disk we can mutate.

        Phrase-mode songs (current_song_path is None) need to be
        Save-As'd before LFO editing works — the LFO mutations have
        to land somewhere durable.
        """
        return self._song_path() is not None

    def _song_path(self):
        p = self.app.player
        if p is None or p.current_song_path is None:
            return None
        return p.current_song_path

    def _resolved(self):
        if self.app.player is None:
            return None
        try:
            self.app.player._resolve_current()
        except Exception:
            return None
        return self.app.player.current_resolved


class LfoEditDialog:
    """Modal Toplevel — fill in / edit LFO knobs."""

    def __init__(
        self, app: "GuiApp", *,
        on_apply, initial: dict[str, str],
        locked_name: bool = False,
    ) -> None:
        self.app = app
        self.on_apply = on_apply
        self.win = tk.Toplevel(app.root)
        self.win.title("New LFO" if not locked_name else f"Edit LFO {initial.get('name', '')}")
        self.win.transient(app.root)
        self.win.grab_set()
        self.win.resizable(False, False)

        self.vars: dict[str, tk.StringVar] = {}

        def _row(label: str, key: str, *, widget="entry", combo_values=None):
            row = tk.Frame(self.win)
            row.pack(fill="x", padx=12, pady=2)
            tk.Label(row, text=label, width=10, anchor="w").pack(side="left")
            var = tk.StringVar(value=initial.get(key, ""))
            self.vars[key] = var
            if widget == "entry":
                e = ttk.Entry(row, textvariable=var, width=20)
                if locked_name and key == "name":
                    e.config(state="readonly")
                e.pack(side="left")
            else:  # combo
                ttk.Combobox(
                    row, textvariable=var, values=combo_values, state="readonly",
                    width=18,
                ).pack(side="left")

        _row("Name:", "name")
        _row("Shape:", "shape", widget="combo", combo_values=_SHAPES)
        _row("Bars:", "bars")
        _row("Width:", "width")
        _row("Height:", "height")
        _row("Offset:", "offset")

        btns = tk.Frame(self.win)
        btns.pack(padx=12, pady=(8, 12), fill="x")
        ttk.Button(btns, text="Apply", command=self._apply).pack(side="right", padx=2)
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(side="right", padx=2)
        self.win.bind("<Return>", lambda _e: self._apply())
        self.win.bind("<Escape>", lambda _e: self.win.destroy())

    def _apply(self) -> None:
        name = self.vars["name"].get().strip()
        if not name:
            messagebox.showerror("LFO", "name is required", parent=self.win)
            return
        knobs: dict[str, str] = {}
        for k in ("shape", "bars", "width", "height", "offset"):
            v = self.vars[k].get().strip()
            if v:
                knobs[k] = v
        if "shape" not in knobs:
            messagebox.showerror("LFO", "shape is required", parent=self.win)
            return
        if "bars" not in knobs and "hz" not in knobs:
            messagebox.showerror("LFO", "bars (or hz) is required", parent=self.win)
            return
        self.win.destroy()
        self.on_apply(name, knobs)


class ApplyAddDialog:
    """Modal Toplevel — pick which part to attach an LFO to."""

    def __init__(
        self, app: "GuiApp", *,
        lfo_name: str, part_names: list[str], on_apply,
    ) -> None:
        self.app = app
        self.on_apply = on_apply
        self.win = tk.Toplevel(app.root)
        self.win.title(f"Apply {lfo_name}")
        self.win.transient(app.root)
        self.win.grab_set()
        self.win.resizable(False, False)

        tk.Label(
            self.win,
            text=f"Bind LFO '{lfo_name}' to a part target.",
            anchor="w", wraplength=320, justify="left",
        ).pack(padx=12, pady=(12, 6))

        # Part picker.
        part_row = tk.Frame(self.win)
        part_row.pack(padx=12, pady=2, fill="x")
        tk.Label(part_row, text="Part:", width=10, anchor="w").pack(side="left")
        self.part_var = tk.StringVar(value=part_names[0] if part_names else "")
        ttk.Combobox(
            part_row, textvariable=self.part_var, state="readonly",
            values=part_names, width=18,
        ).pack(side="left")

        # MIDI channel + CC inputs (MVP — other target kinds via raw target field below).
        ch_row = tk.Frame(self.win)
        ch_row.pack(padx=12, pady=2, fill="x")
        tk.Label(ch_row, text="MIDI Ch:", width=10, anchor="w").pack(side="left")
        self.ch_var = tk.StringVar(value="2")
        ttk.Combobox(
            ch_row, textvariable=self.ch_var, state="readonly",
            values=[str(i) for i in range(1, 17)], width=4,
        ).pack(side="left")
        tk.Label(ch_row, text="  CC:").pack(side="left")
        self.cc_var = tk.StringVar(value="74")
        ttk.Entry(ch_row, textvariable=self.cc_var, width=6).pack(side="left")
        tk.Label(
            ch_row, text="  (74=cutoff, 71=resonance, 7=volume)",
            fg="gray", font=("TkDefaultFont", 9),
        ).pack(side="left")

        # Raw target override — for non-midi_cc targets.
        raw_row = tk.Frame(self.win)
        raw_row.pack(padx=12, pady=(8, 2), fill="x")
        tk.Label(
            raw_row, text="Or raw target=", width=14, anchor="w",
        ).pack(side="left")
        self.raw_var = tk.StringVar(value="")
        ttk.Entry(raw_row, textvariable=self.raw_var, width=30).pack(side="left")
        tk.Label(
            self.win,
            text="(blank uses the midi:ch/cc above; otherwise paste a "
                 "full target string e.g. surge:/param/a/filter/1/cutoff)",
            fg="gray", wraplength=350, justify="left",
        ).pack(padx=12)

        btns = tk.Frame(self.win)
        btns.pack(padx=12, pady=(8, 12), fill="x")
        ttk.Button(btns, text="Apply", command=self._apply).pack(side="right", padx=2)
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(side="right", padx=2)
        self.win.bind("<Return>", lambda _e: self._apply())
        self.win.bind("<Escape>", lambda _e: self.win.destroy())

    def _apply(self) -> None:
        part = self.part_var.get().strip()
        if not part:
            messagebox.showerror("Apply", "pick a part", parent=self.win)
            return
        raw = self.raw_var.get().strip()
        if raw:
            target = raw
        else:
            ch = self.ch_var.get().strip()
            cc = self.cc_var.get().strip()
            if not ch or not cc:
                messagebox.showerror("Apply", "MIDI ch + CC are required", parent=self.win)
                return
            target = f"midi:ch:{ch}/cc:{cc}"
        self.win.destroy()
        self.on_apply(part, target)
