"""Tiny Tk control window for ``slackbeatz live`` and ``slackbeatz repl``.

FluidSynth itself is headless, so we open a small native window that
sends shell commands (``gain N``, ``set synth.reverb.* N``,
``prog C N``, ``select C SF B P``) to its stdin every time the user
changes a slider, toggles a checkbox, or picks a new instrument. No
external dependency — Tk ships with CPython on macOS, Linux, and
Windows (Homebrew users need ``brew install python-tk@<version>``).

The shell-command names below match FluidSynth 2.x's interactive
shell (verified against ``help general`` and the documented ``set``
runtime-settings syntax).

Architecture:

* Tk needs to run on the main thread. The caller runs the scheduler
  (or REPL input loop) in a background thread (``daemon=True``) and
  calls ``run_tweak_gui`` on the main thread.
* Closing the window calls ``on_close`` which signals the caller to
  shut down (terminate FluidSynth, kill the daemon thread implicitly).
"""

from __future__ import annotations

from typing import IO, Callable


# Slider definitions — (label, fluidsynth shell command template, low, high, default).
# Values placed at sensible centre points so the GUI is immediately useful
# without having to twiddle every slider to a starting position.
_SLIDERS: list[tuple[str, str, float, float, float]] = [
    ("Master gain",       "gain {v:.2f}",                          0.0,   2.0,  0.6),
    ("Reverb room size",  "set synth.reverb.room-size {v:.2f}",    0.0,   1.0,  0.4),
    ("Reverb damp",       "set synth.reverb.damp {v:.2f}",         0.0,   1.0,  0.3),
    ("Reverb level",      "set synth.reverb.level {v:.2f}",        0.0,   1.0,  0.7),
    ("Reverb width",      "set synth.reverb.width {v:.0f}",        0.0, 100.0, 80.0),
    ("Chorus depth",      "set synth.chorus.depth {v:.1f}",        0.0,  50.0,  8.0),
    ("Chorus level",      "set synth.chorus.level {v:.2f}",        0.0,  10.0,  2.0),
    ("Chorus speed",      "set synth.chorus.speed {v:.2f}",        0.29,  5.0,  0.3),
]


# General-MIDI program list (program 0-127). Drum-bank presets live
# below — channel 10 (1-indexed) gets a different picker. Names follow
# the GM Level 1 spec.
_GM_PROGRAMS: list[str] = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord",
    "Clavinet", "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar",
    "Guitar Harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)",
    "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings",
    "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "SynthStrings 1", "SynthStrings 2",
    "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn",
    "Brass Section", "SynthBrass 1", "SynthBrass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle",
    "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)",
    "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum",
    "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]

# GM drum-kit presets on bank 128. FluidR3 / GeneralUser populate
# only a subset; the rest fall back to the Standard Set silently.
_GM_DRUM_KITS: list[tuple[int, str]] = [
    (0,   "Standard Set"),
    (8,   "Room Set"),
    (16,  "Power Set"),
    (24,  "Electronic Set"),
    (25,  "TR-808"),
    (32,  "Jazz Set"),
    (40,  "Brush Set"),
    (48,  "Orchestral Set"),
    (56,  "Sound FX Set"),
]


# Slackbeatz-conventional channel labels for the GM setup. The
# instruments tab uses these as hints so the dropdown for ch 2 reads
# "ch 2 — bass" not just "ch 2". Songs using a different setup still
# get the right MIDI behaviour; only the *label* may be misleading.
_CHANNEL_LABELS: dict[int, str] = {
    1:  "lead",
    2:  "bass",
    3:  "pad / chords",
    4:  "candy / FX",
    10: "drums",
}


def run_tweak_gui(
    fs_stdin: IO[bytes],
    *,
    initial_gain: float | None = None,
    initial_reverb_room: float | None = None,
    initial_programs: dict[int, int] | None = None,
    player=None,
    on_close: Callable[[], None] | None = None,
) -> None:
    """Open the tweak window. Blocks until the user closes it.

    Parameters
    ----------
    fs_stdin:
        FluidSynth's stdin pipe (from ``subprocess.Popen(..., stdin=PIPE)``).
        Slider movements / dropdown changes write shell commands to
        this file handle.
    initial_gain, initial_reverb_room:
        Override the slider defaults to match values the user passed via
        ``--gain`` / ``--reverb`` on the CLI.
    initial_programs:
        Optional ``{channel_1_indexed: gm_program}`` map. Pre-populates
        the per-channel program dropdowns so the GUI starts with the
        same patch assignments slackbeatz is about to send. Channel 10
        is treated as drums (the value is a drum-kit preset index).
    on_close:
        Called when the user closes the window. The caller typically
        uses this to terminate the FluidSynth subprocess.
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as e:
        # The Homebrew python@3.x formulas don't bundle Tk — `import
        # tkinter` raises ModuleNotFoundError: No module named '_tkinter'.
        # macOS users typically need `brew install python-tk@3.12` (or
        # the version matching their venv); the official python.org
        # installer includes Tk natively.
        import sys
        py_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise RuntimeError(
            f"Tk is unavailable in this Python build ({e}). "
            f"On macOS, install Tk for your Python via:\n"
            f"  brew install python-tk@{py_minor}\n"
            f"Or use the REPL's inline /tweak commands instead "
            f"(see /help in `slackbeatz repl`)."
        ) from e

    def send(cmd: str) -> None:
        try:
            fs_stdin.write((cmd + "\n").encode("utf-8"))
            fs_stdin.flush()
        except (BrokenPipeError, OSError):
            # FluidSynth already gone; the parent will handle shutdown.
            pass

    root = tk.Tk()
    root.title("slackbeatz live — tweak")
    root.minsize(440, 480)

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=6, pady=6)

    # ------------------------------------------------------------------
    # Transport tab — only created if a Player is provided. Drives the
    # shared transport state: phrase load, play/stop, tempo, style,
    # seed, loop, reroll.
    # ------------------------------------------------------------------
    if player is not None:
        from slackbeatz.player import KNOWN_STYLES

        transport = ttk.Frame(notebook)
        notebook.add(transport, text="Transport")

        # Now-playing label + Play/Stop button.
        nowplaying_var = tk.StringVar(value="(no song loaded)")
        nowplaying_lbl = ttk.Label(
            transport, textvariable=nowplaying_var,
            font=("TkDefaultFont", 12, "bold"),
        )
        nowplaying_lbl.pack(padx=10, pady=(10, 4), anchor="w")

        playstop_var = tk.StringVar(value="▶ Play")

        def _refresh_nowplaying() -> None:
            # Called on player state change.
            if player.title:
                src = f'"{player.title}"'
            elif player.current_phrase:
                src = f'"{player.current_phrase}"'
            elif player.current_song_path:
                src = player.current_song_path.name
            else:
                src = "(no song loaded)"
            state = "▶ playing" if player.is_playing else "■ stopped"
            nowplaying_var.set(f"{state}: {src}")
            playstop_var.set("■ Stop" if player.is_playing else "▶ Play")

        # Player notifies the GUI by invoking on_state_change. Tk widgets
        # must be touched from the main thread, so we marshal via after().
        def _on_state_change_from_thread() -> None:
            root.after(0, _refresh_nowplaying)
        player.on_state_change = _on_state_change_from_thread

        button_row = ttk.Frame(transport)
        button_row.pack(fill="x", padx=10, pady=4)
        ttk.Button(
            button_row, textvariable=playstop_var, width=10,
            command=lambda: (player.toggle(), _refresh_nowplaying()),
        ).pack(side="left", padx=2)
        ttk.Button(
            button_row, text="⟳ Re-roll", width=12,
            command=lambda: (player.reroll_seed(), _refresh_nowplaying()),
        ).pack(side="left", padx=2)
        ttk.Button(
            button_row, text="Reset overrides", width=14,
            command=lambda: (player.reset_overrides(), _refresh_nowplaying()),
        ).pack(side="left", padx=2)

        def _on_save():
            # Native macOS file picker. The dialog blocks the main
            # thread; player.save_state acquires its own lock so a
            # concurrent slider movement / phrase load just queues.
            from tkinter import filedialog
            initial = (
                f"{player.title.lower().replace(' ', '_')}.sb"
                if player.title else "song.sb"
            )
            path = filedialog.asksaveasfilename(
                title="Save current state as…",
                defaultextension=".sb",
                initialfile=initial,
                filetypes=[("Slackbeatz songs", "*.sb"), ("All files", "*.*")],
            )
            if path:
                status = player.save_state(path)
                print(status)  # also goes to the REPL terminal
        ttk.Button(
            button_row, text="💾 Save", width=8, command=_on_save,
        ).pack(side="left", padx=2)

        # Tempo slider — None / auto when the slider is at its left edge.
        ttk.Separator(transport, orient="horizontal").pack(fill="x", padx=10, pady=8)
        tempo_row = ttk.Frame(transport); tempo_row.pack(fill="x", padx=10, pady=2)
        ttk.Label(tempo_row, text="Tempo (BPM)", width=14, anchor="w").pack(side="left")
        tempo_var = tk.IntVar(value=120 if player.tempo_override is None else player.tempo_override)

        def _on_tempo(value):
            v = int(float(value))
            player.set_tempo(v)
        tempo_scale = tk.Scale(
            tempo_row, from_=60, to=200, orient="horizontal",
            variable=tempo_var, showvalue=True, length=240,
            command=_on_tempo,
        )
        tempo_scale.pack(side="left", fill="x", expand=True)
        ttk.Button(
            tempo_row, text="Auto", width=6,
            command=lambda: (player.set_tempo(None), tempo_var.set(120)),
        ).pack(side="left", padx=4)

        # Style dropdown.
        style_row = ttk.Frame(transport); style_row.pack(fill="x", padx=10, pady=4)
        ttk.Label(style_row, text="Style", width=14, anchor="w").pack(side="left")
        style_choices = ["(auto)"] + list(KNOWN_STYLES)
        style_var = tk.StringVar(value="(auto)")
        style_cb = ttk.Combobox(
            style_row, values=style_choices, state="readonly",
            textvariable=style_var, width=20,
        )

        def _on_style(_event):
            choice = style_var.get()
            player.set_style(None if choice == "(auto)" else choice)
        style_cb.bind("<<ComboboxSelected>>", _on_style)
        style_cb.pack(side="left", padx=2)

        # Seed offset entry.
        seed_row = ttk.Frame(transport); seed_row.pack(fill="x", padx=10, pady=4)
        ttk.Label(seed_row, text="Seed offset", width=14, anchor="w").pack(side="left")
        seed_var = tk.StringVar(value=str(player.seed_offset))
        seed_entry = ttk.Entry(seed_row, textvariable=seed_var, width=14)
        seed_entry.pack(side="left", padx=2)

        def _on_seed_apply():
            try:
                player.set_seed_offset(int(seed_var.get()))
            except ValueError:
                seed_var.set(str(player.seed_offset))
        ttk.Button(seed_row, text="Apply", width=8, command=_on_seed_apply).pack(side="left", padx=2)
        seed_entry.bind("<Return>", lambda _e: _on_seed_apply())

        # Seek to bar input.
        seek_row = ttk.Frame(transport); seek_row.pack(fill="x", padx=10, pady=4)
        ttk.Label(seek_row, text="Seek to bar", width=14, anchor="w").pack(side="left")
        seek_bar_var = tk.StringVar(value="1")
        ttk.Entry(seek_row, textvariable=seek_bar_var, width=6).pack(side="left", padx=2)
        ttk.Label(seek_row, text=" beat ").pack(side="left")
        seek_beat_var = tk.StringVar(value="0")
        ttk.Entry(seek_row, textvariable=seek_beat_var, width=6).pack(side="left", padx=2)

        def _on_seek():
            try:
                bar = int(seek_bar_var.get())
                beat = float(seek_beat_var.get() or 0)
            except ValueError:
                return
            player.seek(bar=bar, beat=beat)
            _refresh_nowplaying()
        ttk.Button(seek_row, text="Go", width=6, command=_on_seek).pack(side="left", padx=4)

        # Loop + preserve-position toggles on one row.
        toggle_row = ttk.Frame(transport); toggle_row.pack(fill="x", padx=10, pady=4)
        loop_var = tk.IntVar(value=1 if player.loop else 0)
        ttk.Checkbutton(
            toggle_row, text="Loop on song end", variable=loop_var,
            command=lambda: player.set_loop(bool(loop_var.get())),
        ).pack(side="left", padx=2)
        preserve_var = tk.IntVar(value=1 if player.preserve_position else 0)
        ttk.Checkbutton(
            toggle_row, text="Preserve bar across param changes",
            variable=preserve_var,
            command=lambda: player.set_preserve_position(bool(preserve_var.get())),
        ).pack(side="left", padx=8)

        # MIDI Clock output.
        clock_row = ttk.Frame(transport); clock_row.pack(fill="x", padx=10, pady=4)
        clock_var = tk.IntVar(value=1 if player.emit_clock else 0)
        ttk.Checkbutton(
            clock_row, text="Send MIDI Clock (sync external gear)",
            variable=clock_var,
            command=lambda: player.set_emit_clock(bool(clock_var.get())),
        ).pack(side="left", padx=2)

        ttk.Label(
            transport,
            text="Type a phrase at the REPL prompt to load a song. "
                 "Tempo / Style / Seed restart the current song with the "
                 "new value — by default at the current bar (uncheck "
                 "‘Preserve bar’ to restart from bar 1).",
            wraplength=400, justify="left", foreground="#666",
        ).pack(padx=10, pady=(10, 4), anchor="w")

        _refresh_nowplaying()

    # ------------------------------------------------------------------
    # Generators tab — per-gen knob sliders. Only built when a Player
    # is wired in. Rebuilds itself every time the loaded song changes
    # (different songs have different gens with different (type, style)
    # pairs needing different sliders).
    # ------------------------------------------------------------------
    if player is not None:
        from slackbeatz.player import KNOB_SPECS

        gens_tab = ttk.Frame(notebook)
        notebook.add(gens_tab, text="Generators")

        ttk.Label(
            gens_tab,
            text="Per-gen knob sliders. Move one → song restarts at "
                 "current bar with the override applied. Overrides "
                 "survive style / tempo / seed changes until /reset.",
            wraplength=400, justify="left", foreground="#444",
        ).pack(padx=10, pady=(8, 4), anchor="w")

        # Scrollable area — songs can have 5-8 gens × 5-7 knobs = a lot
        # of rows.
        gens_canvas = tk.Canvas(gens_tab, borderwidth=0, highlightthickness=0)
        gens_scrollbar = ttk.Scrollbar(gens_tab, orient="vertical", command=gens_canvas.yview)
        gens_inner = ttk.Frame(gens_canvas)

        def _on_gens_inner_configure(_event):
            gens_canvas.configure(scrollregion=gens_canvas.bbox("all"))
        gens_inner.bind("<Configure>", _on_gens_inner_configure)
        gens_canvas.create_window((0, 0), window=gens_inner, anchor="nw")
        gens_canvas.configure(yscrollcommand=gens_scrollbar.set)
        gens_canvas.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=4)
        gens_scrollbar.pack(side="right", fill="y", padx=(0, 6), pady=4)

        # Track the gen layout we built widgets for so we don't tear
        # down + recreate ~130 widgets on every state change. Most
        # state changes (knob nudge, mute, tempo, seed, etc.) keep the
        # gen list identical — the layout only changes on a new
        # phrase / file / style override.
        last_layout: dict[str, object] = {"key": None}

        # Coalesce multiple queued rebuilds — if the user drags a
        # slider rapidly, the Player fires on_state_change for each
        # debounced commit, and without this the rebuild queue grew
        # faster than Tk could drain it (the visible symptom: the
        # macOS beachball).
        rebuild_pending: dict[str, bool] = {"flag": False}

        def _request_rebuild():
            if rebuild_pending["flag"]:
                return
            rebuild_pending["flag"] = True
            root.after_idle(_do_rebuild)

        def _do_rebuild():
            rebuild_pending["flag"] = False
            _rebuild_gens_tab()

        # State: rebuilt only when the gen layout actually changes.
        def _rebuild_gens_tab():
            # Use the Player's cached resolved song — calling
            # _resolve_current here would do a redundant compose +
            # parse + resolve on top of the one the Player already
            # did to play the song. Tk thread cost is the
            # destroy/create widget work alone (~5-30ms), not the
            # 50ms+ resolve.
            resolved = player.current_resolved
            if resolved is None:
                # Empty state — show a placeholder and remember it as
                # the layout so we don't redraw next state change.
                if last_layout["key"] != "EMPTY":
                    for child in gens_inner.winfo_children():
                        child.destroy()
                    ttk.Label(
                        gens_inner,
                        text="(load a song to see its gens — type a phrase at the prompt)",
                        foreground="#888",
                    ).pack(padx=10, pady=20)
                    last_layout["key"] = "EMPTY"
                return

            # Layout key = sorted tuple of (handle, type_, style). If
            # this hasn't changed, the existing widgets are fine — the
            # slider values track the current overrides via the var
            # bindings the closures captured. Skip the rebuild.
            layout_key = tuple(sorted(
                (h, g.type_, g.style) for h, g in resolved.gens.items()
            ))
            if layout_key == last_layout.get("key"):
                return

            # Layout changed — rebuild from scratch.
            for child in gens_inner.winfo_children():
                child.destroy()
            last_layout["key"] = layout_key

            overrides = player.get_knob_overrides()

            for handle, gen in resolved.gens.items():
                # Gen header row.
                row = ttk.Frame(gens_inner, borderwidth=1, relief="solid")
                row.pack(fill="x", padx=4, pady=4)
                ttk.Label(
                    row,
                    text=f"{handle}  ({gen.type_} / {gen.style})",
                    font=("TkDefaultFont", 10, "bold"),
                ).pack(anchor="w", padx=4, pady=(2, 0))

                specs = KNOB_SPECS.get(gen.type_, [])
                if not specs:
                    ttk.Label(row, text="(no tweakable knobs)", foreground="#888").pack(
                        anchor="w", padx=8, pady=2,
                    )
                    continue
                for knob_name, lo, hi, default, kind in specs:
                    knob_row = ttk.Frame(row)
                    knob_row.pack(fill="x", padx=8, pady=1)
                    ttk.Label(knob_row, text=knob_name, width=14, anchor="w").pack(side="left")
                    # Resolve initial value: override > gen.knobs > default.
                    if handle in overrides and knob_name in overrides[handle]:
                        value = overrides[handle][knob_name]
                    elif knob_name in gen.knobs:
                        value = gen.knobs[knob_name]
                    else:
                        value = default
                    is_int = kind == "int"
                    if is_int:
                        var = tk.IntVar(value=int(value))
                        resolution = 1
                    else:
                        var = tk.DoubleVar(value=float(value))
                        resolution = (hi - lo) / 100 if hi > lo else 0.01

                    # Debounce — Tk's Scale command fires on every pixel
                    # of drag, which would thrash the recompose loop.
                    # We schedule the actual override apply 100ms after
                    # the last drag event.
                    pending = {"after_id": None}

                    def _commit(v, h=handle, n=knob_name):
                        try:
                            cast = int(float(v)) if is_int else float(v)
                        except ValueError:
                            return
                        player.set_knob(h, n, cast)

                    def _on_drag(v, p=pending, h=handle, n=knob_name):
                        if p["after_id"] is not None:
                            try:
                                root.after_cancel(p["after_id"])
                            except Exception:
                                pass
                        p["after_id"] = root.after(120, lambda: _commit(v, h, n))

                    scale = tk.Scale(
                        knob_row, from_=lo, to=hi, resolution=resolution,
                        orient="horizontal", variable=var,
                        showvalue=True, length=180,
                        command=_on_drag,
                    )
                    scale.pack(side="left", fill="x", expand=True)

                    def _reset_knob(h=handle, n=knob_name, v=var, d=default):
                        player.unset_knob(h, n)
                        v.set(d)
                    ttk.Button(
                        knob_row, text="↺", width=2,
                        command=_reset_knob,
                    ).pack(side="left", padx=2)

        _request_rebuild()

        # Rebuild whenever the player's state changes (new song
        # loaded, style/seed changed → potentially different gen
        # layout). Player calls on_state_change from arbitrary
        # threads, so marshal via after. We layer this on top of any
        # existing handler — the Transport tab already set
        # on_state_change to refresh its now-playing label.
        prev_state_handler = player.on_state_change

        def _combined_state_change() -> None:
            try:
                prev_state_handler()
            except Exception:
                pass
            # Coalesces multiple rapid state changes into one rebuild.
            # No-op if the gen layout hasn't actually changed, so
            # knob/mute/tempo tweaks (the common case during live
            # tweaking) don't trigger any widget churn.
            _request_rebuild()
        player.on_state_change = _combined_state_change

    # ------------------------------------------------------------------
    # Effects tab — gain / reverb / chorus sliders + on-off toggles.
    # ------------------------------------------------------------------
    effects = ttk.Frame(notebook)
    notebook.add(effects, text="Effects")

    overrides: dict[str, float] = {}
    if initial_gain is not None:
        overrides["Master gain"] = initial_gain
    if initial_reverb_room is not None:
        overrides["Reverb room size"] = initial_reverb_room

    for label, cmd_tmpl, low, high, default in _SLIDERS:
        value = overrides.get(label, default)
        row = ttk.Frame(effects)
        row.pack(fill="x", padx=10, pady=2)
        ttk.Label(row, text=label, width=18, anchor="w").pack(side="left")
        var = tk.DoubleVar(value=value)
        resolution = (high - low) / 200 if (high - low) > 0 else 0.01
        scale = tk.Scale(
            row, from_=low, to=high,
            resolution=resolution,
            orient="horizontal", variable=var,
            showvalue=True, length=240,
            command=lambda v, c=cmd_tmpl: send(c.format(v=float(v))),
        )
        scale.pack(side="left", fill="x", expand=True)

    toggles = ttk.Frame(effects)
    toggles.pack(fill="x", padx=10, pady=(8, 4))
    rev_var = tk.IntVar(value=1)
    cho_var = tk.IntVar(value=1)
    ttk.Checkbutton(
        toggles, text="Reverb on", variable=rev_var,
        command=lambda: send(f"set synth.reverb.active {rev_var.get()}"),
    ).pack(side="left", padx=6)
    ttk.Checkbutton(
        toggles, text="Chorus on", variable=cho_var,
        command=lambda: send(f"set synth.chorus.active {cho_var.get()}"),
    ).pack(side="left", padx=6)

    ttk.Label(
        effects,
        text="Move a slider to tweak the synth live. Close window or "
             "hit Ctrl+C in the terminal to stop.",
        wraplength=400, justify="center", foreground="#666",
    ).pack(padx=10, pady=(8, 4))

    # ------------------------------------------------------------------
    # Instruments tab — per-channel program-change dropdowns. Channel
    # 10 gets the drum-kit picker; the other 15 channels each get a
    # 128-name GM program dropdown.
    # ------------------------------------------------------------------
    instruments = ttk.Frame(notebook)
    notebook.add(instruments, text="Instruments")

    ttk.Label(
        instruments,
        text="Pick a GM program for each channel. Slackbeatz typically "
             "uses ch 1 (lead), ch 2 (bass), ch 3 (pad), ch 4 (candy), "
             "ch 10 (drums).",
        wraplength=400, justify="left", foreground="#444",
    ).pack(padx=10, pady=(8, 4), anchor="w")

    initial_programs = initial_programs or {}

    # Scrollable area so 16 rows fit comfortably even on small windows.
    canvas = tk.Canvas(instruments, borderwidth=0, highlightthickness=0)
    scrollbar = ttk.Scrollbar(instruments, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)

    def _on_inner_configure(_event):
        canvas.configure(scrollregion=canvas.bbox("all"))
    inner.bind("<Configure>", _on_inner_configure)
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=4)
    scrollbar.pack(side="right", fill="y", padx=(0, 6), pady=4)

    program_index_by_name = {name: i for i, name in enumerate(_GM_PROGRAMS)}
    drum_kit_label_by_idx = {idx: f"{name} ({idx})" for idx, name in _GM_DRUM_KITS}
    drum_kit_choices = [drum_kit_label_by_idx[idx] for idx, _ in _GM_DRUM_KITS]
    drum_idx_by_label = {label: idx for idx, label in drum_kit_label_by_idx.items()}

    for ch in range(1, 17):
        row = ttk.Frame(inner)
        row.pack(fill="x", padx=2, pady=1)
        role = _CHANNEL_LABELS.get(ch, "")
        label_text = f"ch {ch:>2}" + (f" — {role}" if role else "")
        ttk.Label(row, text=label_text, width=18, anchor="w").pack(side="left")

        # Per-channel mute + solo checkboxes — only meaningful when a
        # Player is wired in (otherwise there's nothing to gate against).
        # Solo is additive (DAW convention): solo'ing more than one
        # channel makes all of them audible together; everything else
        # gets muted. Clicking a lit Solo unlights it.
        if player is not None:
            mute_var = tk.IntVar(value=1 if ch in player._user_mutes else 0)

            def _on_mute(channel=ch, var=mute_var):
                if var.get():
                    player.mute(channel)
                else:
                    player.unmute(channel)
            ttk.Checkbutton(
                row, text="mute", variable=mute_var, command=_on_mute,
            ).pack(side="left", padx=(0, 4))

            solo_var = tk.IntVar(value=1 if ch in player._solo else 0)

            def _on_solo(channel=ch, var=solo_var):
                if var.get():
                    player.solo(channel)
                else:
                    player.unsolo_channel(channel)
            ttk.Checkbutton(
                row, text="solo", variable=solo_var, command=_on_solo,
            ).pack(side="left", padx=(0, 6))

        if ch == 10:
            # Drum bank picker (bank 128 preset).
            initial_kit = initial_programs.get(10, 0)
            current_label = drum_kit_label_by_idx.get(initial_kit, drum_kit_choices[0])
            cb = ttk.Combobox(
                row, values=drum_kit_choices, state="readonly",
                width=24,
            )
            cb.set(current_label)

            def _drum_select(_event, combo=cb):
                idx = drum_idx_by_label.get(combo.get(), 0)
                # select <chan-0idx> <sfont_id> <bank> <preset>
                # sfont_id 1 is the first/only SF FluidSynth loaded.
                send(f"select 9 1 128 {idx}")
            cb.bind("<<ComboboxSelected>>", _drum_select)
            cb.pack(side="left", fill="x", expand=True)
        else:
            initial_prog = initial_programs.get(ch, 0)
            initial_prog = max(0, min(127, initial_prog))
            display_choices = [f"{i:>3}  {name}" for i, name in enumerate(_GM_PROGRAMS)]
            cb = ttk.Combobox(
                row, values=display_choices, state="readonly",
                width=24,
            )
            cb.set(display_choices[initial_prog])

            def _prog_select(_event, combo=cb, chan_zero=ch - 1):
                label = combo.get()
                # Label is "  N  Name" — split on the first two spaces.
                try:
                    idx = int(label.strip().split()[0])
                except (ValueError, IndexError):
                    idx = program_index_by_name.get(label, 0)
                send(f"prog {chan_zero} {idx}")
            cb.bind("<<ComboboxSelected>>", _prog_select)
            cb.pack(side="left", fill="x", expand=True)

    if on_close is not None:
        root.protocol("WM_DELETE_WINDOW", lambda: (on_close(), root.destroy()))

    root.mainloop()
