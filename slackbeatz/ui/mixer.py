"""Mixer screen — per-channel volume / mute / solo strips + activity LEDs.

Shown when the user clicks the Mixer button in the Arrangement
header. The mixer reads the resolved song's gens to enumerate active
channels and wires each strip to ``Player.toggle_mute`` /
``Player.toggle_solo``.

Volume slider sends Surge's ``/param/a/amp/volume`` OSC for the
channel's surge-xt-cli instance (when present). Channels without a
live Surge instance (e.g. ch10 drums via FluidSynth) show a disabled
slider — proper MIDI CC7 routing for those is a follow-up.

Activity LED per strip — gray when silent, green when sounding.
Backed by :meth:`Player.is_channel_active` with the standard 150ms
grace window so both short hi-hats and long pads light up correctly.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slackbeatz.ui.launcher import GuiApp


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
        tk.Label(
            foot,
            text="LED green = channel sounding · volume slider drives "
                 "Surge /param/a/amp/volume (FluidSynth ch10 drums TBD)",
            fg="gray", anchor="w",
        ).pack(side="left")

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

        # Volume slider — vertical, 0..127 like MIDI velocity range.
        # Defaults to 100 (Surge global default ~= 0.79 normalised).
        surge_inst = self._surge_instance_for_channel(channel)
        vol_var = tk.IntVar(value=100)
        vol_scale = tk.Scale(
            strip, from_=127, to=0, resolution=1, length=140,
            orient="vertical", showvalue=True, variable=vol_var,
            state=("normal" if surge_inst is not None else "disabled"),
        )
        vol_scale.pack(pady=2)
        if surge_inst is not None:
            vol_scale.bind(
                "<ButtonRelease-1>",
                lambda _e, ch=channel, var=vol_var: self._on_volume_change(ch, var.get()),
            )

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
