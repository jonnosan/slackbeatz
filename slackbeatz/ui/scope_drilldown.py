"""Three-tier Algorithm / Pattern / Feel drilldown widget.

The detail pane the Arrangement screen mounts when the user selects a
(voice, part) cell. Three tiers:

* **Algorithm** — pick the generator class (single dropdown).
* **Pattern** — algorithm-specific knobs (voicing, swing, progression,
  density, octave, ...). Filtered to knobs the algorithm registers in
  the per-(type, algorithm) defaults table.
* **Feel** — universal knob set from :mod:`slackbeatz.generators.feel`
  applied to every algorithm. Always the same 8 knobs in the same
  order.

Each knob row reads the effective cascaded value and shows a scope dot
flagging where the override (if any) lives:

    swing       [▬▬▬●▬▬]  0.58           ● voice                 [↺]

The scope picker at the top of the screen decides which scope a *new*
edit lands in.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, TYPE_CHECKING

from slackbeatz.generators.feel import FEEL_KNOBS, FeelSpec
from slackbeatz.generators.registry import REGISTRY
from slackbeatz.ui.knob_specs import KnobSpec, get_knob_spec
from slackbeatz.ui.tooltip import Tooltip

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


# Pattern-tier knobs we surface in the UI today, per generator type.
# Algorithm-specific subsets get filtered in by looking at the gen's
# current knob dict (a knob the algorithm actually reads is worth
# showing). This list is the *upper bound* — we never offer a knob the
# generator type doesn't know about.
_PATTERN_KNOB_HINTS: dict[str, tuple[str, ...]] = {
    "rhythm":  ("swing", "gate", "density", "accent", "drop_prob", "polyrhythm"),
    "bass":    ("voicing", "progression", "bars_per_chord", "gate", "octave",
                "walking", "pickup", "fifth_prob", "third_prob",
                "burble_prob", "kick_env", "bend"),
    "melody":  ("voicing", "progression", "bars_per_chord", "gate", "octave",
                "arp_prob", "arp_period", "motif_memory", "pair"),
    "chords":  ("voicing", "progression", "bars_per_chord", "inversion",
                "arp_prob", "arp_period", "voice_lead"),
    "candy":   ("density", "cycle", "cc", "resonance", "modwheel", "pan", "reverb"),
    "subbass": ("octave", "gate"),
    "speech":  ("phrase_interval", "voice", "velocity", "note_base"),
    "sample":  ("bank", "pattern", "pulses", "steps", "velocity"),
}


class ScopeDrilldown(tk.Frame):
    """Detail widget for one (voice, part) selection."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        app: "GuiApp",
        voice_handle: str,
        part_name: str,
        scope: str,
        on_change: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.voice_handle = voice_handle
        self.part_name = part_name
        self.scope = scope  # "part" | "voice" | "song"
        self.on_change = on_change
        # Cache the resolved song so we don't re-resolve on every
        # widget paint.
        self.resolved = app.player.current_resolved
        self.gen = self.resolved.gens[voice_handle]
        self.part = self.resolved.parts[part_name]
        # Scrollable surface — knob rows can overflow on small screens.
        self._build()

    def _build(self) -> None:
        """Build the drilldown UI.

        Layout: scrollable Canvas with collapsible sections inside.
        Each section starts collapsed except Algorithm + Patch (the
        most-immediate touch-points). User clicks section headers to
        expand / collapse — keeps the drilldown compact for a quick
        scan even with dozens of knobs in scope.
        """
        # Scrollable surface — Canvas + vertical Scrollbar wrapping a
        # content Frame. Knob lists can run to 30+ rows on busy
        # voices (Feel × Pattern × Patch); without this the lower
        # rows fall off the bottom of the detail host.
        canvas = tk.Canvas(self, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        sb.pack(side="right", fill="y")
        canvas.config(yscrollcommand=sb.set)
        content = tk.Frame(canvas)
        canvas_win = canvas.create_window((0, 0), window=content, anchor="nw")

        def _on_content_resize(_e=None):
            canvas.config(scrollregion=canvas.bbox("all"))
        content.bind("<Configure>", _on_content_resize)

        def _on_canvas_resize(_e):
            canvas.itemconfigure(canvas_win, width=_e.width)
        canvas.bind("<Configure>", _on_canvas_resize)

        # Mouse-wheel: route platform-specific wheel events to the
        # canvas. Different OSes deliver different event names.
        def _on_wheel(e):
            delta = -1 if (getattr(e, "delta", 0) > 0 or getattr(e, "num", 0) == 4) else 1
            canvas.yview_scroll(delta, "units")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(seq, _on_wheel, add="+")

        # ---- Algorithm section (always-expanded; primary control) ----
        algo_sec = self._make_section(content, "Algorithm", expanded=True)
        algo_row = tk.Frame(algo_sec)
        algo_row.pack(fill="x", padx=8, pady=(6, 4))
        current_algo = self.part.algorithm_overrides.get(
            self.voice_handle, self.gen.style,
        )
        choices = sorted(a for (t, a) in REGISTRY if t == self.gen.type_)
        self.algo_var = tk.StringVar(value=current_algo)
        combo = ttk.Combobox(
            algo_row, textvariable=self.algo_var, state="readonly",
            values=choices, width=22,
        )
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._on_algo_change())
        algo_dot = self._scope_dot_for(
            song_value=self.gen.style,
            voice_value=None,
            part_value=self.part.algorithm_overrides.get(self.voice_handle),
        )
        tk.Label(algo_row, text=algo_dot, fg="orange",
                 font=("TkDefaultFont", 10, "bold")).pack(side="left", padx=4)

        # ---- Patch section (expanded — high-touch sound design) ----
        patch_sec = self._make_section(content, "Patch + FX", expanded=True)
        self._build_patch_row(current_algo, parent=patch_sec)

        # ---- Pattern section (collapsed by default — gen-specific knobs) ----
        pattern_sec = self._make_section(content, "Pattern (gen-specific)", expanded=False)
        pattern_frame = tk.Frame(pattern_sec)
        pattern_frame.pack(fill="x", padx=16)
        for knob_name in _PATTERN_KNOB_HINTS.get(self.gen.type_, ()):
            self._build_knob_row(pattern_frame, knob_name, tier="pattern")

        # ---- Feel section (collapsed by default — universal knobs) ----
        feel_sec = self._make_section(content, "Feel (universal)", expanded=False)
        feel_frame = tk.Frame(feel_sec)
        feel_frame.pack(fill="x", padx=16)
        for spec in FEEL_KNOBS:
            self._build_knob_row(feel_frame, spec.name, tier="feel", feel_spec=spec)

    # ----- collapsible section helper ---------------------------------

    def _make_section(self, parent: tk.Misc, title: str, *, expanded: bool) -> tk.Frame:
        """Build a collapsible section + return its body Frame.

        Header row shows ``▼ title`` when expanded, ``▶ title`` when
        collapsed. Clicking the header toggles. Body Frame is what
        the caller packs widgets into; it's hidden when collapsed by
        ``pack_forget`` (and re-packed on expand).
        """
        wrap = tk.Frame(parent, relief="ridge", borderwidth=1)
        wrap.pack(fill="x", padx=8, pady=2)
        header = tk.Frame(wrap, bg="#e8e8e8")
        header.pack(fill="x")
        arrow_var = tk.StringVar(value="▼" if expanded else "▶")
        label = tk.Label(
            header, textvariable=arrow_var,
            font=("TkDefaultFont", 10, "bold"), bg="#e8e8e8",
            cursor="hand2", width=2,
        )
        label.pack(side="left", padx=4)
        title_label = tk.Label(
            header, text=title, font=("TkDefaultFont", 10, "bold"),
            bg="#e8e8e8", cursor="hand2",
        )
        title_label.pack(side="left", padx=2)

        body = tk.Frame(wrap)
        if expanded:
            body.pack(fill="x")

        def _toggle(_e=None):
            if body.winfo_ismapped():
                body.pack_forget()
                arrow_var.set("▶")
            else:
                body.pack(fill="x")
                arrow_var.set("▼")
        for w in (label, title_label, header):
            w.bind("<Button-1>", _toggle)
        return body

    # ----- patch picker ------------------------------------------------

    def _build_patch_row(self, current_algo: str, *, parent: tk.Misc | None = None) -> None:
        """Surge factory-patch picker for this voice's channel.

        Lists every ``.fxp`` under the role's factory subdirectory
        (Basses / Leads / Pads / Sequences). A ☰ button next to the
        dropdown switches to "ALL CATEGORIES" mode so the user can
        pick any patch regardless of category — handy when the
        role's default category doesn't carry the sound they want
        (e.g. picking a Lead patch for the candy channel).

        Selecting a patch calls ``SurgeInstance.load_patch`` on the
        running surge-xt-cli for this channel — change is audible
        immediately in live mode. Offline render is a separate
        path that uses ``audio_offline_presets`` and doesn't honor
        this selection (dawdreamer's .fxp loader is broken).
        """
        from slackbeatz.surge_host import (
            _GEN_TYPE_TO_ROLE,
            list_factory_patches, patch_category_for_role,
            resolve_factory_patch,
        )
        role = _GEN_TYPE_TO_ROLE.get(self.gen.type_)
        if role is None:
            return
        # Find the SurgeInstance for this voice's channel (if any).
        # Without a runtime, the dropdown still shows the catalogue
        # but selection no-ops (offline path doesn't honor live picks).
        surge_inst = self._surge_instance_for_channel(
            self.gen.instrument.channel if self.gen.instrument else None,
        )
        category = patch_category_for_role(role)

        patch_parent = parent if parent is not None else self
        patch_row = tk.Frame(patch_parent)
        patch_row.pack(fill="x", padx=8, pady=(2, 4))
        tk.Label(patch_row, text="Patch:",
                 font=("TkDefaultFont", 10, "bold")).pack(side="left")

        self._patch_mode = tk.StringVar(value="role")  # "role" | "all"

        def _refresh_patch_choices(*_a):
            mode = self._patch_mode.get()
            chosen_cat = category if mode == "role" else None
            patches = list_factory_patches(chosen_cat)
            self._patches_by_display = {d: rel for d, rel in patches}
            display_choices = list(self._patches_by_display.keys())
            combo["values"] = display_choices
            # Try to preserve the current selection across mode flips.
            cur = self.patch_var.get()
            if cur not in self._patches_by_display:
                # Try matching the currently loaded patch
                if surge_inst is not None:
                    rel = surge_inst.current_patch_rel
                    if rel:
                        from pathlib import Path as _P
                        stem = _P(rel).stem
                        if stem in self._patches_by_display:
                            self.patch_var.set(stem)
                            return
                        # All-categories shows full relpath; try that
                        rel_no_ext = rel[:-4] if rel.endswith(".fxp") else rel
                        if rel_no_ext in self._patches_by_display:
                            self.patch_var.set(rel_no_ext)
                            return
                if display_choices:
                    self.patch_var.set(display_choices[0])
                else:
                    self.patch_var.set("")

        self.patch_var = tk.StringVar(value="")
        combo = ttk.Combobox(
            patch_row, textvariable=self.patch_var, state="readonly",
            values=[], width=40,
        )
        combo.pack(side="left", padx=(8, 4))
        _refresh_patch_choices()

        def _on_select(_e=None):
            display = self.patch_var.get()
            rel = self._patches_by_display.get(display)
            if rel is None:
                return
            path = resolve_factory_patch(rel)
            if path is None or surge_inst is None:
                return
            try:
                surge_inst.load_patch(path)
            except Exception:
                pass
        combo.bind("<<ComboboxSelected>>", _on_select)

        # Role / All-categories toggle.
        def _toggle_mode():
            self._patch_mode.set("all" if self._patch_mode.get() == "role" else "role")
            mode_btn.config(text=("All" if self._patch_mode.get() == "all" else "Role"))
            _refresh_patch_choices()
        mode_label = "Role" if self._patch_mode.get() == "role" else "All"
        mode_btn = ttk.Button(
            patch_row, text=mode_label, width=4, command=_toggle_mode,
        )
        mode_btn.pack(side="left", padx=2)

        # Status hint when no surge instance is running.
        if surge_inst is None:
            tk.Label(
                patch_row,
                text="(no live surge for this channel)",
                fg="gray", font=("TkDefaultFont", 9, "italic"),
            ).pack(side="left", padx=6)

        # FX editor — separate button opens a window with full FX1/FX2
        # type pickers + per-FX param sliders. Avoids cluttering the
        # already-busy drilldown with two more multi-row controls.
        if surge_inst is not None:
            ttk.Button(
                patch_row, text="FX…", width=5,
                command=lambda: self._open_fx_editor(surge_inst),
            ).pack(side="left", padx=6)

    def _surge_instance_for_channel(self, channel_1idx: int | None):
        """Return the live ``SurgeInstance`` running on *channel_1idx*,
        or None when no live runtime is up (offline / no surge-xt-cli)."""
        if channel_1idx is None:
            return None
        runtime = getattr(self.app, "live_runtime", None)
        if runtime is None:
            return None
        for inst in getattr(runtime, "surge_instances", []) or []:
            if getattr(inst.config, "channel_1idx", None) == channel_1idx:
                return inst
        return None

    def _open_fx_editor(self, surge_inst) -> None:
        from slackbeatz.ui.fx_editor import FxEditorDialog
        FxEditorDialog(self.app, surge_inst)

    # ----- knob rows ---------------------------------------------------

    def _build_knob_row(
        self,
        parent: tk.Misc,
        knob_name: str,
        *,
        tier: str,
        feel_spec: FeelSpec | None = None,
    ) -> None:
        """One row: label + smart-controller + effective-value badge +
        scope dot + revert.

        Controller dispatch (per ``ui.knob_specs.get_knob_spec``):
        * bool → ``Checkbutton``
        * enum → readonly ``Combobox``
        * float / int → ``Scale`` slider
        * unknown → small effective-value Label + Edit dialog

        The effective value is always rendered as a badge to the right
        of the controller. Inherited values render italic-gray;
        set-at-current-scope values render bold-black — so the user
        can see at a glance whether a knob is locally overridden vs
        cascading down from voice / song / engine default.
        """
        row = tk.Frame(parent)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=knob_name, width=16, anchor="w").pack(side="left")

        song_val = self.gen.knobs.get(knob_name)
        voice_val = self.resolved.voice_defaults.get(self.gen.type_, {}).get(knob_name)
        part_val = self.part.knob_overrides.get(self.voice_handle, {}).get(knob_name)
        effective = part_val if part_val is not None else (
            voice_val if voice_val is not None else song_val
        )

        # Resolve the spec — feel knobs come pre-specced; pattern
        # knobs go through the central KNOB_SPECS table.
        spec = self._unified_spec(knob_name, feel_spec)
        ctrl_frame = tk.Frame(row)
        ctrl_frame.pack(side="left", padx=4)

        if spec is not None:
            self._build_controller(
                ctrl_frame, knob_name, spec, effective,
            )
        else:
            # Unknown spec — fall back to read-only Label + Edit button.
            val_label = tk.Label(
                ctrl_frame,
                text=str(effective) if effective is not None else "(default)",
                width=18, anchor="w", relief="sunken", bg="white",
            )
            val_label.pack(side="left")
            ttk.Button(
                ctrl_frame, text="Edit",
                command=lambda n=knob_name, e=effective:
                    self._open_knob_editor(n, e),
            ).pack(side="left", padx=4)

        # Effective-value badge — always visible, distinguishes
        # set-at-current-scope (bold black) vs inherited (italic gray).
        set_here = self._is_set_at_current_scope(part_val, voice_val, song_val)
        eff_text = "(default)" if effective is None else str(effective)
        if set_here:
            badge = tk.Label(
                row, text=f"= {eff_text}", fg="black",
                font=("TkDefaultFont", 9, "bold"), width=14, anchor="w",
            )
        else:
            badge = tk.Label(
                row, text=f"= {eff_text}", fg="gray",
                font=("TkDefaultFont", 9, "italic"), width=14, anchor="w",
            )
        badge.pack(side="left", padx=4)

        dot_text, dot_color = self._scope_dot_string(part_val, voice_val, song_val)
        dot_label = tk.Label(row, text=dot_text, fg=dot_color, width=10, anchor="w")
        dot_label.pack(side="left", padx=4)
        tooltip_text = self._cascade_tooltip(
            knob_name, part_val, voice_val, song_val,
        )
        if tooltip_text:
            Tooltip(dot_label, tooltip_text)

        ttk.Button(
            row, text="↺", width=2,
            command=lambda n=knob_name: self._on_revert(n),
        ).pack(side="left")

    # ----- spec unification + controller dispatch ---------------------

    def _unified_spec(self, knob_name: str, feel_spec: FeelSpec | None):
        """Return a uniform spec dict for *knob_name*.

        Feel knobs come with their own FeelSpec; pattern knobs go
        through KNOB_SPECS. Returns None when no spec is registered
        (caller falls back to the text-entry dialog).

        Output shape: a :class:`KnobSpec` so the dispatcher only
        needs to handle one type.
        """
        if feel_spec is not None:
            kind = "float" if isinstance(feel_spec.high, float) else "int"
            return KnobSpec(
                kind=kind,
                low=feel_spec.low, high=feel_spec.high,
                step=0.01 if kind == "float" else 1,
                default=feel_spec.default,
            )
        return get_knob_spec(knob_name, self.gen.type_)

    def _build_controller(
        self, parent: tk.Misc, knob_name: str, spec: KnobSpec, current,
    ) -> None:
        """Render the right Tk widget for *spec.kind*."""
        if spec.kind == "bool":
            var = tk.BooleanVar(value=bool(current) if current is not None else bool(spec.default))
            tk.Checkbutton(
                parent, variable=var,
                command=lambda n=knob_name, v=var: self._on_knob_change(n, bool(v.get())),
            ).pack(side="left")
            return
        if spec.kind == "enum":
            choices = list(spec.choices or ())
            cur_str = str(current) if current is not None else str(spec.default)
            if cur_str not in choices and "(none)" in choices:
                cur_str = "(none)"
            elif cur_str not in choices and choices:
                cur_str = choices[0]
            var = tk.StringVar(value=cur_str)
            combo = ttk.Combobox(
                parent, textvariable=var, state="readonly",
                values=choices, width=18,
            )
            combo.pack(side="left")
            def _on_pick(_e, n=knob_name, v=var):
                picked = v.get()
                # "(none)" sentinel → revert (clear override).
                if picked == "(none)":
                    self._on_revert(n)
                else:
                    self._on_knob_change(n, picked)
            combo.bind("<<ComboboxSelected>>", _on_pick)
            return
        # Numeric — Scale slider.
        is_float = spec.kind == "float"
        try:
            cur_val = float(current) if current is not None else float(spec.default or 0)
        except (TypeError, ValueError):
            cur_val = float(spec.default or 0)
        slider = tk.Scale(
            parent, from_=spec.low, to=spec.high,
            resolution=spec.step or (0.01 if is_float else 1),
            orient="horizontal", length=180, showvalue=True,
        )
        slider.set(cur_val)
        slider.pack(side="left")
        slider.bind(
            "<ButtonRelease-1>",
            lambda _e, n=knob_name, s=slider, f=is_float:
                self._on_knob_change(n, float(s.get()) if f else int(s.get())),
        )

    def _is_set_at_current_scope(self, part_val, voice_val, song_val) -> bool:
        """True iff the EFFECTIVE value at the user's current scope
        is set at THAT scope (vs cascading down from a wider scope)."""
        if self.scope == "part":
            return part_val is not None
        if self.scope == "voice":
            return voice_val is not None
        return song_val is not None

    # ----- effective-value + scope helpers ----------------------------

    def _scope_dot_for(self, *, song_value, voice_value, part_value):
        """Variant of _scope_dot_string for the algorithm tier.

        Returns the dot-text for the most-specific scope that carries
        a value. We don't bother colouring the algorithm dot — it sits
        next to the algorithm picker and just flags "this is a per-
        part override" vs "this is the song default".
        """
        if part_value is not None and part_value != song_value:
            return "● part"
        if voice_value is not None and voice_value != song_value:
            return "● voice"
        if song_value is not None:
            return "● song"
        return ""

    def _scope_dot_string(self, part_val, voice_val, song_val):
        if part_val is not None:
            return ("● part", "orange")
        if voice_val is not None:
            return ("● voice", "blue")
        if song_val is not None:
            return ("● song", "gray")
        return ("", "black")

    def _cascade_tooltip(
        self, knob_name: str, part_val, voice_val, song_val,
    ) -> str:
        """Build the multi-line tooltip showing the cascade chain.

        Example output:
            Defined at part:verse = 0.6
            Voice default 0.4
            Song default 0.4 (style)
        """
        lines: list[str] = []
        if part_val is not None:
            lines.append(f"Defined at part:{self.part_name} = {part_val}")
        if voice_val is not None:
            lines.append(f"Voice default for {self.gen.type_} = {voice_val}")
        if song_val is not None:
            lines.append(f"Song default = {song_val}")
        if not lines:
            return f"{knob_name}: engine default (no override active)"
        # Always show the engine-default fallback as the last line so
        # the user knows where the chain bottoms out.
        lines.append("(engine default below)")
        return "\n".join(lines)

    # ----- knob/algorithm change handlers -----------------------------

    def _on_algo_change(self) -> None:
        """Algorithm picker selection. Always goes to part scope today
        — voice-block algorithm overrides aren't supported by the
        cascade (voice carries knobs, not algorithm names)."""
        new_algo = self.algo_var.get()
        # Mutate the resolved part in place (the Player.save_state
        # path round-trips part.algorithm_overrides explicitly).
        self.part.algorithm_overrides[self.voice_handle] = new_algo
        # Track on Player so it survives re-resolves.
        if self.voice_handle in self.app.player._part_algorithm_overrides.setdefault(
            self.part_name, {},
        ):
            self.app.player._part_algorithm_overrides[self.part_name][
                self.voice_handle
            ] = new_algo
        else:
            self.app.player._part_algorithm_overrides[self.part_name][
                self.voice_handle
            ] = new_algo
        self.on_change()

    def _on_knob_change(self, knob_name: str, value) -> None:
        """A control changed. Apply the value at the current scope.

        Three-step write so the change is both visible and audible:

        1. Mutate the live resolved structures — instant visual feedback
           in the drilldown (effective-value badge updates on the next
           grid-rebuild).
        2. Persist the override on the Player at the right scope so it
           survives the next ``_resolve_current``.
        3. Schedule a bar-aligned re-play() so the new value actually
           takes effect during live playback — at the next bar boundary
           rather than mid-bar.
        """
        if isinstance(value, float) and value.is_integer():
            spec = next((s for s in FEEL_KNOBS if s.name == knob_name), None)
            if spec is not None and isinstance(spec.high, int):
                value = int(value)

        player = self.app.player
        if self.scope == "part":
            bucket = self.part.knob_overrides.setdefault(self.voice_handle, {})
            bucket[knob_name] = value
            if player is not None:
                player.set_part_knob(
                    self.part_name, self.voice_handle, knob_name, value,
                )
        elif self.scope == "voice":
            bucket = self.resolved.voice_defaults.setdefault(self.gen.type_, {})
            bucket[knob_name] = value
            if player is not None:
                player.set_voice_knob(self.gen.type_, knob_name, value)
        else:  # song
            self.gen.knobs[knob_name] = value
            if player is not None:
                player._knob_overrides.setdefault(
                    self.voice_handle, {},
                )[knob_name] = value
        self._request_bar_aligned_apply()
        self.on_change()

    def _request_bar_aligned_apply(self) -> None:
        """Ask the ArrangementScreen to re-play at the next bar.

        Looks up the screen via the GuiApp's current frame so the
        drilldown doesn't need a back-reference. The screen owns the
        Tk.after debouncer + computes the ms-to-next-bar.
        """
        screen = getattr(self.app, "_current_frame", None)
        if screen is None:
            return
        handler = getattr(screen, "schedule_bar_aligned_apply", None)
        if callable(handler):
            handler()

    def _open_knob_editor(self, knob_name: str, current) -> None:
        """Pattern-knob text entry dialog (used when we don't have a slider)."""
        top = tk.Toplevel(self)
        top.title(f"Edit {knob_name}")
        top.transient(self.app.root)
        top.grab_set()
        tk.Label(top, text=f"{knob_name} =").grid(row=0, column=0, padx=8, pady=8)
        var = tk.StringVar(value=str(current) if current is not None else "")
        entry = ttk.Entry(top, textvariable=var)
        entry.grid(row=0, column=1, padx=8, pady=8)
        entry.focus_set()

        def _apply() -> None:
            raw = var.get().strip()
            if raw == "":
                self._on_revert(knob_name)
            else:
                # Coerce numeric strings to int / float; leave others as str.
                value: object = raw
                try:
                    value = int(raw)
                except ValueError:
                    try:
                        value = float(raw)
                    except ValueError:
                        pass
                self._on_knob_change(knob_name, value)
            top.destroy()

        ttk.Button(top, text="Apply", command=_apply).grid(
            row=1, column=0, columnspan=2, pady=8,
        )
        top.bind("<Return>", lambda _e: _apply())

    def _on_revert(self, knob_name: str) -> None:
        """Clear the override at the current scope and fall back."""
        player = self.app.player
        if self.scope == "part":
            bucket = self.part.knob_overrides.get(self.voice_handle, {})
            bucket.pop(knob_name, None)
            if player is not None:
                player.unset_part_knob(
                    self.part_name, self.voice_handle, knob_name,
                )
        elif self.scope == "voice":
            bucket = self.resolved.voice_defaults.get(self.gen.type_, {})
            bucket.pop(knob_name, None)
            if player is not None:
                player.unset_voice_knob(self.gen.type_, knob_name)
        else:  # song
            self.gen.knobs.pop(knob_name, None)
            if player is not None:
                player._knob_overrides.get(self.voice_handle, {}).pop(
                    knob_name, None,
                )
        self._request_bar_aligned_apply()
        self.on_change()
