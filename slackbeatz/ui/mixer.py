"""Mixer screen — per-channel volume / mute / solo strips + activity LEDs.

Shown when the user clicks the Mixer button in the Arrangement
header. The mixer reads the resolved song's gens to enumerate active
channels and wires each strip to ``Player.toggle_mute`` /
``Player.toggle_solo``.

Behaviour depends on the Setup's mode (see [[backend_is_setup]]):

* ``surge-standalone`` — full strip: activity LED, volume slider
  (Surge ``/param/a/amp/volume`` OSC), patch picker, FX editor,
  mute/solo. Today's behaviour.
* ``ableton-blackhole`` — Ableton owns mixing/FX, so the strip drops
  the volume slider + FX button. Patch picker, mute/solo, and the
  activity LED stay. A toolbar "Open Ableton template" button opens
  the user's starter Live Set.
* ``external`` — no Surge instance; volume slider is disabled
  (proper MIDI CC7 is a follow-up).

Activity LED per strip — gray when silent, green when sounding.
Backed by :meth:`Player.is_channel_active` with the standard 150ms
grace window so both short hi-hats and long pads light up correctly.
"""

from __future__ import annotations

import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


# Default Ableton template path. Stored in the user's Ableton library
# so it survives across SB upgrades (we don't overwrite it). The "Open
# Ableton template" button targets this; if the file is missing the
# button shows a dialog with setup instructions instead.
_DEFAULT_ABLETON_TEMPLATE = Path(
    "~/Music/Ableton/User Library/Templates/Slackbeatz.als"
).expanduser()


class MixerScreen(tk.Frame):
    def __init__(self, app: "GuiApp") -> None:
        super().__init__(app.root)
        self.app = app
        # channel_1idx → (led_label, surge_inst_or_None)
        self._strip_leds: dict[int, tk.Label] = {}
        self._poll_active = False
        self._build()

    def _build(self) -> None:
        bar = tk.Frame(self, relief="ridge", borderwidth=1)
        bar.pack(fill="x")
        ttk.Button(bar, text="← Arrangement", command=self._back).pack(side="left")
        tk.Label(bar, text="Mixer", font=("TkDefaultFont", 12, "bold")).pack(
            side="left", padx=12,
        )

        # "Open Ableton template" — only shown in ableton-blackhole mode.
        if self._mode() == "ableton-blackhole":
            ttk.Button(
                bar, text="Open Ableton template",
                command=self._open_ableton_template,
            ).pack(side="right", padx=4)

        body = tk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        resolved = self._resolved()
        if resolved is None:
            tk.Label(body, text="(no song loaded)", fg="gray").pack(pady=40)
            return

        # Collect distinct channels in gen-order, with a label per
        # channel (handle list of that channel).
        channels: dict[int, list[str]] = {}
        for handle, gen in resolved.gens.items():
            if gen.instrument is None:
                continue
            channels.setdefault(gen.instrument.channel, []).append(handle)

        for ch in sorted(channels):
            self._build_strip(body, ch, channels[ch])

        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=4)
        foot = tk.Frame(self)
        foot.pack(fill="x", padx=12, pady=8)
        if self._mode() == "ableton-blackhole":
            hint = (
                "LED green = channel sounding · Ableton owns volume/FX "
                "(mute+solo + patch picker stay here)"
            )
        else:
            hint = (
                "LED green = channel sounding · volume slider drives "
                "Surge /param/a/amp/volume (FluidSynth ch10 drums TBD)"
            )
        tk.Label(foot, text=hint, fg="gray", anchor="w").pack(side="left")

        # Start the LED + volume polling loop.
        self._poll_active = True
        self.after(100, self._poll_mixer_state)

    def _build_strip(self, parent: tk.Misc, channel: int, handles: list[str]) -> None:
        strip = tk.Frame(parent, relief="raised", borderwidth=1, padx=4, pady=4)
        strip.pack(side="left", padx=4, fill="y")

        # Header row: LED + channel number.
        header = tk.Frame(strip)
        header.pack()
        led = tk.Label(header, text="●", fg="#444",
                       font=("TkDefaultFont", 12, "bold"))
        led.pack(side="left", padx=(0, 4))
        tk.Label(header, text=f"ch {channel}",
                 font=("TkDefaultFont", 10, "bold")).pack(side="left")
        self._strip_leds[channel] = led

        tk.Label(strip, text="\n".join(handles), font=("TkDefaultFont", 9),
                 fg="gray").pack()

        surge_inst = self._surge_instance_for_channel(channel)
        # In ableton-blackhole mode Ableton owns volume + FX — skip
        # both strip controls. Patch picker + mute/solo stay because
        # they're sound design / MIDI routing, not mixing.
        ableton_owns_mix = self._mode() == "ableton-blackhole"

        if not ableton_owns_mix:
            # Volume slider — vertical, 0..127 like MIDI velocity range.
            # Defaults to 100 (Surge global default ~= 0.79 normalised).
            # Uses Scale ``command`` callback (fires on every drag move)
            # so user hears the level live while sliding — required for
            # auditioning.
            vol_var = tk.IntVar(value=100)
            last_pushed = [-1]
            def _live_volume_callback(value_str, ch=channel):
                try:
                    v = int(float(value_str))
                except (TypeError, ValueError):
                    return
                if v == last_pushed[0]:
                    return
                last_pushed[0] = v
                self._on_volume_change(ch, v)
            tk.Scale(
                strip, from_=127, to=0, resolution=1, length=140,
                orient="vertical", showvalue=True, variable=vol_var,
                command=_live_volume_callback if surge_inst is not None else None,
                state=("normal" if surge_inst is not None else "disabled"),
            ).pack(pady=2)

        # Patch button — same picker the drilldown uses. Available in
        # all modes that have a live surge instance (so the user can
        # change sound design without leaving the Mixer).
        if surge_inst is not None:
            ttk.Button(
                strip, text="Patch…", width=8,
                command=lambda inst=surge_inst: self._open_patch_picker(inst),
            ).pack(pady=1)
            if not ableton_owns_mix:
                # FX editor — Surge's per-instance FX slots. Skipped
                # under ableton-blackhole since FX lives in Ableton.
                ttk.Button(
                    strip, text="FX…", width=8,
                    command=lambda inst=surge_inst: self._open_fx_editor(inst),
                ).pack(pady=1)

        # Mute / Solo toggles.
        player = self.app.player
        muted = player is not None and channel in player._user_mutes
        soloed = player is not None and channel in player._solo
        mute_var = tk.BooleanVar(value=muted)
        solo_var = tk.BooleanVar(value=soloed)
        tk.Checkbutton(
            strip, text="Mute", variable=mute_var,
            command=lambda c=channel, v=mute_var: self._on_mute(c, v),
        ).pack()
        tk.Checkbutton(
            strip, text="Solo", variable=solo_var,
            command=lambda c=channel, v=solo_var: self._on_solo(c, v),
        ).pack()

    def _open_patch_picker(self, surge_inst) -> None:
        from slackbeatz.ui.patch_picker import PatchPickerDialog
        PatchPickerDialog(self.app, surge_inst)

    def _open_fx_editor(self, surge_inst) -> None:
        from slackbeatz.ui.fx_editor import FxEditorDialog
        FxEditorDialog(self.app, surge_inst)

    def _surge_instance_for_channel(self, channel_1idx: int):
        runtime = getattr(self.app, "live_runtime", None)
        if runtime is None:
            return None
        for inst in getattr(runtime, "surge_instances", []) or []:
            if getattr(inst.config, "channel_1idx", None) == channel_1idx:
                return inst
        return None

    # ----- polling -----------------------------------------------------

    def _poll_mixer_state(self) -> None:
        if not self._poll_active:
            return
        try:
            self._tick_leds()
        finally:
            try:
                self.after(100, self._poll_mixer_state)
            except tk.TclError:
                self._poll_active = False

    def _tick_leds(self) -> None:
        p = self.app.player
        if p is None:
            return
        for ch, led in self._strip_leds.items():
            try:
                active = p.is_channel_active(ch, window_ms=150.0)
                led.config(fg="#22cc44" if active else "#444")
            except (tk.TclError, AttributeError):
                pass

    # ----- actions -----------------------------------------------------

    def _on_volume_change(self, channel: int, value_127: int) -> None:
        inst = self._surge_instance_for_channel(channel)
        if inst is None:
            return
        # Surge scene volume is normalised 0..1. MIDI 0..127 → 0..1.
        normalised = max(0.0, min(1.0, value_127 / 127.0))
        try:
            inst.set_param("/param/a/amp/volume", normalised)
        except Exception:
            pass

    def _on_mute(self, channel: int, var: tk.BooleanVar) -> None:
        if self.app.player is None:
            return
        self.app.player.toggle_mute(channel)
        var.set(channel in self.app.player._user_mutes)

    def _on_solo(self, channel: int, var: tk.BooleanVar) -> None:
        if self.app.player is None:
            return
        self.app.player.toggle_solo(channel)
        var.set(channel in self.app.player._solo)

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

    def _mode(self) -> str:
        """Current Setup mode — drives strip layout + toolbar buttons."""
        resolved = self._resolved()
        if resolved is None:
            return "external"
        return getattr(resolved.setup, "mode", "external")

    def _open_ableton_template(self) -> None:
        """Open the user's Slackbeatz Ableton template (or instruct).

        If the template exists at the default path, opens it (which
        launches Ableton + loads it). If not, shows a dialog with the
        steps to create one — first-time users see the path + the
        BlackHole channel layout they need to wire up.
        """
        if _DEFAULT_ABLETON_TEMPLATE.is_file():
            try:
                subprocess.Popen(["open", str(_DEFAULT_ABLETON_TEMPLATE)])
            except OSError as e:
                messagebox.showerror(
                    "Couldn't open Ableton template",
                    f"open {_DEFAULT_ABLETON_TEMPLATE}\n\n{e}",
                )
            return
        # Not present yet — explain how to set one up.
        msg = (
            f"No Slackbeatz template found at:\n  {_DEFAULT_ABLETON_TEMPLATE}\n\n"
            "Set one up once and Slackbeatz will reopen it each time:\n\n"
            "1. Open Ableton Live, create a new Live Set.\n"
            "2. Audio prefs: Audio Input Device = BlackHole 16ch.\n"
            "3. Input Config: enable channel pairs 3/4, 5/6, 7/8, 9/10, 11/12.\n"
            "4. Add 5 Audio tracks with Audio From → Ext. In:\n"
            "     Track 1 (audio): 3/4  = lead\n"
            "     Track 2 (audio): 5/6  = bass\n"
            "     Track 3 (audio): 7/8  = pad\n"
            "     Track 4 (audio): 9/10 = candy\n"
            "     Track 5 (audio): 11/12= sub\n"
            "5. Add 1 MIDI track for drums:\n"
            "     MIDI From: slackbeatz-drums (Channel: All)\n"
            "     Add any Drum Rack / sampler instrument\n"
            "     Monitor: In, arm the track\n"
            "6. Set Monitor = In on all audio tracks; drop FX as desired.\n"
            "7. (Optional) MIDI tracks subscribed to slackbeatz-chord /\n"
            "   slackbeatz-root for arp / triad-builder tools.\n"
            "8. File → Save Live Set As… save to:\n"
            f"     {_DEFAULT_ABLETON_TEMPLATE.parent}/Slackbeatz.als\n\n"
            "Open Templates folder in Finder now?"
        )
        if messagebox.askyesno("Set up Ableton template", msg):
            templates_dir = _DEFAULT_ABLETON_TEMPLATE.parent
            templates_dir.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.Popen(["open", str(templates_dir)])
            except OSError:
                pass
