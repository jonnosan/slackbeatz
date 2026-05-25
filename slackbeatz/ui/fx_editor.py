"""FX editor dialog — full Surge FX1 / FX2 type + param control.

Opened from the voice drilldown's ``FX…`` button. Shows one frame
per FX slot (A1, A2) with:

* FX type dropdown — every entry in
  :data:`slackbeatz.surge_host.FX_CATALOG` (Off / Delay / Reverb /
  Phaser / Chorus / Flanger / Distortion / Rotary / Vocoder /
  Ring Mod / ...). Selecting a new type sends the appropriate
  OSC ``/param/fx/a/<slot>/type`` write, which makes Surge swap
  the FX module + reset its inner params.
* Power toggle — ON / OFF (the FX deactivate bit, distinct from
  type=Off so the type stays selected when the user just wants
  to mute the slot temporarily).
* Per-FX sliders — sourced from :data:`FX_CATALOG`'s
  ``params`` tuple (a 3-or-so subset of useful knobs per type).
  Each slider is a 0..1 normalised float; Surge resolves the
  display unit on its side.

All writes are live — the user hears the change in milliseconds.
There's no persistence layer today; closing the dialog leaves
whatever the user dialed in active on the live Surge instance,
but a song reload will reset to the patch's defaults.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from slackbeatz.surge_host import FX_CATALOG, fx_addr

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


class FxEditorDialog:
    """Modeless Toplevel — pick FX type + tweak params per slot."""

    def __init__(self, app: "GuiApp", surge_instance) -> None:
        self.app = app
        self.inst = surge_instance
        self.win = tk.Toplevel(app.root)
        self.win.title(
            f"FX — {surge_instance.config.role} "
            f"(ch {surge_instance.config.channel_1idx})"
        )
        self.win.transient(app.root)
        self._build()

    def _build(self) -> None:
        header = tk.Label(
            self.win,
            text=(
                "FX A1 and A2 are Surge's scene-A FX slots, processed in "
                "order. Changes apply live to the running surge-xt-cli; "
                "they do not persist to .sb yet."
            ),
            wraplength=520, justify="left", fg="#555", anchor="w",
        )
        header.pack(fill="x", padx=12, pady=(8, 6))

        for slot in (1, 2):
            self._build_slot_frame(slot)

        bottom = tk.Frame(self.win)
        bottom.pack(fill="x", padx=12, pady=(4, 10))
        ttk.Button(bottom, text="Close", command=self.win.destroy).pack(side="right")

    def _build_slot_frame(self, slot: int) -> None:
        frame = tk.LabelFrame(self.win, text=f"FX A{slot}", padx=8, pady=6)
        frame.pack(fill="x", padx=12, pady=4)

        # Type dropdown.
        type_row = tk.Frame(frame)
        type_row.pack(fill="x", pady=2)
        tk.Label(type_row, text="Type:", width=10, anchor="w").pack(side="left")

        sorted_items = sorted(FX_CATALOG.items(), key=lambda kv: (kv[0] != 0, kv[0]))
        names_by_id = {tid: spec.name for tid, spec in sorted_items}
        ids_by_name = {spec.name: tid for tid, spec in sorted_items}

        cur_type_raw = self.inst.get_value(fx_addr(slot, "type"))
        cur_type_id = int(cur_type_raw) if cur_type_raw is not None else 0
        cur_name = names_by_id.get(cur_type_id, "Off")

        type_var = tk.StringVar(value=cur_name)
        type_combo = ttk.Combobox(
            type_row, textvariable=type_var, state="readonly",
            values=[name for _id, name in [(tid, names_by_id[tid]) for tid in sorted(names_by_id)]],
            width=18,
        )
        # Sort by id with Off first so the dropdown matches FX_CATALOG order.
        type_combo["values"] = [names_by_id[tid] for tid, _ in sorted_items]
        type_combo.pack(side="left", padx=4)

        # Power toggle (deactivate bit; Surge uses 1=off, 0=on).
        cur_deact = self.inst.get_value(fx_addr(slot, "deactivate")) or 0.0
        power_var = tk.BooleanVar(value=cur_deact < 0.5)
        power_cb = ttk.Checkbutton(
            type_row, text="Power", variable=power_var,
            command=lambda: self.inst.set_param(
                fx_addr(slot, "deactivate"), 0.0 if power_var.get() else 1.0,
            ),
        )
        power_cb.pack(side="left", padx=8)

        # Param sliders — rebuild on type change.
        params_frame = tk.Frame(frame)
        params_frame.pack(fill="x", pady=4)

        def _build_param_sliders(type_id: int) -> None:
            for child in list(params_frame.winfo_children()):
                child.destroy()
            spec = FX_CATALOG.get(type_id)
            if spec is None or not spec.params:
                tk.Label(
                    params_frame,
                    text="(no editable params for this FX type)",
                    fg="gray", anchor="w",
                ).pack(fill="x")
                return
            for label, p_idx in spec.params:
                row = tk.Frame(params_frame)
                row.pack(fill="x", pady=1)
                tk.Label(row, text=label, width=12, anchor="w").pack(side="left")
                addr = fx_addr(slot, "param", p_idx)
                cur = self.inst.get_value(addr) or 0.0
                slider = tk.Scale(
                    row, from_=0.0, to=1.0, resolution=0.01,
                    orient="horizontal", length=220, showvalue=True,
                )
                slider.set(float(cur))
                slider.pack(side="left", padx=4)
                # Live write on each release — same idiom as the
                # scope_drilldown knob rows.
                slider.bind(
                    "<ButtonRelease-1>",
                    lambda _e, a=addr, s=slider: self.inst.set_param(a, s.get()),
                )

        _build_param_sliders(cur_type_id)

        def _on_type_change(_e=None) -> None:
            name = type_var.get()
            new_id = ids_by_name.get(name)
            if new_id is None:
                return
            self.inst.set_param(fx_addr(slot, "type"), float(new_id))
            # Re-query the new type's docs so labels stay live.
            try:
                self.inst.query_fx_slot_docs(slot)
            except Exception:
                pass
            _build_param_sliders(new_id)
        type_combo.bind("<<ComboboxSelected>>", _on_type_change)
