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

from typing import IO, Callable, Optional


# FluidSynth shell-command templates for the drums strip on the Mixer
# tab. Each entry is (display label, command template, low, high,
# default). What used to be the standalone "Effects" tab — these now
# live inline on the drums (ch 10) strip because in --surge mode
# FluidSynth only ever renders drums; without --surge they're
# effectively global but still belong to the only FluidSynth-backed
# strip we have.
_FLUIDSYNTH_DRUM_SLIDERS: list[tuple[str, str, float, float, float]] = [
    ("Reverb room",   "set synth.reverb.room-size {v:.2f}",    0.0,   1.0,  0.4),
    ("Reverb damp",   "set synth.reverb.damp {v:.2f}",         0.0,   1.0,  0.3),
    ("Reverb level",  "set synth.reverb.level {v:.2f}",        0.0,   1.0,  0.7),
    ("Reverb width",  "set synth.reverb.width {v:.0f}",        0.0, 100.0, 80.0),
    ("Chorus depth",  "set synth.chorus.depth {v:.1f}",        0.0,  50.0,  8.0),
    ("Chorus level",  "set synth.chorus.level {v:.2f}",        0.0,  10.0,  2.0),
    ("Chorus speed",  "set synth.chorus.speed {v:.2f}",        0.29,  5.0,  0.3),
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
    5:  "voice (TTS)",
    6:  "sub-bass",
    10: "drums",
    11: "fx (sampler)",
}


# Sound tab per-instance knob layout. Each entry is
# (label, KNOB_ADDRS key, kind). 'kind' is 'slider' (0..1 float) or
# 'dropdown' (enum index); dropdowns get their option list from a
# separate map (_SURGE_ENUM_OPTIONS).
_SOUND_KNOBS: list[tuple[str, str, str]] = [
    ("Filter cutoff",     "filter_cutoff",    "slider"),
    ("Filter resonance",  "filter_resonance", "slider"),
    ("Filter type",       "filter_type",      "dropdown"),
    ("Osc 1 type",        "osc1_type",        "dropdown"),
    ("AEG attack",        "aeg_attack",       "slider"),
    ("AEG decay",         "aeg_decay",        "slider"),
    ("AEG sustain",       "aeg_sustain",      "slider"),
    ("AEG release",       "aeg_release",      "slider"),
    ("Scene volume",      "scene_volume",     "slider"),
]

# Surge XT enum value → label. Indices match Surge XT's parameter
# enum order (verified by querying /doc and /q during build-out).
_SURGE_ENUM_OPTIONS: dict[str, list[str]] = {
    # /param/a/filter/1/type — Surge XT 1.3 filter algorithm list.
    "filter_type": [
        "Off", "Legacy LP", "Legacy HP", "Legacy BP", "Legacy Notch",
        "OB-Xd 12dB", "OB-Xd 24dB", "K35 LP", "K35 HP", "Diode Ladder",
        "Cutoff Warp LP", "Cutoff Warp HP", "Cutoff Warp BP",
        "Cutoff Warp Notch", "Resonance Warp LP", "Resonance Warp HP",
        "Resonance Warp BP", "Resonance Warp Notch", "Tri-pole",
        "Comb +", "Comb -", "Sample & Hold",
    ],
    # /param/a/osc/1/type — Surge XT oscillator algorithms.
    "osc1_type": [
        "Classic", "Modern", "Wavetable", "Window", "Sine",
        "FM2", "FM3", "String", "Twist", "Alias", "S&H Noise",
        "Audio Input",
    ],
}


def _build_sound_tab(parent, surge_instances, ttk, tk, *, sampler=None, _var=None, player=None) -> None:
    """Render the comprehensive per-voice sound-design surface into
    *parent*. In ``--surge`` mode this tab consolidates everything
    about how each voice sounds: patch picker + FX slots + engine
    knobs per Surge instance, plus FX slots + bank management per
    sampler port.

    Each :class:`SurgeInstance` becomes an inner notebook sub-tab
    with (top-to-bottom):

    * **Patch picker** — dropdown of role-appropriate Surge factory
      patches (Leads / Basses / Pads / Sequences). Selecting one
      fires :meth:`SurgeInstance.load_patch`.
    * **FX A1 + FX A2** — type-picker dropdowns + Power + dynamic
      params, identical to the surface the Mixer tab used to render.
    * **Engine knobs** — filter cutoff / resonance / type / osc /
      ADSR / scene volume sliders + dropdowns from the curated
      :data:`_SOUND_KNOBS` list.
    * **Open GUI editor…** button for deep patch editing in a
      separate Surge XT standalone window.

    If *sampler* is provided, two additional sub-tabs appear:
    🎙 Voice (TTS phrases on ch 5) and 🔊 FX (WAV samples on ch 11).
    Each carries the FX-chain surface (Distortion / Delay / Reverb
    / … slot pickers) above its bank-management UI.

    *_var* is the variable-pinning helper from :func:`run_tweak_gui`
    that keeps Tk Vars alive across Homebrew non-threaded Tcl. Falls
    back to constructing raw Tk Vars if not supplied (used by
    tests / callers that don't need the pinning workaround)."""
    from pathlib import Path as _Path

    from slackbeatz.surge_host import (
        KNOB_ADDRS, _SURGE_FACTORY,
        list_factory_patches, patch_category_for_role,
        resolve_factory_patch, spawn_surge_gui,
    )

    # Fallback no-op pin when callers don't supply _var. Same shape
    # as run_tweak_gui's helper: cls + args/kwargs in, instance out.
    if _var is None:
        def _var(cls, *args, **kwargs):  # noqa: E306 — local helper
            return cls(*args, **kwargs)

    ttk.Label(
        parent,
        text="🎚 Per-voice sound design — patch + FX chain + engine "
             "knobs for every Surge channel; FX chain + bank "
             "management for sampler voice / fx.",
        wraplength=580, justify="left", foreground="#444",
    ).pack(padx=10, pady=(10, 6), anchor="w")

    inner = ttk.Notebook(parent)
    inner.pack(fill="both", expand=True, padx=8, pady=6)

    # Sub-tab title text stays static. Toggling the title to add ● for
    # activity caused the notebook column to grow on every flash (Tk
    # doesn't auto-shrink). The Mixer tab carries the per-channel
    # activity indicators instead; this tab is the sound-design
    # surface, not the performance monitor.
    for inst in surge_instances:
        frame = ttk.Frame(inner)
        tab_title = f"{inst.config.role} (ch {inst.config.channel_1idx})"
        inner.add(frame, text=tab_title)

        # ----- Patch picker --------------------------------------
        # Role-filtered dropdown sourced from the Surge factory tree.
        # Falls back to a static label if the role has no category
        # map (e.g. a custom role added by the user) — they can still
        # use the legacy Open GUI editor button to load a patch by
        # hand.
        patch_header = ttk.Frame(frame)
        patch_header.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(patch_header, text="Patch", width=8, anchor="w").pack(side="left")

        category = patch_category_for_role(inst.config.role)
        patches = list_factory_patches(category) if category else []
        patch_display_choices = [d for d, _rel in patches]
        rel_by_display = {d: rel for d, rel in patches}

        patch_var = _var(tk.StringVar, value="")
        cur_rel = inst.current_patch_rel
        cur_display = _Path(cur_rel).stem if cur_rel else ""
        if cur_display in rel_by_display:
            patch_var.set(cur_display)
        elif patch_display_choices:
            patch_var.set(patch_display_choices[0])

        patch_cb = ttk.Combobox(
            patch_header, values=patch_display_choices,
            textvariable=patch_var, state="readonly", width=28,
        )
        patch_cb.pack(side="left", padx=(0, 8), fill="x", expand=True)

        def _on_patch_select(_event=None, var=patch_var, inst_=inst, rels=rel_by_display):
            display = var.get()
            rel = rels.get(display)
            if rel is None:
                return
            patch_path = resolve_factory_patch(rel)
            if patch_path is not None:
                inst_.load_patch(patch_path)

        patch_cb.bind("<<ComboboxSelected>>", _on_patch_select)

        def _make_open_gui(inst=inst):
            def _open():
                # One-shot Surge XT GUI window for deep editing of the
                # currently-loaded patch.
                rel = inst.current_patch_rel or inst.config.initial_patch
                patch_path = resolve_factory_patch(rel)
                spawn_surge_gui(initial_patch=patch_path)
            return _open

        ttk.Button(patch_header, text="Open GUI editor…", command=_make_open_gui()).pack(side="left")

        # ----- FX slots (A1 + A2) --------------------------------
        # Same surface the Mixer tab used to render. Lives here in
        # --surge mode so all "how this voice sounds" controls are
        # in one place — the Mixer tab keeps only volume.
        fx_block = ttk.LabelFrame(frame, text="FX chain")
        fx_block.pack(fill="x", padx=8, pady=(2, 4))
        _build_surge_fx_slots(fx_block, inst, _var, ttk, tk)

        # Knob grid — two columns. Sliders on the left, value readouts
        # on the right (from the OSC reply cache).
        grid = ttk.Frame(frame)
        grid.pack(fill="both", expand=True, padx=8, pady=8)
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(2, weight=0)

        for row, (label, key, kind) in enumerate(_SOUND_KNOBS):
            addr = KNOB_ADDRS[key]
            ttk.Label(grid, text=label).grid(row=row, column=0, sticky="w", pady=2)

            if kind == "slider":
                # Initial slider position from cached value (set by
                # inst.spawn()'s priming query). Falls back to 0.5.
                initial = inst.get_value(addr)
                var = tk.DoubleVar(value=initial if initial is not None else 0.5)
                readout_var = tk.StringVar(value=inst.get_display(addr) or "—")

                def _on_slider(_=None, inst=inst, addr=addr, var=var, ro=readout_var):
                    inst.set_param(addr, float(var.get()))
                    # Display string updates async via OSC reply; poll
                    # the cache after a beat. Tk timer is fine for this.
                    def _refresh():
                        disp = inst.get_display(addr)
                        if disp:
                            ro.set(disp)
                    parent.after(80, _refresh)

                ttk.Scale(
                    grid, from_=0.0, to=1.0, orient="horizontal",
                    variable=var, command=_on_slider,
                ).grid(row=row, column=1, sticky="ew", padx=(6, 6), pady=2)
                ttk.Label(
                    grid, textvariable=readout_var, width=14,
                    foreground="#345", font=("TkFixedFont", 10),
                ).grid(row=row, column=2, sticky="e", pady=2)
            else:
                # Dropdown — enum value index sent as float.
                options = _SURGE_ENUM_OPTIONS.get(key, [])
                if not options:
                    continue
                current = inst.get_value(addr)
                start_idx = int(current) if current is not None else 0
                start_idx = max(0, min(start_idx, len(options) - 1))
                combo_var = tk.StringVar(value=options[start_idx])
                combo = ttk.Combobox(
                    grid, values=options, textvariable=combo_var,
                    state="readonly", width=22,
                )

                def _on_combo(_=None, inst=inst, addr=addr, var=combo_var, options=options):
                    try:
                        idx = options.index(var.get())
                    except ValueError:
                        return
                    inst.set_param(addr, float(idx))

                combo.bind("<<ComboboxSelected>>", _on_combo)
                combo.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=2)

    # Sampler-backed sub-tabs (🎙 Voice + 🔊 FX). Only render if a live
    # sampler was passed in — without one (e.g. when --surge wasn't
    # specified, or the sampler couldn't start) we'd just be drawing
    # widgets that can't take effect.
    if sampler is not None:
        from slackbeatz.synthhost import OSC_CHANNELS
        voice_port = OSC_CHANNELS["voice"][1]
        voice_ch = OSC_CHANNELS["voice"][0]
        fx_port = OSC_CHANNELS["fx"][1]
        fx_ch = OSC_CHANNELS["fx"][0]
        voice_frame = ttk.Frame(inner)
        inner.add(voice_frame, text=f"🎙 Voice (ch {voice_ch})")
        _build_voice_subtab(voice_frame, sampler, voice_port, ttk, tk, _var=_var)
        fx_frame = ttk.Frame(inner)
        inner.add(fx_frame, text=f"🔊 FX (ch {fx_ch})")
        _build_fx_subtab(fx_frame, sampler, fx_port, ttk, tk, _var=_var)


# --------------------------------------------------------------------------
# Sampler sub-tabs (issue #29)
# --------------------------------------------------------------------------

def _build_voice_subtab(parent, sampler, port_name: str, ttk, tk, *, _var=None) -> None:
    """🎙 Voice — manage TTS phrases on the voice channel.

    Top half: a Treeview listing ``midi_note → wav_path`` entries from
    the current bank, with ▶ (audition) / ✕ (remove) buttons.

    Middle: a "synthesize new phrase" form. Text entry + voice
    dropdown + note picker → calls :func:`tts.synthesize` and
    :meth:`Sampler.set_sample`.

    Bottom: the pedalboard FX-chain surface (two slot rows with
    type-picker dropdown + Power + dynamic params) — moved here from
    the Mixer tab so all sound-design controls live in one place per
    voice. The Mixer tab keeps only the volume slider."""
    from pathlib import Path as _Path

    ttk.Label(
        parent,
        text="Synthesise spoken phrases (Piper TTS). The resulting "
             "WAV is registered with the sampler at the chosen MIDI "
             "note — anything routed to channel 5 triggers it.",
        wraplength=560, justify="left", foreground="#444",
    ).pack(padx=8, pady=(8, 4), anchor="w")

    list_frame = ttk.Frame(parent)
    list_frame.pack(fill="both", expand=True, padx=8, pady=4)

    tree = ttk.Treeview(
        list_frame, columns=("note", "label", "path"),
        show="headings", height=8,
    )
    tree.heading("note", text="Note")
    tree.heading("label", text="Phrase / file")
    tree.heading("path", text="Path")
    tree.column("note", width=80, anchor="w")
    tree.column("label", width=180, anchor="w")
    tree.column("path", width=300, anchor="w")
    tree.pack(side="left", fill="both", expand=True)
    sb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")

    def _refresh_tree() -> None:
        for iid in tree.get_children():
            tree.delete(iid)
        for note, path in sorted(sampler.get_bank(port_name).items()):
            tree.insert("", "end", iid=str(note), values=(
                f"{note} ({_midi_note_name(note)})",
                _Path(path).stem,
                str(path),
            ))

    _refresh_tree()

    btn_row = ttk.Frame(parent)
    btn_row.pack(fill="x", padx=8, pady=(0, 6))

    def _audition_selected() -> None:
        sel = tree.selection()
        if not sel:
            return
        note = int(sel[0])
        path = sampler.get_bank(port_name).get(note)
        if path is not None:
            _audition_wav(path)

    def _remove_selected() -> None:
        sel = tree.selection()
        if not sel:
            return
        note = int(sel[0])
        sampler.remove_sample(port_name, note)
        _refresh_tree()

    ttk.Button(btn_row, text="▶ Audition", command=_audition_selected).pack(side="left", padx=2)
    ttk.Button(btn_row, text="✕ Remove", command=_remove_selected).pack(side="left", padx=2)

    # Synthesize-new form.
    ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=8, pady=6)
    form = ttk.Frame(parent)
    form.pack(fill="x", padx=8, pady=4)

    ttk.Label(form, text="Phrase:").grid(row=0, column=0, sticky="w", padx=2, pady=2)
    text_var = tk.StringVar(value="breathe in slowly")
    text_entry = ttk.Entry(form, textvariable=text_var, width=40)
    text_entry.grid(row=0, column=1, columnspan=3, sticky="ew", padx=2, pady=2)

    ttk.Label(form, text="Voice:").grid(row=1, column=0, sticky="w", padx=2, pady=2)
    voice_var = tk.StringVar(value="en_US-amy-low")
    try:
        from slackbeatz.tts import available_voices
        voices = available_voices() or ["en_US-amy-low"]
    except ImportError:
        voices = ["en_US-amy-low"]
    voice_combo = ttk.Combobox(
        form, textvariable=voice_var, values=voices, state="readonly", width=24,
    )
    voice_combo.grid(row=1, column=1, sticky="w", padx=2, pady=2)

    ttk.Label(form, text="Note:").grid(row=1, column=2, sticky="e", padx=2, pady=2)
    note_var = tk.IntVar(value=_next_free_note(sampler, port_name, 60))
    ttk.Spinbox(
        form, from_=0, to=127, textvariable=note_var, width=6,
    ).grid(row=1, column=3, sticky="w", padx=2, pady=2)

    status_var = tk.StringVar(value="")
    ttk.Label(form, textvariable=status_var, foreground="#345").grid(
        row=3, column=0, columnspan=4, sticky="w", padx=2, pady=(4, 0),
    )

    def _on_generate() -> None:
        text = text_var.get().strip()
        if not text:
            status_var.set("(empty phrase — type something)")
            return
        status_var.set(f"synthesising {text!r}…")
        parent.update_idletasks()
        try:
            from slackbeatz.tts import synthesize
            wav_path = synthesize(text, voice=voice_var.get())
        except Exception as e:  # noqa: BLE001 — surface to user
            status_var.set(f"failed: {e}")
            return
        sampler.set_sample(port_name, int(note_var.get()), wav_path)
        _refresh_tree()
        # Advance to the next free note so repeated clicks add new phrases.
        note_var.set(_next_free_note(sampler, port_name, int(note_var.get()) + 1))
        status_var.set(f"added → {wav_path.name}")

    ttk.Button(form, text="Generate", command=_on_generate).grid(
        row=2, column=0, columnspan=4, sticky="w", padx=2, pady=4,
    )
    form.columnconfigure(1, weight=1)

    # ----- Download voice ------------------------------------------
    # The Voice combobox only lists already-downloaded Piper voices.
    # Type a Piper voice name (e.g. en_US-hfc_female-medium) and hit
    # Download to pull the model + config from HuggingFace; on success
    # the combobox repopulates and the new voice is selectable.
    dl_row = ttk.Frame(parent)
    dl_row.pack(fill="x", padx=8, pady=(0, 6))
    ttk.Label(dl_row, text="Add voice:").pack(side="left")
    new_voice_var = tk.StringVar(value="en_US-hfc_female-medium")
    ttk.Entry(dl_row, textvariable=new_voice_var, width=28).pack(
        side="left", padx=(4, 4),
    )
    dl_status_var = tk.StringVar(value="")
    ttk.Label(dl_row, textvariable=dl_status_var, foreground="#345").pack(
        side="right",
    )

    def _on_download() -> None:
        import threading
        name = new_voice_var.get().strip()
        if not name:
            dl_status_var.set("(type a voice name)")
            return
        dl_status_var.set(f"downloading {name}…")

        def _worker():
            try:
                from slackbeatz.tts import available_voices, download_voice
                download_voice(name)
                vlist = available_voices()
                parent.after(0, lambda: (
                    voice_combo.configure(values=vlist),
                    voice_var.set(name) if name in vlist else None,
                    dl_status_var.set(f"added {name}"),
                ))
            except Exception as e:  # noqa: BLE001 — surface to user
                parent.after(0, lambda exc=e: dl_status_var.set(f"failed: {exc}"))

        threading.Thread(target=_worker, daemon=True).start()

    ttk.Button(dl_row, text="Download", command=_on_download).pack(side="left")

    # ----- FX chain (Pedalboard) -----------------------------------
    # Slot pickers + Power + dynamic params for the voice port's
    # pedalboard chain. Mirror of the Surge sub-tab's FX block —
    # moved here from the Mixer so all sound-design controls for the
    # voice live in one place.
    if _var is not None and sampler.get_slot_state(port_name, 0) is not None:
        fx_block = ttk.LabelFrame(parent, text="FX chain")
        fx_block.pack(fill="x", padx=8, pady=(6, 4))
        _build_sampler_fx_slots(fx_block, sampler, port_name, _var, ttk, tk)


def _build_fx_subtab(parent, sampler, port_name: str, ttk, tk, *, _var=None) -> None:
    """🔊 FX — manage arbitrary WAVs on the fx channel.

    Tree listing + "+ Add WAV" file picker. Drag-and-drop support
    requires the optional ``tkdnd`` pip dep; without it, the file
    picker covers the same ground.

    Bottom: pedalboard FX-chain slot pickers (Distortion / Delay /
    Reverb / etc.) — moved here from the Mixer tab so all
    sound-design controls for the fx port live in one place."""
    from pathlib import Path as _Path
    from tkinter import filedialog

    ttk.Label(
        parent,
        text="Map .wav files to MIDI notes on channel 11. Anything "
             "the song sends to ch 11 plays the matching sample.",
        wraplength=560, justify="left", foreground="#444",
    ).pack(padx=8, pady=(8, 4), anchor="w")

    list_frame = ttk.Frame(parent)
    list_frame.pack(fill="both", expand=True, padx=8, pady=4)

    tree = ttk.Treeview(
        list_frame, columns=("note", "path"),
        show="headings", height=10,
    )
    tree.heading("note", text="Note")
    tree.heading("path", text="WAV path")
    tree.column("note", width=80, anchor="w")
    tree.column("path", width=400, anchor="w")
    tree.pack(side="left", fill="both", expand=True)
    sb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")

    def _refresh_tree() -> None:
        for iid in tree.get_children():
            tree.delete(iid)
        for note, path in sorted(sampler.get_bank(port_name).items()):
            tree.insert("", "end", iid=str(note), values=(
                f"{note} ({_midi_note_name(note)})",
                str(path),
            ))

    _refresh_tree()

    btn_row = ttk.Frame(parent)
    btn_row.pack(fill="x", padx=8, pady=(0, 6))

    note_var = tk.IntVar(value=_next_free_note(sampler, port_name, 36))
    ttk.Label(btn_row, text="Note for next add:").pack(side="left", padx=(0, 4))
    ttk.Spinbox(
        btn_row, from_=0, to=127, textvariable=note_var, width=6,
    ).pack(side="left", padx=2)

    def _on_add() -> None:
        paths = filedialog.askopenfilenames(
            title="Add WAV samples",
            filetypes=[("WAV audio", "*.wav"), ("All files", "*.*")],
        )
        if not paths:
            return
        note = int(note_var.get())
        for p in paths:
            sampler.set_sample(port_name, note, _Path(p))
            note = _next_free_note(sampler, port_name, note + 1)
        note_var.set(note)
        _refresh_tree()

    def _audition_selected() -> None:
        sel = tree.selection()
        if not sel:
            return
        note = int(sel[0])
        path = sampler.get_bank(port_name).get(note)
        if path is not None:
            _audition_wav(path)

    def _remove_selected() -> None:
        sel = tree.selection()
        if not sel:
            return
        note = int(sel[0])
        sampler.remove_sample(port_name, note)
        _refresh_tree()

    ttk.Button(btn_row, text="+ Add WAV…", command=_on_add).pack(side="left", padx=4)
    ttk.Button(btn_row, text="▶ Audition", command=_audition_selected).pack(side="left", padx=2)
    ttk.Button(btn_row, text="✕ Remove", command=_remove_selected).pack(side="left", padx=2)

    # ----- FX chain (Pedalboard) -----------------------------------
    if _var is not None and sampler.get_slot_state(port_name, 0) is not None:
        fx_block = ttk.LabelFrame(parent, text="FX chain")
        fx_block.pack(fill="x", padx=8, pady=(6, 4))
        _build_sampler_fx_slots(fx_block, sampler, port_name, _var, ttk, tk)


def _next_free_note(sampler, port_name: str, start: int) -> int:
    """Return the lowest unmapped MIDI note in [start, 128). Falls
    back to ``start`` if every note above is taken."""
    bank = sampler.get_bank(port_name)
    for note in range(max(0, start), 128):
        if note not in bank:
            return note
    return start


def _audition_wav(path) -> None:
    """Play *path* through the system default audio player. Used by
    the ▶ buttons in the sampler sub-tabs. Cheap + portable;
    ``afplay`` on macOS, ``aplay`` on Linux."""
    import shutil
    import subprocess
    import sys
    binary = None
    if sys.platform == "darwin":
        binary = "afplay"
    elif sys.platform.startswith("linux"):
        binary = "aplay"
    if binary is None or shutil.which(binary) is None:
        print(f"slackbeatz gui: no audition player available for {path}",
              file=sys.stderr)
        return
    subprocess.Popen(
        [binary, str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


_MIDI_NOTE_NAMES = (
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
)


def _midi_note_name(note: int) -> str:
    """Return e.g. ``C4`` for MIDI note 60."""
    pitch = _MIDI_NOTE_NAMES[note % 12]
    octave = note // 12 - 1  # MIDI 60 = C4
    return f"{pitch}{octave}"


# --------------------------------------------------------------------------
# 🎛 Mixer tab
# --------------------------------------------------------------------------

# Per-strip metadata used by _build_mixer_tab. Order = mixer row order
# (top to bottom). Each entry is (channel_1idx, label, emoji, kind);
# kind picks the FX surface — "surge" / "sampler-voice" /
# "sampler-fx" / "fluidsynth-drums".
_MIXER_STRIPS: tuple[tuple[int, str, str, str], ...] = (
    (1,  "lead",  "🎵",  "surge"),
    (2,  "bass",  "🎸",  "surge"),
    (3,  "pad",   "🌊",  "surge"),
    (4,  "candy", "🍬",  "surge"),
    (5,  "voice", "🎙",  "sampler-voice"),
    (6,  "sub",   "🎵",  "surge"),
    (10, "drums", "🥁",  "fluidsynth-drums"),
    (11, "fx",    "🔊",  "sampler-fx"),
)

# Master-volume OSC address on each Surge instance. Using
# /param/global/volume (the master output of the whole instance) —
# NOT /param/a/amp/volume which is scene-A-only and doesn't move the
# audible level when scene B is contributing (and on some patches
# the audible level appears not to budge for /param/a/amp/volume at
# all — the Mixer's strip behaves as "channel fader" which is the
# global-output semantic anyway). The Sound tab's per-knob "Scene
# volume" still points at scene A explicitly via KNOB_ADDRS so users
# wanting scene-A vs scene-B balance still get the control.
_SURGE_VOLUME_ADDR = "/param/global/volume"


def _build_mixer_tab(
    parent,
    *,
    surge_instances,
    sampler,
    send,
    initial_gain,
    initial_reverb_room,
    _var,
    ttk,
    tk,
    player=None,
) -> None:
    """Render the 🎛 Mixer tab into *parent*.

    Layout: one strip per channel from :data:`_MIXER_STRIPS` that
    actually has a backend live (Surge instance running / sampler
    subscribed to the port / FluidSynth available via *send*), then a
    Master strip at the bottom whose slider scales every other strip
    proportionally.

    Per-strip controls:

    * **Surge channels** — Volume drives ``/param/global/volume``
      (master output of the Surge instance). FX + patch picker live
      on the 🎚 Sound tab instead.
    * **Sampler channels** — Volume drives ``Sampler.set_port_gain``;
      Phase 3 will add pedalboard FX.
    * **FluidSynth drums** — Volume drives the ``gain`` shell
      command; Reverb/Chorus sliders + toggles drive
      ``set synth.reverb.* / chorus.*`` (the old Effects-tab surface,
      relocated).
    """
    from slackbeatz.synthhost import OSC_CHANNELS

    # Map channel_1idx → live backend handle so we know which strips
    # actually have something to control on this run.
    surge_by_channel: dict[int, object] = {
        getattr(inst, "config").channel_1idx: inst
        for inst in surge_instances
    }
    sampler_port_for_role: dict[str, str | None] = {
        "voice": OSC_CHANNELS["voice"][1] if sampler is not None else None,
        "fx":    OSC_CHANNELS["fx"][1] if sampler is not None else None,
    }

    # Header.
    ttk.Label(
        parent,
        text="🎛 Per-channel volume + FX. Sliders apply live — no need to "
             "restart playback. Drums FX inherit from the old Effects tab.",
        wraplength=620, justify="left", foreground="#444",
    ).pack(padx=10, pady=(8, 4), anchor="w")

    body = ttk.Frame(parent)
    body.pack(fill="both", expand=True, padx=10, pady=4)

    # Per-strip volume state. Each strip's slider stores 0..1.5 where
    # 1.0 = unity for the channel's native unit (Surge scene volume
    # nominally 0..1, sampler gain 0..N, FluidSynth gain ~0..2). We
    # multiply by the master scalar before sending to the backend.
    strip_vol_vars: dict[int, "tk.DoubleVar"] = {}
    master_var = _var(tk.DoubleVar, value=1.0)

    def _apply_strip(channel_1idx: int) -> None:
        """Recompute backend value for one strip + send it. Called on
        per-strip slider drag AND on master-slider drag (which loops
        over every strip)."""
        per_strip = float(strip_vol_vars[channel_1idx].get())
        master = float(master_var.get())
        effective = per_strip * master

        # Surge → /param/global/volume. Surge's param range is 0..1
        # normalised so we clamp here. The slider's own 0..1 range
        # already matches; the clamp is defensive against Master ×
        # per_strip products that overshoot.
        surge = surge_by_channel.get(channel_1idx)
        if surge is not None:
            surge.set_param(_SURGE_VOLUME_ADDR, max(0.0, min(1.0, effective)))
            return
        # Sampler → set_port_gain.
        for role, port in sampler_port_for_role.items():
            if port is None:
                continue
            if OSC_CHANNELS[role][0] == channel_1idx:
                sampler.set_port_gain(port, effective)
                return
        # FluidSynth drums → gain shell command. The historical
        # default is 0.6; we treat the user's slider as a 0..2 range
        # to match the old Effects-tab "Master gain" surface. `send`
        # is None in bare-MIDI mode (no FluidSynth running), but the
        # drums strip is also hidden there — this guard is purely
        # defensive in case a stale closure fires post-cleanup.
        if channel_1idx == 10 and send is not None:
            send(f"gain {effective:.2f}")

    # Activity indicators — channel → Label widget. The strip's
    # LabelFrame title stays static; the activity is shown on an
    # inline Label widget at the right of the volume row with
    # width=2 (so the slot never resizes). Toggling the title text
    # instead caused Tk's geometry manager to grow the window every
    # time a note fired — no shrink path, so it ratcheted wider.
    strip_activity: dict[int, "ttk.Label"] = {}

    # Build one strip per known channel (skip strips whose backend isn't
    # present this run — e.g. without --surge there are no Surge handles
    # so those strips don't render; in bare-MIDI mode FluidSynth isn't
    # running so the drums strip's reverb / chorus / gain knobs would
    # silently no-op).
    has_fluidsynth = send is not None
    for ch_1idx, role, emoji, kind in _MIXER_STRIPS:
        backend_present = (
            (kind == "surge" and ch_1idx in surge_by_channel)
            or (kind == "sampler-voice" and sampler_port_for_role["voice"] is not None)
            or (kind == "sampler-fx" and sampler_port_for_role["fx"] is not None)
            or (kind == "fluidsynth-drums" and has_fluidsynth)
        )
        if not backend_present:
            continue

        strip_title = f"{emoji}  {role} (ch {ch_1idx})"
        strip = ttk.LabelFrame(body, text=strip_title)
        strip.pack(fill="x", padx=4, pady=4)

        # Volume row.
        vol_row = ttk.Frame(strip)
        vol_row.pack(fill="x", padx=8, pady=(4, 2))
        ttk.Label(vol_row, text="Vol", width=8, anchor="w").pack(side="left")

        # Activity indicator — fixed-width Label that toggles its
        # text colour when this channel fires a note_on within the
        # last ~150 ms. width=2 keeps the slot dimensionally constant
        # so flashing doesn't trigger Tk to re-layout the parent (the
        # window otherwise grows rightward on every flash since Tk
        # doesn't auto-shrink).
        activity_label = ttk.Label(
            vol_row, text="●", width=2, anchor="center",
            foreground="#ccc",
        )
        activity_label.pack(side="right", padx=(4, 0))
        strip_activity[ch_1idx] = activity_label

        # Per-strip-kind range + initial position:
        #   surge            — 0..1 (matches /param/global/volume). Initial
        #                      from cached value or 0.8 default.
        #   sampler-voice/fx — 0..1.5 (Sampler.set_port_gain is a linear
        #                      multiplier; values > 1.0 boost above
        #                      unity).
        #   fluidsynth-drums — 0..2 (FluidSynth `gain` shell command;
        #                      historically 0.6 default, 2.0 max).
        if kind == "surge":
            surge = surge_by_channel[ch_1idx]
            cur = surge.get_value(_SURGE_VOLUME_ADDR)
            # /param/global/volume hasn't always replied by the time
            # the GUI builds — Surge's `/q` round-trip is ~50 ms.
            # 0.8 is a reasonable "loud-but-not-clipping" default for
            # the channel's initial slider position.
            initial = float(cur) if cur is not None else 0.8
            slider_max = 1.0
        elif kind == "fluidsynth-drums":
            initial = float(initial_gain) if initial_gain is not None else 0.6
            slider_max = 2.0
        else:  # sampler-voice / sampler-fx
            initial = 1.0
            slider_max = 1.5
        var = _var(tk.DoubleVar, value=initial)
        strip_vol_vars[ch_1idx] = var
        scale = tk.Scale(
            vol_row, from_=0.0, to=slider_max,
            resolution=0.01,
            orient="horizontal", variable=var,
            showvalue=True, length=320,
            command=lambda _v, c=ch_1idx: _apply_strip(c),
        )
        scale.pack(side="left", fill="x", expand=True)

        # FX surface — per-strip-kind. Surge + Sampler FX now live on
        # the 🎚 Sound tab (under the matching voice's sub-tab) so all
        # sound-design controls per voice are in one place. The drums
        # strip keeps its FluidSynth-global reverb/chorus inline
        # because FluidSynth has no Sound sub-tab.
        if kind == "fluidsynth-drums":
            _build_fluidsynth_fx(strip, send, _var, ttk, tk, initial_reverb_room)
        elif kind == "surge":
            ttk.Label(
                strip,
                text="(patch + FX on 🎚 Sound tab)",
                foreground="#888",
                font=("TkDefaultFont", 9, "italic"),
            ).pack(anchor="w", padx=8, pady=(0, 6))
        elif kind in ("sampler-voice", "sampler-fx"):
            role = "voice" if kind == "sampler-voice" else "fx"
            port = sampler_port_for_role[role]
            if port is None or sampler.get_slot_state(port, 0) is None:
                ttk.Label(
                    strip,
                    text="(pedalboard not installed — `pip install "
                         "'slackbeatz[tts]'` to enable per-slot FX. "
                         "Quote the brackets in zsh.)",
                    foreground="#888",
                    font=("TkDefaultFont", 9, "italic"),
                ).pack(anchor="w", padx=8, pady=(0, 6))
            else:
                ttk.Label(
                    strip,
                    text="(FX on 🎚 Sound tab)",
                    foreground="#888",
                    font=("TkDefaultFont", 9, "italic"),
                ).pack(anchor="w", padx=8, pady=(0, 6))

    # Master strip — slim row at the bottom that scales every per-strip
    # slider's effective value.
    master_strip = ttk.LabelFrame(body, text="🎚  Master")
    master_strip.pack(fill="x", padx=4, pady=(12, 4))
    m_row = ttk.Frame(master_strip)
    m_row.pack(fill="x", padx=8, pady=4)
    ttk.Label(m_row, text="Vol", width=8, anchor="w").pack(side="left")
    m_scale = tk.Scale(
        m_row, from_=0.0, to=1.5, resolution=0.01,
        orient="horizontal", variable=master_var,
        showvalue=True, length=320,
        command=lambda _v: [
            _apply_strip(c) for c in strip_vol_vars
        ],
    )
    m_scale.pack(side="left", fill="x", expand=True)

    # Activity flash — self-arming 80 ms poll loop. The dot widget
    # stays the same width either way; only its colour changes
    # between "#ccc" (idle, dim grey) and "#2c2" (active, bright
    # green). No geometry-manager pumping, no window-grow side
    # effects.
    #
    # Player.is_channel_active reads the dict the _ActivityTapSink
    # writes from the audio thread — lock-free, dict.get is atomic
    # in CPython. Worst-case stale read is one poll cycle (~80 ms),
    # imperceptible to the eye. We compare to the last-known state
    # so configure() only fires on transitions, keeping the Tk
    # event queue quiet when nothing is changing.
    if player is not None:
        active_now: dict[int, bool] = {ch: False for ch in strip_activity}

        def _poll_activity() -> None:
            for ch, label in strip_activity.items():
                is_active = player.is_channel_active(ch)
                if is_active == active_now[ch]:
                    continue
                active_now[ch] = is_active
                try:
                    label.configure(foreground="#2c2" if is_active else "#ccc")
                except Exception:
                    pass
            parent.after(80, _poll_activity)

        parent.after(80, _poll_activity)


def _build_surge_fx_slots(parent, surge_instance, _var, ttk, tk) -> None:
    """Render two FX-slot rows (FX-A1 + FX-A2) for one Surge strip.

    Each row has a type-picker dropdown + Power toggle + up to N param
    sliders. Changing the dropdown re-renders the param sliders (they
    differ per FX type) and sends the new ``/param/fx/a/<slot>/type``
    OSC write so Surge swaps in the new effect."""
    from slackbeatz.surge_host import (
        FX_CATALOG, FX_DOC_CACHE, _FX_DOC_DISCOVERY_DONE, fx_addr,
    )

    # Display-name → type-id, sorted to match a stable dropdown order
    # (Off first; rest by type-id so families stay clustered).
    dropdown_items = sorted(FX_CATALOG.items(), key=lambda kv: (kv[0] != 0, kv[0]))
    dropdown_labels = [spec.name for _tid, spec in dropdown_items]
    label_to_typeid = {spec.name: tid for tid, spec in dropdown_items}

    for slot in (1, 2):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=(0, 4))

        # Slot label + dropdown + power toggle on one line.
        header = ttk.Frame(row)
        header.pack(fill="x")
        ttk.Label(header, text=f"FX-A{slot}", width=8, anchor="w").pack(side="left")

        # Read the live type-id from Surge's cached values. Falls back
        # to the catalog's first entry if Surge hasn't replied yet.
        cur_type = surge_instance.get_value(fx_addr(slot, "type"))
        cur_type_id = int(cur_type) if cur_type is not None else dropdown_items[0][0]
        cur_label = FX_CATALOG.get(cur_type_id, FX_CATALOG[0]).name
        type_var = _var(tk.StringVar, value=cur_label)
        cb = ttk.Combobox(
            header, values=dropdown_labels, textvariable=type_var,
            state="readonly", width=12,
        )
        cb.pack(side="left", padx=(0, 8))

        # Power toggle — OSC ``deactivate`` is inverse: 1 = off,
        # 0 = on. Tk IntVar holds the *power* state (1 = on) so the
        # checkbox label reads naturally.
        power_var = _var(tk.IntVar, value=0)  # default Off (matches spawn-time setup)
        ttk.Checkbutton(
            header, text="Power", variable=power_var,
            command=lambda s=slot, v=power_var: surge_instance.set_param(
                fx_addr(s, "deactivate"), 0.0 if v.get() else 1.0,
            ),
        ).pack(side="left")

        # Params row — gets rebuilt every time the dropdown changes.
        params_frame = ttk.Frame(row)
        params_frame.pack(fill="x", padx=(0, 4), pady=(2, 0))

        def _label_for_param(
            slot_: int, p_idx: int, fallback: str,
            type_id: Optional[int] = None,
        ) -> tuple[Optional[str], str]:
            """Resolve a label for this FX slot's param. Returns
            ``(label, source)`` where:

            * ``label`` is the string to show, or ``None`` if the
              caller should hide this row entirely (advanced section
              only; essentials always render).
            * ``source`` is a debug tag — ``"live"``, ``"cache"``,
              ``"fallback"``, or ``"hide"``.

            Resolution order:

            1. Live ``/doc`` reply from this instance (most authoritative,
               but may not have arrived yet on first render after a
               type change).
            2. Process-wide :data:`FX_DOC_CACHE` populated during the
               one-shot discovery sweep at spawn time.
            3. ``fallback`` (the catalog essential label, or
               ``"param N"`` for advanced) — but ONLY while discovery
               is still in flight. Once discovery is done, a cache
               miss for the current FX type means Surge really has
               no name for this slot → hide it.
            """
            # 1. Live /doc reply.
            doc = surge_instance.get_param_doc(fx_addr(slot_, "param", p_idx))
            if doc is not None:
                name = doc[0].strip()
                if name and not name.lower().startswith("param "):
                    return name, "live"

            # 2. Process-wide pre-warmed cache. Prefer the caller's
            # known type_id (avoids racing the type-change /param echo
            # back through the value cache).
            cur_type: Optional[int]
            if type_id is not None:
                cur_type = type_id
            else:
                raw = surge_instance.get_value(fx_addr(slot_, "type"))
                cur_type = int(raw) if raw is not None else None
            if cur_type is not None:
                cached = FX_DOC_CACHE.get((cur_type, p_idx))
                if cached:
                    return cached, "cache"

            # 3. Fallback OR hide.
            #    - During discovery: render with fallback so the user
            #      gets *something*; subsequent re-polls will swap in
            #      real labels as they arrive.
            #    - After discovery: cache miss is authoritative
            #      "no name in this FX type" → hide.
            if not _FX_DOC_DISCOVERY_DONE.is_set():
                return fallback, "fallback"
            return None, "hide"

        def _make_param_slider(parent_widget, slot_: int, p_idx: int, label: str) -> None:
            p_row = ttk.Frame(parent_widget)
            p_row.pack(fill="x", pady=1)
            ttk.Label(p_row, text=label, width=10, anchor="w").pack(side="left")
            addr = fx_addr(slot_, "param", p_idx)
            cur = surge_instance.get_value(addr)
            p_var = _var(
                tk.DoubleVar,
                value=float(cur) if cur is not None else 0.5,
            )
            tk.Scale(
                p_row, from_=0.0, to=1.0, resolution=0.01,
                orient="horizontal", variable=p_var,
                showvalue=False, length=180,
                command=lambda _v, a=addr, v=p_var:
                    surge_instance.set_param(a, float(v.get())),
            ).pack(side="left", fill="x", expand=True)

        # Persisted across type changes so user's "Advanced expanded"
        # preference survives the rebuild. Closure cell so the
        # _rebuild_params callback can read + write it.
        advanced_expanded = [False]  # mutable wrapper

        def _rebuild_params(spec_type_id: int, frame=params_frame, slot_=slot) -> None:
            for w in frame.winfo_children():
                w.destroy()

            # Source of truth for "what params exist on this FX type"
            # is Surge itself — via the FX_DOC_CACHE the discovery
            # sweep filled at spawn (or the live /doc cache on this
            # instance for types we've touched since). The catalog's
            # role is purely the dropdown's display-label mapping;
            # the (label, p_idx) lists inside it can drift out of
            # sync with the running Surge XT build (param indices
            # and short names change between releases) and that
            # USED to leave the user with no sliders to grab.
            #
            # Algorithm:
            #   * collect every p_idx 1..12 that has a real name
            #     (live /doc OR FX_DOC_CACHE for this type),
            #   * split that list into "essentials" (first 3) +
            #     "advanced" (the rest) so the row stays compact,
            #   * if nothing has a name yet (discovery still in
            #     flight on the first repaint), fall back to the
            #     catalog so the user gets something to grab until
            #     real labels arrive.
            named_params: list[tuple[int, str]] = []
            for p_idx in range(1, 13):
                label, src = _label_for_param(
                    slot_, p_idx, fallback="",  # blank fallback — see below
                    type_id=spec_type_id,
                )
                if label and src in ("live", "cache"):
                    named_params.append((p_idx, label))

            using_catalog_fallback = False
            if not named_params:
                # Pre-discovery or post-discovery-with-no-data cases.
                # Use the catalog's essentials as a safety net so the
                # user can still grab the common knobs.
                spec = FX_CATALOG.get(spec_type_id)
                catalog_essentials = spec.params if spec is not None else ()
                named_params = [(p_idx, lbl) for lbl, p_idx in catalog_essentials]
                using_catalog_fallback = bool(named_params)

            essentials_rows = named_params[:3]
            advanced_rows: list[tuple[int, str]] = named_params[3:]

            # Essentials block.
            for p_idx, label in essentials_rows:
                _make_param_slider(frame, slot_, p_idx, label)
            if not essentials_rows:
                # Genuinely no params discovered for this FX type
                # (e.g. Off, Vocoder). Skip the empty-essentials
                # placeholder — the advanced section's "(no extra
                # params)" hint below conveys the same thing.
                pass

            adv_header = ttk.Frame(frame)
            adv_header.pack(fill="x", pady=(4, 0))
            adv_btn_var = _var(
                tk.StringVar,
                value=("▼ Advanced" if advanced_expanded[0] else "▶ Advanced"),
            )
            adv_frame = ttk.Frame(frame)

            def _toggle_advanced(btn_var=adv_btn_var, frm=adv_frame) -> None:
                advanced_expanded[0] = not advanced_expanded[0]
                if advanced_expanded[0]:
                    btn_var.set("▼ Advanced")
                    frm.pack(fill="x", pady=(2, 0))
                else:
                    btn_var.set("▶ Advanced")
                    frm.pack_forget()

            # Show the row count in the button so the user knows
            # how many params hide behind it without having to expand.
            if advanced_rows:
                adv_btn_var.set(
                    ("▼" if advanced_expanded[0] else "▶")
                    + f" Advanced ({len(advanced_rows)})"
                )
                ttk.Button(
                    adv_header, textvariable=adv_btn_var,
                    command=_toggle_advanced,
                    width=16,
                ).pack(side="left")
            elif essentials_rows:
                # Got essentials but nothing beyond — say so.
                ttk.Label(
                    adv_header, text="(no extra params for this FX)",
                    foreground="#888",
                    font=("TkDefaultFont", 9, "italic"),
                ).pack(side="left")
            else:
                # No essentials AND no advanced. Could mean: Off /
                # Vocoder (genuinely zero params), OR discovery
                # hasn't completed yet on a type we've never seen.
                # Either way, give the user a non-empty hint they
                # can act on.
                if not _FX_DOC_DISCOVERY_DONE.is_set():
                    msg = "(discovering params from Surge — wait a moment)"
                else:
                    msg = (
                        f"(Surge reports no params for FX type "
                        f"#{spec_type_id} — try another type, "
                        f"or use Open GUI editor…)"
                    )
                ttk.Label(
                    adv_header, text=msg,
                    foreground="#888",
                    font=("TkDefaultFont", 9, "italic"),
                ).pack(side="left")

            # If we're rendering from the catalog fallback (no real
            # labels yet), nudge the user with a one-time note.
            if using_catalog_fallback:
                ttk.Label(
                    frame, text="(loading real param names — sliders "
                                "will refresh in ~1 s)",
                    foreground="#888",
                    font=("TkDefaultFont", 9, "italic"),
                ).pack(anchor="w", pady=(2, 0))

            # Render the advanced sliders into the (possibly hidden)
            # frame ahead of time so toggling it on is instant.
            for p_idx, label in advanced_rows:
                _make_param_slider(adv_frame, slot_, p_idx, label)

            if advanced_expanded[0]:
                adv_frame.pack(fill="x", pady=(2, 0))

        _rebuild_params(cur_type_id)

        def _on_type_change(_event=None, var=type_var, slot_=slot) -> None:
            new_label = var.get()
            new_id = label_to_typeid.get(new_label)
            if new_id is None:
                return
            surge_instance.set_param(fx_addr(slot_, "type"), float(new_id))
            # Fire /doc queries for the new FX type's params. Replies
            # land asynchronously — we re-render now using whatever
            # cache we have (FX_DOC_CACHE prefills most labels at
            # spawn) and reschedule a few times so any slow /doc
            # replies still get a chance to paint.
            surge_instance.query_fx_slot_docs(slot_)
            _rebuild_params(new_id)
            for delay_ms in (150, 400, 1000):
                parent.after(delay_ms, lambda nid=new_id: _rebuild_params(nid))

        cb.bind("<<ComboboxSelected>>", _on_type_change)


def _build_sampler_fx_slots(parent, sampler, port_name, _var, ttk, tk) -> None:
    """Render two pedalboard FX-slot rows (FX-1 + FX-2) for one
    sampler strip — mirror of :func:`_build_surge_fx_slots` for the
    Surge strips.

    Each row has a type-picker dropdown (sourced from
    :data:`PEDALBOARD_FX_CATALOG`) + Power toggle + dynamic param
    sliders that re-render when the dropdown changes. Type swap
    rebuilds the underlying Pedalboard chain atomically via
    :meth:`Sampler.set_slot_fx`; Power toggles add/remove the slot's
    plugin from the live chain via :meth:`Sampler.set_slot_power`."""
    from slackbeatz.sampler import PEDALBOARD_FX_CATALOG

    catalog_names = list(PEDALBOARD_FX_CATALOG.keys())

    for slot_idx in (0, 1):
        slot_state = sampler.get_slot_state(port_name, slot_idx)
        if slot_state is None:
            continue

        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=(0, 4))

        header = ttk.Frame(row)
        header.pack(fill="x")
        ttk.Label(header, text=f"FX-{slot_idx + 1}", width=8, anchor="w").pack(side="left")

        type_var = _var(tk.StringVar, value=slot_state.type_key)
        cb = ttk.Combobox(
            header, values=catalog_names, textvariable=type_var,
            state="readonly", width=12,
        )
        cb.pack(side="left", padx=(0, 8))

        power_var = _var(tk.IntVar, value=1 if slot_state.powered else 0)
        ttk.Checkbutton(
            header, text="Power", variable=power_var,
            command=lambda s=slot_idx, v=power_var:
                sampler.set_slot_power(port_name, s, bool(v.get())),
        ).pack(side="left")

        # Params re-render on type change; pre-allocated frame so
        # _rebuild_params can wipe + repopulate cleanly.
        params_frame = ttk.Frame(row)
        params_frame.pack(fill="x", padx=(0, 4), pady=(2, 0))

        def _rebuild_params(slot_=slot_idx, frame=params_frame) -> None:
            for w in frame.winfo_children():
                w.destroy()
            state = sampler.get_slot_state(port_name, slot_)
            if state is None:
                return
            spec = PEDALBOARD_FX_CATALOG.get(state.type_key)
            if spec is None or not spec.params:
                ttk.Label(
                    frame, text="(no live params for this FX)",
                    foreground="#888",
                    font=("TkDefaultFont", 9, "italic"),
                ).pack(anchor="w")
                return
            for param in spec.params:
                p_row = ttk.Frame(frame)
                p_row.pack(fill="x", pady=1)
                ttk.Label(p_row, text=param.label, width=8, anchor="w").pack(side="left")
                current = float(getattr(state.plugin, param.attr, param.default))
                p_var = _var(tk.DoubleVar, value=current)
                # Choose a resolution scaled to the range so the
                # slider feels musical without being twitchy.
                rng = max(0.001, param.hi - param.lo)
                resolution = round(rng / 200, 6) if rng > 0 else 0.01
                tk.Scale(
                    p_row, from_=param.lo, to=param.hi,
                    resolution=resolution,
                    orient="horizontal", variable=p_var,
                    showvalue=False, length=200,
                    command=lambda _v, attr=param.attr, var=p_var, slot__=slot_:
                        _mutate_slot_plugin(slot__, attr, float(var.get())),
                ).pack(side="left", fill="x", expand=True)

        def _mutate_slot_plugin(slot_, attr, value) -> None:
            """Mutate the slot's live plugin in place. The plugin is
            held by reference inside the Pedalboard chain so the
            audio thread sees the change on the next callback."""
            state = sampler.get_slot_state(port_name, slot_)
            if state is None:
                return
            try:
                setattr(state.plugin, attr, value)
            except (AttributeError, ValueError):
                # Plugin doesn't accept that value — silently keep
                # the previous setting rather than crash the GUI.
                pass

        _rebuild_params()

        def _on_type_change(_event=None, var=type_var, slot_=slot_idx) -> None:
            new_key = var.get()
            if not sampler.set_slot_fx(port_name, slot_, new_key):
                return
            _rebuild_params(slot_=slot_)

        cb.bind("<<ComboboxSelected>>", _on_type_change)


def _build_fluidsynth_fx(parent, send, _var, ttk, tk, initial_reverb_room) -> None:
    """Render the FluidSynth reverb + chorus surface inside the drums
    strip. This is the entire pre-mixer Effects tab, relocated."""
    fx_row = ttk.Frame(parent)
    fx_row.pack(fill="x", padx=8, pady=(0, 6))

    # Power toggles up top.
    toggles = ttk.Frame(fx_row)
    toggles.pack(fill="x", pady=(0, 2))
    rev_var = _var(tk.IntVar, value=1)
    cho_var = _var(tk.IntVar, value=1)
    ttk.Checkbutton(
        toggles, text="Reverb on", variable=rev_var,
        command=lambda: send(f"set synth.reverb.active {rev_var.get()}"),
    ).pack(side="left", padx=4)
    ttk.Checkbutton(
        toggles, text="Chorus on", variable=cho_var,
        command=lambda: send(f"set synth.chorus.active {cho_var.get()}"),
    ).pack(side="left", padx=4)

    # Sliders grid — two columns to keep the strip compact.
    grid = ttk.Frame(fx_row)
    grid.pack(fill="x")
    grid.columnconfigure(1, weight=1)
    grid.columnconfigure(3, weight=1)

    overrides: dict[str, float] = {}
    if initial_reverb_room is not None:
        overrides["Reverb room"] = float(initial_reverb_room)

    for i, (label, cmd_tmpl, low, high, default) in enumerate(
        _FLUIDSYNTH_DRUM_SLIDERS,
    ):
        value = overrides.get(label, default)
        col_pair = (i % 2) * 2  # 0 or 2
        row_idx = i // 2
        ttk.Label(grid, text=label, anchor="w").grid(
            row=row_idx, column=col_pair, sticky="w", padx=(0, 4), pady=1,
        )
        var = _var(tk.DoubleVar, value=value)
        resolution = (high - low) / 200 if (high - low) > 0 else 0.01
        scale = tk.Scale(
            grid, from_=low, to=high,
            resolution=resolution,
            orient="horizontal", variable=var,
            showvalue=False, length=140,
            command=lambda v, c=cmd_tmpl: send(c.format(v=float(v))),
        )
        scale.grid(row=row_idx, column=col_pair + 1, sticky="ew", padx=(0, 12), pady=1)


# --------------------------------------------------------------------------
# 🎼 Builder tab
# --------------------------------------------------------------------------

def _build_builder_tab(parent, *, player, _var, ttk, tk) -> None:
    """Compose songs by picking a style + title (+ optional tempo).

    Drives the existing Player.style_override / Player.tempo_override
    / Player.load_phrase / Player.reroll_seed APIs. Generating a new
    song updates the rest of the GUI (Transport now-playing label,
    Generators tab knob list, Mixer + Sound activity, Instruments
    GM dropdowns) via Player.on_state_change.

    The title field doubles as the seed-defining phrase — typing
    "rolling acid trax" produces a different song from "dark acid
    trax" even at the same style + tempo. Empty title falls back to
    "new <style> song" so Generate always works.
    """
    from slackbeatz.player import KNOWN_STYLES

    ttk.Label(
        parent,
        text="🎼 Pick a style + add a title, then click Generate to "
             "compose + play. Re-roll spins a fresh variation with "
             "the same title + style. Save as… exports the current "
             "song to a .sb file you can play later.",
        wraplength=620, justify="left", foreground="#444",
    ).pack(padx=10, pady=(8, 6), anchor="w")

    form = ttk.Frame(parent)
    form.pack(fill="x", padx=10, pady=4)
    form.columnconfigure(1, weight=1)

    # Style dropdown — Sourced from player.KNOWN_STYLES so adding a
    # new style upstream surfaces here without a code edit.
    ttk.Label(form, text="Style", width=10, anchor="w").grid(
        row=0, column=0, sticky="w", pady=4,
    )
    style_var = _var(
        tk.StringVar,
        value=player.style_override if player.style_override in KNOWN_STYLES else KNOWN_STYLES[0],
    )
    style_cb = ttk.Combobox(
        form, values=KNOWN_STYLES, textvariable=style_var,
        state="readonly", width=20,
    )
    style_cb.grid(row=0, column=1, sticky="w", padx=(4, 0), pady=4)

    # Title entry. Doubles as the compose-time seed; empty falls
    # back to "new <style> song" so the button always does something.
    ttk.Label(form, text="Title", width=10, anchor="w").grid(
        row=1, column=0, sticky="w", pady=4,
    )
    title_var = _var(
        tk.StringVar,
        value=player.current_phrase or "",
    )
    ttk.Entry(form, textvariable=title_var, width=40).grid(
        row=1, column=1, sticky="ew", padx=(4, 0), pady=4,
    )
    ttk.Label(
        form, text="(also seeds the variation)",
        foreground="#888", font=("TkDefaultFont", 9, "italic"),
    ).grid(row=1, column=2, sticky="w", padx=(6, 0))

    # Tempo override — checkbox + spinbox. When unchecked, the
    # compose layer picks a style-appropriate BPM from the title's
    # sentiment hash.
    ttk.Label(form, text="Tempo", width=10, anchor="w").grid(
        row=2, column=0, sticky="w", pady=4,
    )
    tempo_row = ttk.Frame(form)
    tempo_row.grid(row=2, column=1, sticky="w", padx=(4, 0), pady=4)
    tempo_override_var = _var(
        tk.IntVar, value=1 if player.tempo_override is not None else 0,
    )
    tempo_value_var = _var(
        tk.IntVar, value=player.tempo_override or 120,
    )
    ttk.Checkbutton(
        tempo_row, text="override",
        variable=tempo_override_var,
    ).pack(side="left")
    ttk.Spinbox(
        tempo_row, from_=40, to=220,
        textvariable=tempo_value_var, width=6,
    ).pack(side="left", padx=(8, 0))
    ttk.Label(tempo_row, text=" BPM").pack(side="left")

    # Status line — shows what was last generated. Updates on every
    # Generate + on player state changes from other tabs.
    status_var = _var(tk.StringVar, value="(no song generated yet)")
    ttk.Label(
        parent, textvariable=status_var,
        foreground="#345", padding=(10, 4),
    ).pack(fill="x")

    def _refresh_status() -> None:
        # Called by the main_thread_callbacks poller on player state
        # changes (Generate / Re-roll / REPL phrase load all fire it).
        if player.current_phrase:
            src = f'"{player.current_phrase}"'
        elif player.current_song_path is not None:
            src = player.current_song_path.name
        else:
            src = "(no song generated yet)"
        style = player.style_override or "(style: auto from title)"
        status_var.set(f"current: {src}  •  style: {style}")

    _refresh_status()

    # Buttons row.
    actions = ttk.Frame(parent)
    actions.pack(fill="x", padx=10, pady=(2, 8))

    def _generate() -> None:
        # Push style + tempo overrides to the Player, then load the
        # title as the active phrase + start playback. Player handles
        # stopping any in-flight song automatically (its play() does
        # stop_locked() before kicking off the new worker).
        style = style_var.get()
        player.style_override = style if style in KNOWN_STYLES else None
        if tempo_override_var.get():
            player.tempo_override = int(tempo_value_var.get())
        else:
            player.tempo_override = None
        title = title_var.get().strip() or f"new {style} song"
        player.load_phrase(title)
        player.play()
        _refresh_status()

    def _reroll() -> None:
        # Bumps player.seed_offset + replays. Player.reroll_seed
        # returns a status string for the REPL — we discard it and
        # rely on _refresh_status / on_state_change to update the UI.
        player.reroll_seed()
        _refresh_status()

    def _save_as() -> None:
        from tkinter import filedialog
        # Suggest a filename derived from the current title — strip
        # punctuation, lowercase, _-separate, .sb suffix.
        raw = title_var.get().strip() or "new_song"
        suggested = "".join(
            c if c.isalnum() or c in " _-" else " " for c in raw
        ).strip().lower().replace(" ", "_")
        initial = f"{suggested}.sb"
        path = filedialog.asksaveasfilename(
            title="Save composed song as…",
            defaultextension=".sb",
            initialfile=initial,
            filetypes=[
                ("Slackbeatz songs", "*.sb"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        # Player.save_state writes the current composed .sb to the
        # given path + returns a status line. Re-emit via the status
        # label so the user sees it.
        try:
            status = player.save_state(path)
        except Exception as e:  # noqa: BLE001
            status = f"error: {e}"
        status_var.set(status)

    ttk.Button(actions, text="▶  Generate", command=_generate).pack(side="left", padx=2)
    ttk.Button(actions, text="↻  Re-roll", command=_reroll).pack(side="left", padx=2)
    ttk.Button(actions, text="💾  Save as…", command=_save_as).pack(side="left", padx=2)

    # ------------------------------------------------------------------
    # Parts panel — shows the song's arrangement once a song is loaded.
    # Each part gets a checkbox (skip toggle). Per-voice meter
    # overrides live on the Generators tab — different voices can
    # run in different time signatures within the same part (e.g.
    # drums in 4/4 while bass runs in 5/4), which is what the user
    # actually wants for polyrhythm work.
    # ------------------------------------------------------------------
    parts_frame = ttk.LabelFrame(parent, text="🧱  Parts")
    parts_frame.pack(fill="both", expand=True, padx=10, pady=(8, 8))

    parts_body = ttk.Frame(parts_frame)
    parts_body.pack(fill="both", expand=True, padx=8, pady=4)

    def _refresh_parts() -> None:
        # Wipe + rebuild — cheap. Fires on every state change
        # (Generate, Re-roll, REPL phrase load, skip-toggle, …).
        for w in parts_body.winfo_children():
            w.destroy()
        resolved = player.current_resolved
        if resolved is None:
            ttk.Label(
                parts_body,
                text="(generate a song to see its parts here)",
                foreground="#888", font=("TkDefaultFont", 10, "italic"),
            ).pack(anchor="w")
            return
        # Build a sorted list of unique part names from the
        # composed arrangement (drop duplicates — skip is by name,
        # not by arrangement index in v1). Show bars + use-count
        # so the user knows what each part contributes.
        seen: list[str] = []
        uses: dict[str, int] = {}
        for name in resolved.arrangement:
            if name not in seen:
                seen.append(name)
            uses[name] = uses.get(name, 0) + 1
        # Also include parts in resolved.parts that aren't in
        # arrangement — they might be in skip_parts already.
        for name in resolved.parts:
            if name not in seen and name in player.skip_parts:
                seen.append(name)
                uses[name] = 0
        if not seen:
            ttk.Label(
                parts_body,
                text="(this song has no parts? unusual — check the .sb)",
                foreground="#888", font=("TkDefaultFont", 10, "italic"),
            ).pack(anchor="w")
            return
        part_overrides_snapshot = player.get_part_overrides()
        for name in seen:
            wrapper = ttk.Frame(parts_body)
            wrapper.pack(fill="x", pady=1)
            row = ttk.Frame(wrapper)
            row.pack(fill="x")
            include_var = _var(
                tk.IntVar, value=0 if name in player.skip_parts else 1,
            )

            def _on_include(n=name, v=include_var):
                # Checkbox is "include"; skip = not include.
                player.set_skip_part(n, not bool(v.get()))

            ttk.Checkbutton(
                row, text="", variable=include_var, command=_on_include,
            ).pack(side="left")

            part = resolved.parts.get(name)
            bars = part.bars if part is not None else 0
            use_count = uses.get(name, 0)
            count_hint = f" ×{use_count}" if use_count > 1 else ""
            ttk.Label(
                row, text=f"{name}", width=14, anchor="w",
            ).pack(side="left")
            ttk.Label(
                row, text=f"({bars} bars{count_hint})",
                width=14, anchor="w", foreground="#666",
            ).pack(side="left", padx=(4, 8))

            # Show the part's meter so the user has a reference point
            # for what each voice will inherit if its meter dropdown
            # on the Generators tab is left at "auto". Read-only —
            # per-voice meter overrides live next to each gen.
            if part is not None:
                ttk.Label(
                    row, text=f"meter {part.meter}",
                    foreground="#888",
                ).pack(side="left", padx=(0, 4))

            # Expand-to-edit toggle — reveals tempo / key / scale /
            # role / tension / transpose_prob editors for this part.
            # Hidden by default to keep the Parts list compact.
            attrs_row = ttk.Frame(wrapper)
            expanded = {"on": False}
            this_overrides = part_overrides_snapshot.get(name, {})

            def _on_toggle(p=part, r=attrs_row, e=expanded,
                           n=name, ov=this_overrides):
                if e["on"]:
                    r.pack_forget()
                    e["on"] = False
                    return
                e["on"] = True
                _populate_attrs(r, p, n, ov)
                r.pack(fill="x", padx=(24, 0), pady=(0, 4))

            ttk.Button(
                row, text="▾", width=2, command=_on_toggle,
            ).pack(side="left", padx=(4, 0))

        def _populate_attrs(container, part, part_name, overrides):
            # Re-bind callback closures each rebuild so they see the
            # current part/name pair rather than the loop's last
            # iteration. Called at most once per part (when first
            # expanded), so the cost is fine.
            for child in container.winfo_children():
                child.destroy()
            if part is None:
                ttk.Label(container, text="(part not in current resolve)",
                          foreground="#888").pack(anchor="w")
                return

            def _entry(label, attr, current, kind="str", choices=None):
                line = ttk.Frame(container)
                line.pack(fill="x", pady=1)
                ttk.Label(line, text=label, width=14, anchor="w",
                          foreground="#666").pack(side="left")
                if kind == "enum":
                    var = tk.StringVar(value=str(current))
                    cb = ttk.Combobox(line, textvariable=var,
                                      values=choices or [], state="readonly",
                                      width=14)
                    cb.pack(side="left")
                    def _commit(_event, a=attr, v=var):
                        player.set_part_attr(part_name, a, v.get())
                    cb.bind("<<ComboboxSelected>>", _commit)
                else:
                    var = tk.StringVar(value="" if current is None else str(current))
                    ent = ttk.Entry(line, textvariable=var, width=14)
                    ent.pack(side="left")
                    def _commit(_event=None, a=attr, v=var, k=kind):
                        raw = v.get().strip()
                        if not raw:
                            player.set_part_attr(part_name, a, None)
                            return
                        try:
                            if k == "int":
                                player.set_part_attr(part_name, a, int(raw))
                            elif k == "float":
                                player.set_part_attr(part_name, a, float(raw))
                            else:
                                player.set_part_attr(part_name, a, raw)
                        except ValueError:
                            pass
                    ent.bind("<Return>", _commit)
                    ent.bind("<FocusOut>", _commit)
                def _clear(a=attr, v=var, k=kind):
                    player.set_part_attr(part_name, a, None)
                    v.set("")
                ttk.Button(line, text="↺", width=2, command=_clear).pack(
                    side="left", padx=(4, 0),
                )

            _entry("tempo", "tempo",
                   overrides.get("tempo", part.tempo), kind="int")
            _entry("key", "key",
                   overrides.get("key", part.key), kind="str")
            _entry("scale", "scale_override",
                   overrides.get("scale_override", part.scale_override or ""),
                   kind="str")
            _entry("role", "role",
                   overrides.get("role", part.role), kind="str")
            tn = overrides.get("tension", part.tension)
            _entry("tension", "tension",
                   "" if tn is None else f"{tn:g}", kind="float")
            _entry("transpose_prob", "transpose_prob",
                   overrides.get("transpose_prob", part.transpose_prob),
                   kind="float")

    _refresh_parts()

    # Single state-change callback that refreshes BOTH the status
    # line and the parts panel. Both share the same trigger (player
    # state change) so one entry on main_thread_callbacks is enough.
    def _refresh_all() -> None:
        _refresh_status()
        _refresh_parts()

    return _refresh_all


# --------------------------------------------------------------------------
# 🎵 Render menubar — offline MIDI / audio / stems
# --------------------------------------------------------------------------

def _reveal_in_finder(path) -> None:
    """Open a file in the platform file browser (macOS / Linux / Windows)."""
    import subprocess
    import sys
    from pathlib import Path
    p = Path(path).resolve()
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(p.parent)])
        elif sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(p)])
    except Exception:
        # Best-effort — reveal failure shouldn't tear down the GUI.
        pass


def _open_render_dialog(root, player, mode: str) -> None:
    """Open a modal render dialog for *mode* ∈ {"midi", "audio", "stems"}.

    Picks an output path, then spawns a worker thread to do the actual
    render. A progress Toplevel shows an indeterminate progress bar
    while the worker runs; on completion it swaps to a "Reveal in
    Finder" + "Close" pair.
    """
    import tkinter as tk
    from tkinter import filedialog, ttk
    from pathlib import Path
    import threading

    resolved = player.current_resolved
    if resolved is None:
        # Nothing loaded — show a brief notice instead of opening the
        # picker. Cheaper than wiring a full message-box.
        win = tk.Toplevel(root)
        win.title("Render")
        ttk.Label(
            win,
            text="No song loaded — generate or load one first.",
        ).pack(padx=20, pady=20)
        ttk.Button(win, text="OK", command=win.destroy).pack(pady=(0, 12))
        return

    # Default output dir + filename based on the song title (if any).
    title = (player.title or "slackbeatz_song").replace(" ", "_")
    default_dir = str(Path.home() / "Music" / "slackbeatz")

    if mode == "midi":
        out_path = filedialog.asksaveasfilename(
            parent=root,
            title="Render MIDI",
            initialdir=default_dir,
            initialfile=f"{title}.mid",
            defaultextension=".mid",
            filetypes=[("MIDI file", "*.mid"), ("All", "*.*")],
        )
        if not out_path:
            return
        _start_render_worker(root, player, mode, Path(out_path), use_surge=False)
        return

    if mode == "audio":
        # Small chooser for format + Surge toggle BEFORE the file dialog.
        win = tk.Toplevel(root)
        win.title("Render audio")
        win.transient(root)
        ttk.Label(win, text="Format:").grid(row=0, column=0, sticky="e", padx=6, pady=6)
        fmt_var = tk.StringVar(value="mp3")
        for col, fmt in enumerate(["mp3", "wav"]):
            ttk.Radiobutton(win, text=f".{fmt}", value=fmt, variable=fmt_var).grid(
                row=0, column=1 + col, padx=4, pady=6, sticky="w",
            )
        surge_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            win, text="Use Surge XT (slower; matches --surge live mode)",
            variable=surge_var,
        ).grid(row=1, column=0, columnspan=3, padx=6, pady=(0, 6), sticky="w")

        def _on_ok():
            fmt = fmt_var.get()
            use_surge = surge_var.get()
            win.destroy()
            out_path = filedialog.asksaveasfilename(
                parent=root,
                title="Render audio",
                initialdir=default_dir,
                initialfile=f"{title}.{fmt}",
                defaultextension=f".{fmt}",
                filetypes=[(fmt.upper(), f"*.{fmt}"), ("All", "*.*")],
            )
            if not out_path:
                return
            _start_render_worker(root, player, "audio", Path(out_path), use_surge=use_surge)

        ttk.Button(win, text="Render", command=_on_ok).grid(
            row=2, column=1, pady=(4, 10), padx=4,
        )
        ttk.Button(win, text="Cancel", command=win.destroy).grid(
            row=2, column=2, pady=(4, 10), padx=4,
        )
        return

    if mode == "stems":
        out_dir = filedialog.askdirectory(
            parent=root,
            title="Export stems to folder",
            initialdir=default_dir,
        )
        if not out_dir:
            return
        # Append the song title so multiple exports don't collide.
        out_path = Path(out_dir) / f"{title}_stems"
        _start_render_worker(root, player, "stems", out_path, use_surge=False)
        return


def _start_render_worker(root, player, mode: str, out_path, *, use_surge: bool) -> None:
    """Spawn the render thread + show the progress popup."""
    import tkinter as tk
    from tkinter import ttk
    import threading
    from pathlib import Path

    win = tk.Toplevel(root)
    win.title(f"Rendering {mode}…")
    win.transient(root)
    ttk.Label(win, text=f"Rendering {mode} to:").pack(padx=14, pady=(12, 2))
    ttk.Label(win, text=str(out_path), foreground="#444").pack(padx=14)
    bar = ttk.Progressbar(win, mode="indeterminate", length=320)
    bar.pack(padx=14, pady=10)
    bar.start(40)
    status_var = tk.StringVar(value="working…")
    ttk.Label(win, textvariable=status_var, foreground="#666").pack(padx=14, pady=(0, 6))
    btn_row = ttk.Frame(win)
    btn_row.pack(padx=14, pady=(0, 12))

    state = {"done": False, "error": None}

    def _worker():
        try:
            _do_render(player, mode, out_path, use_surge=use_surge)
        except Exception as exc:  # noqa: BLE001 — surfaced in the dialog
            state["error"] = exc
        finally:
            state["done"] = True

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    def _poll():
        if not state["done"]:
            root.after(200, _poll)
            return
        bar.stop()
        bar["mode"] = "determinate"
        bar["value"] = 100
        if state["error"]:
            status_var.set(f"failed: {state['error']}")
        else:
            status_var.set("done.")
            ttk.Button(
                btn_row, text="Reveal in Finder",
                command=lambda: _reveal_in_finder(out_path),
            ).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Close", command=win.destroy).pack(side="left", padx=4)

    root.after(200, _poll)


def _do_render(player, mode: str, out_path, *, use_surge: bool) -> None:
    """Run the actual render. Called from a worker thread."""
    from pathlib import Path
    from slackbeatz.engine.midifile import write_midifile

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Re-resolve so the render captures the latest overrides (knob,
    # arrangement, skip_parts, etc). _resolve_current re-uses the
    # currently-loaded phrase / file under the Player's lock so it's
    # safe to call from this worker thread.
    resolved = player._resolve_current()

    if mode == "midi":
        write_midifile(resolved, out_path)
        return

    if mode == "audio":
        if use_surge:
            from slackbeatz.audio_offline import render_song_with_surge
            from slackbeatz.audio import find_soundfont
            soundfont = find_soundfont(None)
            render_song_with_surge(
                resolved, out_path, soundfont=soundfont,
                sample_rate=44100, bitrate="192k",
            )
        else:
            import tempfile
            from slackbeatz.audio import find_soundfont, render_audio
            soundfont = find_soundfont(None)
            with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
                tmp_mid = Path(tmp.name)
            try:
                write_midifile(resolved, tmp_mid)
                render_audio(tmp_mid, out_path, soundfont)
            finally:
                tmp_mid.unlink(missing_ok=True)
        return

    if mode == "stems":
        from slackbeatz.export import export_bundle
        from slackbeatz.audio import find_soundfont
        soundfont = find_soundfont(None)
        export_bundle(resolved, out_path, soundfont=soundfont, sample_rate=44100)
        return

    raise ValueError(f"unknown render mode: {mode}")


# --------------------------------------------------------------------------
# 🔍 Inspect menu — text-window dumps of overrides / status / lists
# --------------------------------------------------------------------------

def _open_inspect_dialog(root, player, mode: str) -> None:
    """Pop up a read-only text view for *mode*.

    Modes mirror the corresponding REPL / CLI commands:

    * ``overrides``  — Player._knob_overrides + arrangement / skip /
                       part overrides (same content as ``/knob`` with
                       no args).
    * ``status``     — current song name, tempo, key, arrangement,
                       gen layout (same as REPL ``/status``).
    * ``generators`` — every registered (type, style) pair.
    * ``setups``     — bundled setup names.
    * ``ports``      — available MIDI output ports.
    * ``validate``   — re-resolve current song; show errors or
                       "ok — N gens, N parts, …".
    """
    import tkinter as tk
    from tkinter import ttk

    title_map = {
        "overrides": "Active overrides",
        "status": "Song status",
        "generators": "Registered generators",
        "setups": "Bundled setups",
        "ports": "Available MIDI ports",
        "validate": "Validate current song",
    }
    win = tk.Toplevel(root)
    win.title(title_map.get(mode, "Inspect"))
    win.transient(root)
    win.geometry("640x460")

    body = tk.Text(win, wrap="word", font=("Menlo", 11),
                   borderwidth=1, relief="solid")
    body.pack(fill="both", expand=True, padx=10, pady=(10, 4))
    sb = ttk.Scrollbar(win, orient="vertical", command=body.yview)
    body.configure(yscrollcommand=sb.set)

    btn_row = ttk.Frame(win)
    btn_row.pack(fill="x", padx=10, pady=(0, 10))
    ttk.Button(btn_row, text="Close", command=win.destroy).pack(side="right")

    try:
        text = _inspect_body(player, mode)
    except Exception as exc:  # noqa: BLE001 — surface to user
        text = f"error: {exc}"

    body.insert("end", text)
    body.configure(state="disabled")


def _inspect_body(player, mode: str) -> str:
    """Compose the body text for an inspect popup."""
    if mode == "generators":
        from slackbeatz.generators.registry import list_generators
        out = ["# Registered generators (type / style)"]
        for type_, style in list_generators():
            out.append(f"  {type_:10s}  {style}")
        return "\n".join(out)

    if mode == "setups":
        from slackbeatz.setup.loader import list_bundled_setups
        out = ["# Bundled setups"]
        for name in list_bundled_setups():
            out.append(f"  {name}")
        return "\n".join(out)

    if mode == "ports":
        from slackbeatz.sinks.realtime import available_ports
        ports = available_ports() or []
        if not ports:
            return ("No MIDI output ports.\n\n"
                    "On macOS: open Audio MIDI Setup → MIDI Studio → "
                    "enable the IAC Driver to create a virtual port.")
        out = ["# Available MIDI output ports"]
        for p in ports:
            marker = "  ← active" if p == player.port_name else ""
            out.append(f"  {p}{marker}")
        return "\n".join(out)

    if mode == "status":
        resolved = player.current_resolved
        if resolved is None:
            return ("(no song loaded — generate via the Builder tab "
                    "or load a .sb from the REPL)")
        lines = [
            f"name:        {resolved.name}",
            f"tempo:       {resolved.tempo}",
            f"key:         {resolved.key}",
            f"meter:       {resolved.meter}",
            f"seed:        {resolved.seed}",
            f"setup:       {resolved.setup.name}",
            f"parts:       {len(resolved.parts)}",
            f"gens:        {len(resolved.gens)}",
            f"arrangement: {' → '.join(resolved.arrangement)}",
        ]
        lines.append("")
        lines.append("# Parts")
        for name, part in resolved.parts.items():
            tn = "auto" if part.tension is None else f"{part.tension:g}"
            lines.append(
                f"  {name:12s}  bars={part.bars:3d}  tempo={part.tempo:3d}  "
                f"key={part.key:6s}  role={part.role:8s}  tension={tn}"
            )
        lines.append("")
        lines.append("# Gens")
        for handle, gen in resolved.gens.items():
            meter = str(gen.meter) if gen.meter is not None else "(inherit)"
            lines.append(
                f"  {handle:12s}  {gen.type_:8s}  {gen.style:14s}  "
                f"meter={meter}"
            )
        return "\n".join(lines)

    if mode == "overrides":
        lines = ["# Active overrides"]
        # Knob overrides.
        ko = player.get_knob_overrides()
        if ko:
            lines.append("\n## Knob overrides")
            for handle, knobs in ko.items():
                for n, v in knobs.items():
                    lines.append(f"  {handle}.{n} = {v}")
        else:
            lines.append("\n## Knob overrides — (none)")

        # Part attribute overrides.
        po = player.get_part_overrides()
        if po:
            lines.append("\n## Part attribute overrides")
            for pname, attrs in po.items():
                for a, v in attrs.items():
                    lines.append(f"  {pname}.{a} = {v}")
        else:
            lines.append("\n## Part attribute overrides — (none)")

        # Skip-parts.
        if player.skip_parts:
            lines.append("\n## Skipped parts")
            for n in sorted(player.skip_parts):
                lines.append(f"  {n}")
        else:
            lines.append("\n## Skipped parts — (none)")

        # Arrangement override.
        if player.arrangement_override is not None:
            lines.append("\n## Arrangement override")
            lines.append("  " + " → ".join(player.arrangement_override))
        else:
            lines.append("\n## Arrangement override — (using .sb default)")

        # Gen meter overrides.
        if player.gen_meter_overrides:
            lines.append("\n## Per-gen meter overrides")
            for h, m in player.gen_meter_overrides.items():
                lines.append(f"  {h} → {m}")
        else:
            lines.append("\n## Per-gen meter overrides — (none)")

        # Top-level transport overrides.
        lines.append("\n## Transport overrides")
        lines.append(f"  tempo override : {player.tempo_override or '(auto)'}")
        lines.append(f"  style override : {player.style_override or '(auto)'}")
        lines.append(f"  seed offset    : {player.seed_offset}")
        lines.append(f"  emit clock     : {player.emit_clock}")
        return "\n".join(lines)

    if mode == "validate":
        try:
            resolved = player._resolve_current()
        except Exception as e:  # noqa: BLE001 — surface to user
            return f"validation failed:\n  {e}"
        n_bars = sum(
            resolved.parts[p].bars for p in resolved.arrangement
            if p in resolved.parts
        )
        return (
            f"ok — {len(resolved.gens)} gens, "
            f"{len(resolved.parts)} parts, "
            f"{len(resolved.arrangement)} arrangement slots, "
            f"{n_bars} bars total"
        )

    return f"(unknown inspect mode {mode!r})"


# --------------------------------------------------------------------------
# 🔌 I/O tab — MIDI port + soundfont + clock view
# --------------------------------------------------------------------------

def _build_io_tab(parent, *, player, _var, ttk, tk):
    """Display MIDI port + soundfont + MIDI clock state.

    Port + soundfont are baked in at launch (changing them needs a
    restart) — we surface the values so the user can confirm what
    they're hearing. MIDI clock + external-clock are runtime-settable
    and get live toggles here.
    """
    ttk.Label(
        parent,
        text="🔌 Audio + MIDI routing for this session. Most values "
             "lock in at launch — pass --port / --soundfont / "
             "--clock when starting slackbeatz to change them.",
        wraplength=520, justify="left", foreground="#444",
    ).pack(padx=10, pady=(8, 4), anchor="w")

    grid = ttk.Frame(parent)
    grid.pack(fill="x", padx=10, pady=4)
    grid.columnconfigure(1, weight=1)

    row = 0
    ttk.Label(grid, text="MIDI port:", foreground="#666").grid(
        row=row, column=0, sticky="e", padx=(0, 6), pady=2,
    )
    ttk.Label(grid, text=str(player.port_name or "(none)"),
              font=("TkDefaultFont", 10, "bold")).grid(
        row=row, column=1, sticky="w", pady=2,
    )

    row += 1
    ttk.Label(grid, text="Available ports:", foreground="#666").grid(
        row=row, column=0, sticky="ne", padx=(0, 6), pady=2,
    )
    ports_text = tk.Text(grid, height=4, width=44, wrap="none",
                         borderwidth=1, relief="solid")
    ports_text.grid(row=row, column=1, sticky="w", pady=2)
    try:
        from slackbeatz.sinks.realtime import available_ports
        ports = available_ports() or []
    except Exception:
        ports = []
    ports_text.insert("end", "\n".join(ports) if ports else "(none)")
    ports_text.configure(state="disabled")

    row += 1
    ttk.Label(grid, text="Soundfont:", foreground="#666").grid(
        row=row, column=0, sticky="e", padx=(0, 6), pady=2,
    )
    sf_path = "(not loaded)"
    try:
        from slackbeatz.audio import find_soundfont
        sf_path = str(find_soundfont(None))
    except Exception as e:  # noqa: BLE001
        sf_path = f"(unset: {e})"
    ttk.Label(grid, text=sf_path, wraplength=420,
              foreground="#222", justify="left").grid(
        row=row, column=1, sticky="w", pady=2,
    )

    row += 1
    ttk.Separator(grid, orient="horizontal").grid(
        row=row, column=0, columnspan=2, sticky="ew", pady=8,
    )

    row += 1
    ttk.Label(grid, text="MIDI Clock:", foreground="#666").grid(
        row=row, column=0, sticky="e", padx=(0, 6), pady=2,
    )
    clock_var = _var(tk.IntVar, value=1 if player.emit_clock else 0)
    ttk.Checkbutton(
        grid, text="Send MIDI Clock (sync external gear)",
        variable=clock_var,
        command=lambda: player.set_emit_clock(bool(clock_var.get())),
    ).grid(row=row, column=1, sticky="w", pady=2)

    row += 1
    ttk.Label(
        parent,
        text="Tip: `slackbeatz list-ports` shows the same ports your "
             "OS exposes. To stream to an external DAW, start "
             "slackbeatz with --port \"My DAW Port\".",
        wraplength=520, justify="left", foreground="#888",
    ).pack(padx=10, pady=(8, 4), anchor="w")


# --------------------------------------------------------------------------
# 🎛 Setup tab — read-only inst / kit visibility
# --------------------------------------------------------------------------

def _build_setup_tab(parent, *, player, _var, ttk, tk):
    """Display the loaded ``Setup``'s inst + kit declarations.

    Two stacked tables: Instruments (name, channel, fixed note if any)
    and Kits (name, channel, then nested rows for each drum→note).
    Read-only for now — edit support follows once
    ``Setup.to_dsl()`` lands.
    """
    ttk.Label(
        parent,
        text="🎛 Active setup — channel + note routing the song uses. "
             "Read-only for now; edit support coming. Switch the .sb's "
             "setup= line to pick a different rig.",
        wraplength=520, justify="left", foreground="#444",
    ).pack(padx=10, pady=(8, 4), anchor="w")

    name_lbl = ttk.Label(parent, text="(no setup loaded)",
                         font=("TkDefaultFont", 11, "bold"))
    name_lbl.pack(padx=10, anchor="w")

    body = ttk.Frame(parent)
    body.pack(fill="both", expand=True, padx=10, pady=(4, 8))

    def _refresh() -> None:
        for child in body.winfo_children():
            child.destroy()
        setup = getattr(player, "current_setup", None)
        if setup is None:
            name_lbl["text"] = "(no setup loaded — load a song first)"
            return
        name_lbl["text"] = f"setup: {setup.name}"

        inst_frame = ttk.LabelFrame(body, text="Instruments")
        inst_frame.pack(fill="x", pady=(0, 8))
        if not setup.instruments:
            ttk.Label(inst_frame, text="(none)", foreground="#888").pack(
                anchor="w", padx=8, pady=4,
            )
        else:
            header = ttk.Frame(inst_frame)
            header.pack(fill="x", padx=6, pady=(4, 2))
            ttk.Label(header, text="name", width=20, anchor="w",
                      foreground="#666").pack(side="left")
            ttk.Label(header, text="ch", width=6, anchor="w",
                      foreground="#666").pack(side="left")
            ttk.Label(header, text="note", width=8, anchor="w",
                      foreground="#666").pack(side="left")
            for inst in setup.instruments.values():
                row = ttk.Frame(inst_frame)
                row.pack(fill="x", padx=6, pady=1)
                ttk.Label(row, text=inst.name, width=20, anchor="w",
                          font=("TkDefaultFont", 10, "bold")).pack(side="left")
                ttk.Label(row, text=str(inst.channel), width=6,
                          anchor="w").pack(side="left")
                note_text = str(inst.note) if inst.note is not None else "(pitched)"
                ttk.Label(row, text=note_text, width=10,
                          anchor="w", foreground="#666").pack(side="left")

        kit_frame = ttk.LabelFrame(body, text="Kits")
        kit_frame.pack(fill="both", expand=True)
        if not setup.kits:
            ttk.Label(kit_frame, text="(none)", foreground="#888").pack(
                anchor="w", padx=8, pady=4,
            )
        else:
            for kit in setup.kits.values():
                kit_row = ttk.Frame(kit_frame)
                kit_row.pack(fill="x", padx=6, pady=(6, 0))
                ttk.Label(kit_row,
                          text=f"{kit.name}  (ch {kit.channel})",
                          font=("TkDefaultFont", 10, "bold")).pack(side="left")
                drum_grid = ttk.Frame(kit_frame)
                drum_grid.pack(fill="x", padx=20, pady=(0, 4))
                # 4-column grid of drum→note pairs.
                items = list(kit.drum_notes.items())
                for i, (drum, note) in enumerate(items):
                    r, c = divmod(i, 4)
                    ttk.Label(drum_grid,
                              text=f"{drum}={note}",
                              foreground="#444").grid(
                        row=r, column=c, padx=(0, 12), sticky="w",
                    )

    _refresh()
    return _refresh


# --------------------------------------------------------------------------
# 🎬 Arrangement tab
# --------------------------------------------------------------------------

def _build_arrangement_tab(parent, *, player, _var, ttk, tk, root):
    """Editable list of part positions in the song's arrangement.

    Each row corresponds to one position in the linear arrangement
    (groups + ``*N`` repeats are flattened — a repeat is just multiple
    rows of the same part). ⇡ / ⇣ reorder, ✕ deletes, the "Add part"
    dropdown at the bottom appends. "Reset" clears
    ``player.arrangement_override`` so the .sb's original ``play``
    line takes effect again.
    """
    _persistent: list = []

    ttk.Label(
        parent,
        text="🎬 Arrangement order. Reorder with ⇡ ⇣, delete with ✕, "
             "or append via the dropdown below. Changes apply on the "
             "next play / restart. Reset reverts to the song's "
             "original play line.",
        wraplength=520, justify="left", foreground="#444",
    ).pack(padx=10, pady=(8, 4), anchor="w")

    rows_frame = ttk.Frame(parent)
    rows_frame.pack(fill="both", expand=True, padx=10, pady=4)

    footer = ttk.Frame(parent)
    footer.pack(fill="x", padx=10, pady=(4, 8))

    add_var = _var(tk.StringVar, value="")
    add_cb = ttk.Combobox(footer, textvariable=add_var, state="readonly", width=18)
    add_cb.pack(side="left")

    def _current_seq() -> list[str]:
        if player.arrangement_override is not None:
            return list(player.arrangement_override)
        resolved = player.current_resolved
        if resolved is not None:
            return list(resolved.arrangement)
        return []

    def _available_parts() -> list[str]:
        resolved = player.current_resolved
        if resolved is None:
            return []
        return list(resolved.parts.keys())

    def _apply(seq: list[str]) -> None:
        # Apply the new sequence as an override. Empty list still
        # qualifies as an override (it just means "skip everything") —
        # callers wanting to revert call _reset() instead.
        player.set_arrangement(seq)
        _refresh()

    def _move(i: int, delta: int) -> None:
        seq = _current_seq()
        j = i + delta
        if not (0 <= j < len(seq)):
            return
        seq[i], seq[j] = seq[j], seq[i]
        _apply(seq)

    def _delete(i: int) -> None:
        seq = _current_seq()
        if 0 <= i < len(seq):
            del seq[i]
            _apply(seq)

    def _add() -> None:
        name = add_var.get().strip()
        if not name:
            return
        seq = _current_seq()
        seq.append(name)
        _apply(seq)

    def _reset() -> None:
        player.set_arrangement(None)
        _refresh()

    add_btn = ttk.Button(footer, text="+ add", command=_add)
    add_btn.pack(side="left", padx=(4, 0))
    reset_btn = ttk.Button(footer, text="↺ reset to .sb", command=_reset)
    reset_btn.pack(side="right")

    def _refresh() -> None:
        for child in rows_frame.winfo_children():
            child.destroy()

        seq = _current_seq()
        if not seq:
            ttk.Label(
                rows_frame,
                text="(no song loaded — load one via the Builder tab)",
                foreground="#888",
            ).pack(padx=10, pady=20)
        else:
            for i, name in enumerate(seq):
                row = ttk.Frame(rows_frame)
                row.pack(fill="x", pady=1)
                ttk.Label(
                    row, text=f"{i+1:2d}.", width=4, anchor="e",
                    foreground="#888",
                ).pack(side="left")
                ttk.Label(
                    row, text=name, width=20, anchor="w",
                    font=("TkDefaultFont", 10, "bold"),
                ).pack(side="left", padx=(6, 6))
                up_btn = ttk.Button(
                    row, text="⇡", width=2,
                    command=lambda i=i: _move(i, -1),
                )
                up_btn.pack(side="left")
                down_btn = ttk.Button(
                    row, text="⇣", width=2,
                    command=lambda i=i: _move(i, +1),
                )
                down_btn.pack(side="left", padx=(2, 0))
                del_btn = ttk.Button(
                    row, text="✕", width=2,
                    command=lambda i=i: _delete(i),
                )
                del_btn.pack(side="left", padx=(6, 0))
                _persistent.append((up_btn, down_btn, del_btn))

        # Refresh dropdown choices to match the currently-loaded song.
        avail = _available_parts()
        add_cb["values"] = avail
        if avail and add_var.get() not in avail:
            add_var.set(avail[0])
        elif not avail:
            add_var.set("")

        if player.arrangement_override is not None:
            reset_btn.state(["!disabled"])
        else:
            reset_btn.state(["disabled"])

    _refresh()
    _persistent.append(_refresh)
    return _refresh


def run_tweak_gui(
    fs_stdin: Optional[IO[bytes]],
    *,
    initial_gain: float | None = None,
    initial_reverb_room: float | None = None,
    initial_programs: dict[int, int] | None = None,
    player=None,
    show_surge_gui_routing_hint: bool = False,
    surge_instances: list | None = None,
    on_close: Callable[[], None] | None = None,
) -> None:
    """Open the tweak window. Blocks until the user closes it.

    Parameters
    ----------
    fs_stdin:
        FluidSynth's stdin pipe (from ``subprocess.Popen(..., stdin=PIPE)``).
        Slider movements / dropdown changes write shell commands to
        this file handle. ``None`` in bare-MIDI mode — the Mixer hides
        the drums strip + Instruments tab features that need
        FluidSynth, but the rest of the GUI still works.
    initial_gain, initial_reverb_room:
        Override the slider defaults to match values the user passed via
        ``--gain`` / ``--reverb`` on the CLI. Ignored when fs_stdin is None.
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
    except ImportError as e:  # noqa: PERF203 — error message is what matters here
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
        # In bare-MIDI mode there is no FluidSynth → no stdin to write
        # to. The Mixer + Instruments tabs hide their FluidSynth-only
        # controls in that case, so this branch is rarely hit; the
        # guard is defensive against a stale slider callback firing
        # mid-shutdown.
        if fs_stdin is None:
            return
        try:
            fs_stdin.write((cmd + "\n").encode("utf-8"))
            fs_stdin.flush()
        except (BrokenPipeError, OSError):
            # FluidSynth already gone; the parent will handle shutdown.
            pass

    # CRITICAL: the Homebrew python-tk@3.12 Tcl 9 build is *non-threaded*
    # (``tcl_platform(threaded)`` is undefined). Any Tcl call from a
    # non-main thread aborts the process with:
    #
    #   Tcl_WaitForEvent: Notifier not initialized
    #   zsh: trace trap
    #
    # CPython's cyclic garbage collector can run on *any* thread that
    # crosses an allocation threshold. If a tkinter.Variable (or any
    # tk widget) gets reclaimed by cyclic-GC on the REPL daemon thread
    # or the Player worker thread, its __del__ calls Tcl_UnsetVar /
    # Tcl_DeleteCommand from the wrong thread and the process dies.
    #
    # Mitigation strategy:
    #
    # 1. Disable cyclic GC for the lifetime of the GUI. Reference
    #    counting (which is per-thread but deterministic-on-the-decref
    #    thread) still works for non-cyclic cleanup. Widget destroy()
    #    calls happen on the main thread, so widget refcount drops
    #    happen there.
    #
    # 2. Keep every tk.Variable we ever create alive in a permanent
    #    list. Variables form the most common cycles (widget ↔ command
    #    closure ↔ var), so denying them GC eligibility removes the
    #    biggest hazard. The memory cost is bounded (~50 vars per song
    #    layout × a few layouts per session ≈ few KB).
    # See comment above re: non-threaded Tcl. We pin every Tk object
    # we create — Variables, widgets, root — so reference-counting drops
    # to zero never happen on a background thread.
    import gc
    gc.disable()
    _persistent: list = []

    def _var(cls, *args, **kwargs):
        v = cls(*args, **kwargs)
        _persistent.append(v)
        return v

    def _pin(widget):
        """Pin a widget too — its dealloc chain can also touch Tcl."""
        _persistent.append(widget)
        return widget

    root = tk.Tk()
    _persistent.append(root)
    root.title("slackbeatz live — tweak")
    root.minsize(440, 480)

    # ------------------------------------------------------------------
    # 🎵 Render menubar — offline MIDI / audio / stems rendering of the
    # currently-loaded song. Each entry opens a small dialog (file
    # picker + Render button); the actual render runs on a worker
    # thread so the GUI stays responsive, with a progress popup and a
    # "Reveal in Finder" button on completion.
    # ------------------------------------------------------------------
    if player is not None:
        menubar = tk.Menu(root)
        render_menu = tk.Menu(menubar, tearoff=False)
        render_menu.add_command(
            label="Render MIDI…",
            command=lambda: _open_render_dialog(root, player, "midi"),
        )
        render_menu.add_command(
            label="Render audio…",
            command=lambda: _open_render_dialog(root, player, "audio"),
        )
        render_menu.add_command(
            label="Export stems…",
            command=lambda: _open_render_dialog(root, player, "stems"),
        )
        menubar.add_cascade(label="🎵 Render", menu=render_menu)

        # 🔍 Inspect menu — surfaces the same info as the REPL's /knobs,
        # /status, slackbeatz list-generators, list-setups, list-ports
        # and the `check` validate command, but in a Tk popup.
        inspect_menu = tk.Menu(menubar, tearoff=False)
        inspect_menu.add_command(
            label="Active overrides",
            command=lambda: _open_inspect_dialog(root, player, "overrides"),
        )
        inspect_menu.add_command(
            label="Song status",
            command=lambda: _open_inspect_dialog(root, player, "status"),
        )
        inspect_menu.add_separator()
        inspect_menu.add_command(
            label="List generators",
            command=lambda: _open_inspect_dialog(root, player, "generators"),
        )
        inspect_menu.add_command(
            label="List bundled setups",
            command=lambda: _open_inspect_dialog(root, player, "setups"),
        )
        inspect_menu.add_command(
            label="List MIDI ports",
            command=lambda: _open_inspect_dialog(root, player, "ports"),
        )
        inspect_menu.add_separator()
        inspect_menu.add_command(
            label="Validate current song",
            command=lambda: _open_inspect_dialog(root, player, "validate"),
        )
        menubar.add_cascade(label="🔍 Inspect", menu=inspect_menu)

        root.config(menu=menubar)

    # NOTE: an earlier version of this code checked
    # ``tcl_platform(threaded)`` here to refuse non-threaded Tcl
    # outright. That check is wrong for Tcl 9 — the variable was
    # removed (Tcl 9 ships threaded by default), so the check rejected
    # python.org's Python 3.14 + Tcl 9.0.3 even though that combo is
    # safe (apartment-threaded with _tkinter raising RuntimeError on
    # cross-thread Tcl calls).
    #
    # The empirical thread-safety probe now lives in cli.cmd_repl
    # before any of this code is reached. If we get here, the
    # caller has already confirmed the GUI is safe to launch.

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=6, pady=6)

    # Thread-safe state-change signalling. The Player runs on the REPL
    # daemon thread (and on the GUI main thread when sliders are
    # touched). Calling Tk methods (``root.after``, widget mutation,
    # etc.) from any thread other than the one that created the root
    # is unsafe — Tcl 9 enforces this strictly and aborts with
    # "Tcl_WaitForEvent: Notifier not initialized" when a foreign
    # thread tries to schedule a callback.
    #
    # Solution: callbacks from any thread just set this Event, and the
    # main-thread polling loop (_poll_state below) does the actual UI
    # work on a stable cadence. Every UI-refresh function that wants
    # to react to Player state changes registers itself in
    # main_thread_callbacks; the poll drains them all in one pass.
    import threading
    state_dirty = threading.Event()
    main_thread_callbacks: list[Callable[[], None]] = []

    if player is not None:
        def _on_player_state_change_safe():
            state_dirty.set()
        player.on_state_change = _on_player_state_change_safe

    def _poll_state():
        if state_dirty.is_set():
            state_dirty.clear()
            for fn in main_thread_callbacks:
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001
                    import sys
                    print(f"gui state callback failed: {exc}", file=sys.stderr)
        # 80ms feels instant for the now-playing label without
        # spinning the event loop too aggressively.
        root.after(80, _poll_state)

    # ------------------------------------------------------------------
    # 🎼 Builder tab — pick a style + title, click Generate. Renders
    # first so it's the natural "start here" surface when the user
    # opens the GUI without a song already loaded. Only available when
    # a Player is wired in (it drives style_override / load_phrase /
    # play directly on the shared Player).
    # ------------------------------------------------------------------
    builder_refresh = None
    if player is not None:
        builder_tab = ttk.Frame(notebook)
        notebook.add(builder_tab, text="🎼 Builder")
        builder_refresh = _build_builder_tab(
            builder_tab, player=player, _var=_var, ttk=ttk, tk=tk,
        )
        main_thread_callbacks.append(builder_refresh)

    # ------------------------------------------------------------------
    # 🎬 Arrangement tab — reorder / add / remove parts in the play
    # sequence at runtime. Edits the Player's ``arrangement_override``;
    # the .sb's original ``play`` line is unchanged until the user
    # explicitly saves via the Builder. Each row is a single
    # arrangement position (groups are flattened — repeats can be made
    # by duplicating rows).
    # ------------------------------------------------------------------
    if player is not None:
        arr_tab = ttk.Frame(notebook)
        notebook.add(arr_tab, text="🎬 Arrangement")
        arr_refresh = _build_arrangement_tab(
            arr_tab, player=player, _var=_var, ttk=ttk, tk=tk, root=root,
        )
        main_thread_callbacks.append(arr_refresh)

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
        nowplaying_var = _var(tk.StringVar, value="(no song loaded)")
        nowplaying_lbl = ttk.Label(
            transport, textvariable=nowplaying_var,
            font=("TkDefaultFont", 12, "bold"),
        )
        nowplaying_lbl.pack(padx=10, pady=(10, 4), anchor="w")

        playstop_var = _var(tk.StringVar, value="▶ Play")

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

        # Main-thread refresh is driven by the poll loop above —
        # register the callback so it fires whenever state_dirty is set.
        main_thread_callbacks.append(_refresh_nowplaying)

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
        tempo_var = _var(tk.IntVar, value=120 if player.tempo_override is None else player.tempo_override)

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
        style_var = _var(tk.StringVar, value="(auto)")
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
        seed_var = _var(tk.StringVar, value=str(player.seed_offset))
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
        seek_bar_var = _var(tk.StringVar, value="1")
        ttk.Entry(seek_row, textvariable=seek_bar_var, width=6).pack(side="left", padx=2)
        ttk.Label(seek_row, text=" beat ").pack(side="left")
        seek_beat_var = _var(tk.StringVar, value="0")
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

        # Position slider — drag to seek, polled to stay in sync with
        # playback. Uses 0..1 normalised tick space so the Scale widget
        # doesn't have to re-range every time the song length changes.
        pos_row = ttk.Frame(transport); pos_row.pack(fill="x", padx=10, pady=(8, 2))
        ttk.Label(pos_row, text="Position", width=14, anchor="w").pack(side="left")

        position_var = _var(tk.DoubleVar, value=0.0)
        position_label_var = _var(tk.StringVar, value="—")
        # Drag-state flag so the polling loop doesn't fight the user
        # while they're actively dragging. We *only* commit a seek on
        # mouse release — moving the thumb mid-drag would re-resolve
        # the song dozens of times per second.
        position_dragging = {"on": False}

        # tk.Scale (not ttk.Scale) — the macOS Aqua ttk scale "pages"
        # on trough-click (jumps by a fixed step instead of to the
        # click x), which makes the position slider feel broken for
        # seeking. tk.Scale gives us click-to-jump on every platform.
        # We also force `showvalue=False` because the position label
        # already shows "bar N beat M / TOTAL" beside the slider.
        position_slider = tk.Scale(
            pos_row, from_=0.0, to=1.0, resolution=0.001,
            orient="horizontal", variable=position_var, length=300,
            showvalue=False,
        )
        position_slider.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Label(
            pos_row, textvariable=position_label_var, width=24, anchor="w",
            foreground="#345", font=("TkFixedFont", 10),
        ).pack(side="left", padx=4)

        def _on_pos_press(event):
            # Mark "user is dragging" so the poll loop doesn't fight
            # us by overwriting position_var. Also explicitly jump
            # the thumb to the clicked x coordinate — belt-and-
            # suspenders against any theme-quirky paging behaviour.
            position_dragging["on"] = True
            widget = event.widget
            width = max(1, widget.winfo_width())
            fraction = max(0.0, min(1.0, event.x / width))
            position_var.set(fraction)

        def _on_pos_release(_e):
            # Read the dragged-to position FIRST. If we cleared the
            # dragging flag before reading, the 100ms poll loop could
            # fire in between and overwrite position_var with the
            # current playback tick — making the seek effectively a
            # no-op (or, worse, "snap back to where playback was").
            target_fraction = float(position_var.get())
            position_dragging["on"] = False
            if player is None:
                return
            total = player.get_total_ticks()
            if total <= 0:
                return
            target = int(target_fraction * total)
            player.seek_to_tick(target)
            _refresh_nowplaying()

        position_slider.bind("<ButtonPress-1>", _on_pos_press)
        position_slider.bind("<ButtonRelease-1>", _on_pos_release)
        # Also handle drag motion — without this the thumb wouldn't
        # follow the cursor when the user click-and-drags from a
        # trough position past the original click.
        def _on_pos_motion(event):
            if not position_dragging["on"]:
                return
            widget = event.widget
            width = max(1, widget.winfo_width())
            fraction = max(0.0, min(1.0, event.x / width))
            position_var.set(fraction)
        position_slider.bind("<B1-Motion>", _on_pos_motion)

        def _refresh_position():
            """Poll the player every 100ms and reflect playback in the
            slider position + bar/beat readout. Skipped while the user
            is dragging so the thumb doesn't snap away under the cursor.

            Readout format: ``bar N beat M.M / TOTAL`` where N is the
            current bar (1-indexed), M.M is the fractional beat within
            it, and TOTAL is the song's total bars across all
            arrangement instances. When the playhead is on a downbeat
            the beat is omitted (just ``bar 5 / 16``); past the song
            end we show ``TOTAL (end)`` so it's clear playback has
            wrapped.
            """
            if player is not None:
                total = player.get_total_ticks()
                current = player.get_current_tick()
                if total > 0:
                    if not position_dragging["on"]:
                        # Update the bound DoubleVar directly — going
                        # through Scale.set() would fire the command
                        # callback and force a re-render.
                        position_var.set(current / total)
                    label = player.get_position_label(current)
                    total_bars = player.get_total_bars()
                    if label.startswith("end"):
                        # _tick_to_bar_label returns "end (N)" when the
                        # tick is past the song; collapse to a tidy
                        # "TOTAL (end)" since we already know total.
                        text = f"{total_bars} (end)"
                    else:
                        text = f"bar {label}"
                        if total_bars > 0:
                            text += f" / {total_bars}"
                    position_label_var.set(text)
                else:
                    position_label_var.set("—")
            # Re-arm. 100ms = noticeably-smooth playhead movement
            # without burning CPU on Tk redraws.
            transport.after(100, _refresh_position)

        _refresh_position()

        # Loop + preserve-position toggles on one row.
        toggle_row = ttk.Frame(transport); toggle_row.pack(fill="x", padx=10, pady=4)
        loop_var = _var(tk.IntVar, value=1 if player.loop else 0)
        ttk.Checkbutton(
            toggle_row, text="Loop on song end", variable=loop_var,
            command=lambda: player.set_loop(bool(loop_var.get())),
        ).pack(side="left", padx=2)
        preserve_var = _var(tk.IntVar, value=1 if player.preserve_position else 0)
        ttk.Checkbutton(
            toggle_row, text="Preserve bar across param changes",
            variable=preserve_var,
            command=lambda: player.set_preserve_position(bool(preserve_var.get())),
        ).pack(side="left", padx=8)

        # MIDI Clock output.
        clock_row = ttk.Frame(transport); clock_row.pack(fill="x", padx=10, pady=4)
        clock_var = _var(tk.IntVar, value=1 if player.emit_clock else 0)
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

        # When --surge spawned external Surge XT instances, show the
        # per-window MIDI input picklist inside the GUI so users can
        # configure each Surge XT window without leaving Tk to consult
        # the terminal. Each window listens on its own dedicated
        # slackbeatz virtual port (no channel filter needed).
        if show_surge_gui_routing_hint:
            ttk.Separator(transport, orient="horizontal").pack(fill="x", padx=10, pady=8)
            ttk.Label(
                transport,
                text="🎛  Surge XT routing",
                font=("TkDefaultFont", 11, "bold"),
            ).pack(padx=10, anchor="w")
            from slackbeatz.synthhost import OSC_CHANNELS
            routing_text = (
                "Each Surge XT window listens on its own dedicated MIDI port.\n"
                "In each window: Settings → MIDI Settings → MIDI Input =\n"
            )
            for inst, (ch, port, _patch) in OSC_CHANNELS.items():
                routing_text += f"     • window {ch} ({inst}):  {port!r}\n"
            routing_text += (
                "\nSurge XT remembers the choice across launches, so it's a "
                "one-time per-window setup. Drums (channel 10) stay on FluidSynth."
            )
            ttk.Label(
                transport, text=routing_text,
                wraplength=420, justify="left", foreground="#222",
                font=("TkFixedFont", 10),
            ).pack(padx=10, pady=(2, 8), anchor="w")

        _refresh_nowplaying()

    # ------------------------------------------------------------------
    # Generators tab — per-gen knob sliders. Only built when a Player
    # is wired in. Rebuilds itself every time the loaded song changes
    # (different songs have different gens with different (type, style)
    # pairs needing different sliders).
    # ------------------------------------------------------------------
    if player is not None:
        from slackbeatz.player import KNOB_CHOICES, KNOB_SPECS

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

            # Meter dropdown options — "auto" clears the override
            # (gen inherits the part's meter, which is the default).
            # Anything else forces this voice to its own cycle, so
            # e.g. drums can stay 4/4 while bass goes 5/4 within
            # the same part.
            GEN_METER_CHOICES = (
                "auto", "4/4", "3/4", "6/8", "5/4", "7/8",
                "2/4", "9/8", "12/8",
            )

            for handle, gen in resolved.gens.items():
                # Gen header row — label on the left, meter dropdown
                # on the right.
                row = ttk.Frame(gens_inner, borderwidth=1, relief="solid")
                row.pack(fill="x", padx=4, pady=4)
                header = ttk.Frame(row)
                header.pack(fill="x", padx=4, pady=(2, 0))
                ttk.Label(
                    header,
                    text=f"{handle}  ({gen.type_} / {gen.style})",
                    font=("TkDefaultFont", 10, "bold"),
                ).pack(side="left")

                # Meter combo — show the current override if one is
                # set, else show the gen's composed meter as a hint,
                # else "auto" (= inherit part meter). Selecting
                # "auto" clears the override.
                override_meter = player.gen_meter_overrides.get(handle)
                if override_meter is not None:
                    current_meter = override_meter
                elif gen.meter is not None:
                    current_meter = str(gen.meter)
                else:
                    current_meter = "auto"
                meter_var = _var(tk.StringVar, value=current_meter)
                meter_cb = ttk.Combobox(
                    header, values=GEN_METER_CHOICES,
                    textvariable=meter_var,
                    state="readonly", width=6,
                )
                meter_cb.pack(side="right", padx=(4, 4))
                ttk.Label(
                    header, text="meter", foreground="#666",
                ).pack(side="right")

                def _on_gen_meter(_event, h=handle, v=meter_var):
                    choice = v.get()
                    if choice == "auto":
                        player.set_gen_meter(h, None)
                    else:
                        player.set_gen_meter(h, choice)

                meter_cb.bind("<<ComboboxSelected>>", _on_gen_meter)
                _persistent.append(_on_gen_meter)

                specs = KNOB_SPECS.get(gen.type_, [])
                if not specs:
                    ttk.Label(row, text="(no tweakable knobs)", foreground="#888").pack(
                        anchor="w", padx=8, pady=2,
                    )
                    continue
                gen_choices = KNOB_CHOICES.get(gen.type_, {})
                for knob_name, lo, hi, default, kind in specs:
                    knob_row = ttk.Frame(row)
                    knob_row.pack(fill="x", padx=8, pady=1)
                    ttk.Label(knob_row, text=knob_name, width=14, anchor="w").pack(side="left")
                    if kind == "enum":
                        # Combobox: "(default)" clears the override and
                        # lets the gen's style supply its default. Any
                        # other value is stored verbatim as a string.
                        choices = gen_choices.get(knob_name, [])
                        values = ["(default)"] + list(choices)
                        if handle in overrides and knob_name in overrides[handle]:
                            current = str(overrides[handle][knob_name])
                        elif knob_name in gen.knobs:
                            current = str(gen.knobs[knob_name])
                        else:
                            current = "(default)"
                        evar = _var(tk.StringVar, value=current)
                        cb = ttk.Combobox(
                            knob_row, values=values, textvariable=evar,
                            state="readonly", width=20,
                        )
                        cb.pack(side="left", padx=(0, 4))

                        def _on_enum(_event, h=handle, n=knob_name, v=evar):
                            choice = v.get()
                            if choice == "(default)":
                                player.unset_knob(h, n)
                            else:
                                player.set_knob(h, n, choice)

                        cb.bind("<<ComboboxSelected>>", _on_enum)

                        def _reset_enum(h=handle, n=knob_name, v=evar):
                            player.unset_knob(h, n)
                            v.set("(default)")
                        _pin(ttk.Button(
                            knob_row, text="↺", width=2,
                            command=_reset_enum,
                        )).pack(side="left", padx=2)
                        _persistent.append(_on_enum)
                        _persistent.append(_reset_enum)
                        continue

                    # Resolve initial value: override > gen.knobs > default.
                    if handle in overrides and knob_name in overrides[handle]:
                        value = overrides[handle][knob_name]
                    elif knob_name in gen.knobs:
                        value = gen.knobs[knob_name]
                    else:
                        value = default
                    is_int = kind == "int"
                    if is_int:
                        var = _var(tk.IntVar, value=int(value))
                        resolution = 1
                    else:
                        var = _var(tk.DoubleVar, value=float(value))
                        resolution = (hi - lo) / 100 if hi > lo else 0.01

                    # Debounce — Tk's Scale command fires on every pixel
                    # of drag, which would thrash the recompose loop.
                    # We schedule the actual override apply 100ms after
                    # the last drag event.
                    pending = {"after_id": None}

                    def _commit(v, h=handle, n=knob_name, ii=is_int):
                        try:
                            cast = int(float(v)) if ii else float(v)
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

                    scale = _pin(tk.Scale(
                        knob_row, from_=lo, to=hi, resolution=resolution,
                        orient="horizontal", variable=var,
                        showvalue=True, length=180,
                        command=_on_drag,
                    ))
                    scale.pack(side="left", fill="x", expand=True)

                    def _reset_knob(h=handle, n=knob_name, v=var, d=default):
                        player.unset_knob(h, n)
                        v.set(d)
                    _pin(ttk.Button(
                        knob_row, text="↺", width=2,
                        command=_reset_knob,
                    )).pack(side="left", padx=2)
                    _persistent.append(_commit)
                    _persistent.append(_on_drag)
                    _persistent.append(_reset_knob)
                    _persistent.append(pending)

        # Initial paint + register with the poll loop so the tab
        # rebuilds whenever the song changes. The layout-key short-
        # circuit inside _rebuild_gens_tab means this is a no-op
        # cost for the common "user just nudged a knob/tempo/seed"
        # case (same gens → no destroy + recreate).
        _rebuild_gens_tab()
        main_thread_callbacks.append(_rebuild_gens_tab)

    # ------------------------------------------------------------------
    # Per-style Surge patch swap — when the song's style changes, each
    # spawned Surge instance reloads to its (role, style) factory
    # patch (see :data:`surge_host._STYLE_PATCH_FOR_ROLE`). Mirrors
    # how the FluidSynth path's _GM_PROGRAM_DEFAULTS picks a
    # style-appropriate GM program per channel; the Surge side was
    # using one fixed patch per role regardless of style before this.
    # No-op when --surge isn't on (surge_instances is empty).
    # ------------------------------------------------------------------
    if player is not None and surge_instances:
        from slackbeatz.surge_host import apply_song_patches

        def _refresh_surge_patches() -> None:
            try:
                apply_song_patches(surge_instances, player.current_resolved)
            except Exception:
                pass

        _refresh_surge_patches()
        main_thread_callbacks.append(_refresh_surge_patches)

    # ------------------------------------------------------------------
    # Sound tab — per-Surge-XT knobs (only when --surge spawned the
    # headless quartet). Drives surge-xt-cli over OSC for live tweaking.
    # The sampler-backed voice + fx sub-tabs render alongside if a
    # sampler is running (it always does when --surge is on).
    # ------------------------------------------------------------------
    from slackbeatz.sampler import get_active_sampler
    _sampler = get_active_sampler()
    if surge_instances or _sampler is not None:
        sound_tab = ttk.Frame(notebook)
        notebook.add(sound_tab, text="🎚 Sound")
        _build_sound_tab(sound_tab, surge_instances or [], ttk, tk,
                         sampler=_sampler, _var=_var, player=player)

    # ------------------------------------------------------------------
    # 🎛 Mixer tab — per-channel volume + per-channel FX. Replaces the
    # old standalone Effects tab; FluidSynth's reverb/chorus controls
    # now live on the drums strip inside this tab.
    # ------------------------------------------------------------------
    mixer_tab = ttk.Frame(notebook)
    notebook.add(mixer_tab, text="🎛 Mixer")
    _build_mixer_tab(
        mixer_tab,
        surge_instances=surge_instances or [],
        sampler=_sampler,
        # Pass send=None in bare-MIDI mode (no FluidSynth running)
        # so _build_mixer_tab can detect the absence + hide the
        # drums strip rather than rendering controls that no-op.
        send=send if fs_stdin is not None else None,
        initial_gain=initial_gain,
        initial_reverb_room=initial_reverb_room,
        _var=_var,
        ttk=ttk,
        tk=tk,
        # Pass player through so the strip-title activity-flash poll
        # can read Player.is_channel_active.
        player=player,
    )

    # ------------------------------------------------------------------
    # Instruments tab — adaptive per-channel patch / program picker.
    #
    #   * Surge-backed channels → "(patch on 🎚 Sound tab)" hint;
    #     the actual patch picker + FX chain + engine knobs live on
    #     the Sound tab's per-voice sub-tab.
    #   * Sampler-backed channels (voice ch 5, fx ch 11, when --surge
    #     is on) → "(bank on 🎚 Sound tab)" hint.
    #   * Drum channel (10) when FluidSynth is running → drum-kit
    #     bank picker (bank 128 preset).
    #   * Other channels when FluidSynth is running → 128-name GM
    #     program dropdown.
    #   * Bare-MIDI mode (no FluidSynth, no Surge on this channel) →
    #     static "(MIDI out — external)" label. The user's downstream
    #     DAW / HW synth picks its own patch.
    #
    # Mute / solo checkboxes always render when a Player is wired in,
    # regardless of backend. GM dropdowns re-sync on player state
    # change (e.g. when a new phrase loads a different song) via
    # _refresh_instruments.
    # ------------------------------------------------------------------
    instruments = ttk.Frame(notebook)
    notebook.add(instruments, text="Instruments")

    # Channel index → live SurgeInstance for that channel. The
    # Instruments tab itself doesn't render a Surge dropdown anymore
    # (that lives on the Sound tab) but it does need to know which
    # channels are Surge-backed so it can show a "(patch on Sound)"
    # hint instead of an empty row.
    surge_by_channel: dict[int, object] = {
        getattr(inst, "config").channel_1idx: inst
        for inst in (surge_instances or [])
    }
    # 1-indexed channels backed by the in-process Sampler when it's
    # running. Lifted from OSC_CHANNELS so the table stays in sync if
    # the routing layout changes.
    sampler_channels: set[int] = set()
    if _sampler is not None:
        from slackbeatz.synthhost import OSC_CHANNELS
        sampler_channels = {
            OSC_CHANNELS["voice"][0],
            OSC_CHANNELS["fx"][0],
        }

    # Banner text adapts to the active backend mix so the user knows
    # which dropdowns are wired live.
    banner_lines: list[str] = []
    if surge_by_channel:
        banner_lines.append(
            f"Surge-backed channels ({', '.join(str(c) for c in sorted(surge_by_channel))}) "
            "— patch + FX + engine knobs live on the 🎚 Sound tab.",
        )
    if fs_stdin is not None:
        banner_lines.append(
            "FluidSynth-backed channels pick a GM program (ch 10 = drum kit).",
        )
    if sampler_channels:
        banner_lines.append(
            "Sampler-backed channels (voice / fx) — bank + FX on the "
            "🎚 Sound tab.",
        )
    if not banner_lines:
        banner_lines.append(
            "Bare-MIDI mode — patches are controlled by your downstream "
            "DAW / HW synth.",
        )
    ttk.Label(
        instruments,
        text="\n".join(banner_lines),
        wraplength=520, justify="left", foreground="#444",
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
    gm_display_choices = [f"{i:>3}  {name}" for i, name in enumerate(_GM_PROGRAMS)]

    # Per-channel dropdown handles + their kind, so the state-change
    # callback can re-sync them on song load without re-rendering the
    # whole tab.
    cb_by_channel: dict[int, "ttk.Combobox"] = {}
    cb_kind_by_channel: dict[int, str] = {}

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
            mute_var = _var(tk.IntVar, value=1 if ch in player._user_mutes else 0)

            def _on_mute(channel=ch, var=mute_var):
                if var.get():
                    player.mute(channel)
                else:
                    player.unmute(channel)
            ttk.Checkbutton(
                row, text="mute", variable=mute_var, command=_on_mute,
            ).pack(side="left", padx=(0, 4))

            solo_var = _var(tk.IntVar, value=1 if ch in player._solo else 0)

            def _on_solo(channel=ch, var=solo_var):
                if var.get():
                    player.solo(channel)
                else:
                    player.unsolo_channel(channel)
            ttk.Checkbutton(
                row, text="solo", variable=solo_var, command=_on_solo,
            ).pack(side="left", padx=(0, 6))

        # Patch / program picker. Pick the surface based on backend.
        if ch in surge_by_channel:
            # Patch selection lives on the 🎚 Sound tab now (per-voice
            # sub-tab with patch dropdown + FX chain + engine knobs).
            # This row just shows mute/solo + a pointer hint.
            ttk.Label(
                row, text="(patch on 🎚 Sound tab)",
                foreground="#888", font=("TkDefaultFont", 10, "italic"),
            ).pack(side="left", padx=4)
            continue

        if ch in sampler_channels:
            ttk.Label(
                row, text="(bank on 🎚 Sound tab)",
                foreground="#888", font=("TkDefaultFont", 10, "italic"),
            ).pack(side="left", padx=4)
            continue

        if fs_stdin is None:
            # Bare-MIDI mode: no in-process synth on this channel.
            ttk.Label(
                row, text="(MIDI out — external)",
                foreground="#888", font=("TkDefaultFont", 10, "italic"),
            ).pack(side="left", padx=4)
            continue

        # FluidSynth-backed channel.
        if ch == 10:
            initial_kit = initial_programs.get(10, 0)
            current_label = drum_kit_label_by_idx.get(initial_kit, drum_kit_choices[0])
            cb = ttk.Combobox(
                row, values=drum_kit_choices, state="readonly", width=28,
            )
            cb.set(current_label)

            def _drum_select(_event, combo=cb):
                idx = drum_idx_by_label.get(combo.get(), 0)
                # select <chan-0idx> <sfont_id> <bank> <preset>
                # sfont_id 1 is the first/only SF FluidSynth loaded.
                send(f"select 9 1 128 {idx}")
            cb.bind("<<ComboboxSelected>>", _drum_select)
            cb.pack(side="left", fill="x", expand=True)
            cb_by_channel[ch] = cb
            cb_kind_by_channel[ch] = "drum"
        else:
            initial_prog = initial_programs.get(ch, 0)
            initial_prog = max(0, min(127, initial_prog))
            cb = ttk.Combobox(
                row, values=gm_display_choices, state="readonly", width=28,
            )
            cb.set(gm_display_choices[initial_prog])

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
            cb_by_channel[ch] = cb
            cb_kind_by_channel[ch] = "gm"

    # State-change refresh — re-sync each dropdown's selection to
    # what's currently live on its backend. Fires on player state
    # change (e.g. REPL loads a new song with different gens). Only
    # FluidSynth-backed dropdowns are tracked here; Surge patch
    # selection lives on the Sound tab and the Surge sub-tab handles
    # its own state.
    def _refresh_instruments() -> None:
        from slackbeatz.engine.midifile import program_map as _program_map_now
        live_programs: dict[int, int] = {}
        if player is not None and player.current_resolved is not None:
            try:
                live_programs = _program_map_now(player.current_resolved)
            except Exception:
                live_programs = {}
        for ch, cb in cb_by_channel.items():
            kind = cb_kind_by_channel.get(ch)
            if kind == "drum":
                idx = live_programs.get(10, drum_idx_by_label.get(cb.get(), 0))
                new_label = drum_kit_label_by_idx.get(idx)
                if new_label and cb.get() != new_label:
                    cb.set(new_label)
            elif kind == "gm":
                idx = live_programs.get(ch)
                if idx is None:
                    continue
                idx = max(0, min(127, idx))
                new_label = gm_display_choices[idx]
                if cb.get() != new_label:
                    cb.set(new_label)

    if player is not None:
        main_thread_callbacks.append(_refresh_instruments)

    # ------------------------------------------------------------------
    # 🔌 I/O tab — read-only display of the MIDI port + soundfont the
    # session was launched with, plus a MIDI Clock toggle mirror of
    # the Transport tab. Switching port/soundfont at runtime would
    # require tearing down the RealtimeSink + FluidSynth process —
    # we surface the values + a restart hint instead.
    # ------------------------------------------------------------------
    if player is not None:
        io_tab = ttk.Frame(notebook)
        notebook.add(io_tab, text="🔌 I/O")
        _build_io_tab(io_tab, player=player, _var=_var, ttk=ttk, tk=tk)

    # ------------------------------------------------------------------
    # 🎛 Setup tab — read-only view of the currently-loaded Setup's
    # inst + kit declarations. Surfaces the channel + (for inst) fixed
    # note + (for kit) per-drum note map. Editing comes in a follow-up
    # — Phase 3a per the GUI parity epic.
    # ------------------------------------------------------------------
    if player is not None:
        setup_tab = ttk.Frame(notebook)
        notebook.add(setup_tab, text="🎛 Setup")
        setup_refresh = _build_setup_tab(
            setup_tab, player=player, _var=_var, ttk=ttk, tk=tk,
        )
        main_thread_callbacks.append(setup_refresh)

    if on_close is not None:
        root.protocol("WM_DELETE_WINDOW", lambda: (on_close(), root.destroy()))

    # Kick off the main-thread state-poll loop. Must be scheduled
    # from the main thread (we are it here, just before mainloop) so
    # the after-id lives in the correct notifier.
    if player is not None:
        root.after(80, _poll_state)

    root.mainloop()
