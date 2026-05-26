"""Open-Ableton-template helper.

Used from the Setup screen's "Open Ableton template" button (only
shown in ``ableton`` mode). Picks ``Slackbeatz-<style>.als`` first
based on the song's current style, falls back to ``Slackbeatz.als``,
or surfaces a setup-instructions dialog if neither exists yet.

Lives in its own module so the Setup screen + any future callers
share one source of truth for the template path conventions + the
first-time setup walkthrough text.
"""

from __future__ import annotations

import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox


# Default Ableton template path. Per-style templates use the same
# directory with ``Slackbeatz-<style>.als`` naming.
DEFAULT_ABLETON_TEMPLATE = Path(
    "~/Music/Ableton/User Library/Templates/Slackbeatz.als"
).expanduser()


def per_style_template(style: str | None) -> Path:
    """Resolve ``Slackbeatz-<style>.als`` (caller checks ``.is_file()``)."""
    if not style:
        return DEFAULT_ABLETON_TEMPLATE
    return DEFAULT_ABLETON_TEMPLATE.with_name(f"Slackbeatz-{style}.als")


def open_ableton_template(parent: tk.Misc, style: str | None) -> None:
    """Open the per-style template (falling back to default).

    Lookup order:
      1. ``Slackbeatz-<style>.als`` — if the song has an explicit
         style and the file exists.
      2. ``Slackbeatz.als`` — the catch-all default template.

    If neither exists, shows first-time setup instructions describing
    the per-role + per-drum-split + transport MIDI tracks the user
    needs to wire in their Live Set.
    """
    style_path = per_style_template(style)
    target = style_path if style_path.is_file() else DEFAULT_ABLETON_TEMPLATE
    if target.is_file():
        try:
            subprocess.Popen(["open", str(target)])
        except OSError as e:
            messagebox.showerror(
                "Couldn't open Ableton template",
                f"open {target}\n\n{e}",
                parent=parent,
            )
        return
    # Neither default nor style-specific exists — walk the user
    # through the setup once.
    style_hint = (
        f"For per-style templates, save as Slackbeatz-{style}.als.\n"
        if style else ""
    )
    msg = (
        f"No Slackbeatz template found at:\n  {DEFAULT_ABLETON_TEMPLATE}\n\n"
        f"{style_hint}"
        "Set up the default template once and SB reopens it each time:\n\n"
        "1. Open Ableton Live, create a new Live Set.\n"
        "2. Add 5 MIDI tracks for the synth voices:\n"
        "     Track 1: MIDI From = slackbeatz-lead\n"
        "     Track 2: MIDI From = slackbeatz-bass\n"
        "     Track 3: MIDI From = slackbeatz-pad\n"
        "     Track 4: MIDI From = slackbeatz-candy\n"
        "     Track 5: MIDI From = slackbeatz-sub\n"
        "   Drop any Ableton instrument on each (Wavetable, Operator,\n"
        "   Bass, Analog, third-party AU/VST, etc.).\n"
        "3. Add MIDI tracks for splittable drums (one per drum):\n"
        "     Track: MIDI From = slackbeatz-drum-kick   + Drum Rack\n"
        "     Track: MIDI From = slackbeatz-drum-snare  + Drum Rack\n"
        "     Track: MIDI From = slackbeatz-drum-hats   + Drum Rack\n"
        "     Track: MIDI From = slackbeatz-drum-other  + Drum Rack\n"
        "     (also slackbeatz-drum-clap, -ohats if you've defined them)\n"
        "4. Set MIDI To on each track to a unique track destination\n"
        "   so the instruments actually sound. Arm each track.\n"
        "5. (Optional) MIDI tracks subscribed to slackbeatz-chord /\n"
        "   slackbeatz-root for arp / triad-builder tools.\n"
        "6. Settings → Link/MIDI: Sync IN from slackbeatz-transport-out,\n"
        "   Sync OUT to slackbeatz-transport-in for press-play-in-either.\n"
        "7. File → Save Live Set As… save to:\n"
        f"     {DEFAULT_ABLETON_TEMPLATE.parent}/Slackbeatz.als\n\n"
        "Open Templates folder in Finder now?"
    )
    if messagebox.askyesno("Set up Ableton template", msg, parent=parent):
        templates_dir = DEFAULT_ABLETON_TEMPLATE.parent
        templates_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["open", str(templates_dir)])
        except OSError:
            pass
