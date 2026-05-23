"""Tiny Tk control window for ``slackbeatz live`` and ``slackbeatz repl``.

FluidSynth itself is headless, so we open a small native window that
sends ``gain N`` / ``set synth.reverb.* N`` / ``set synth.chorus.* N``
commands to its stdin shell every time a slider moves. No external
dependency — Tk ships with CPython on macOS, Linux, and Windows.

The shell-command names below match what FluidSynth 2.x's interactive
shell accepts (verified against ``help general`` and the documented
``set`` syntax for runtime settings).

Architecture:

* Tk needs to run on the main thread. The caller runs the scheduler
  in a background thread (``daemon=True``) and calls ``run_tweak_gui``
  on the main thread.
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


def run_tweak_gui(
    fs_stdin: IO[bytes],
    *,
    initial_gain: float | None = None,
    initial_reverb_room: float | None = None,
    on_close: Callable[[], None] | None = None,
) -> None:
    """Open the tweak window. Blocks until the user closes it.

    Parameters
    ----------
    fs_stdin:
        FluidSynth's stdin pipe (from ``subprocess.Popen(..., stdin=PIPE)``).
        Slider movements write shell commands to this file handle.
    initial_gain, initial_reverb_room:
        Override the slider defaults to match values the user passed via
        ``--gain`` / ``--reverb`` on the CLI.
    on_close:
        Called when the user closes the window (clicks the close button
        or hits Cmd-W). The caller typically uses this to terminate the
        FluidSynth subprocess.
    """
    try:
        import tkinter as tk
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
    root.minsize(380, 280)

    # Override a couple of slider defaults from the CLI flags so the
    # window reflects what's actually playing.
    overrides: dict[str, float] = {}
    if initial_gain is not None:
        overrides["Master gain"] = initial_gain
    if initial_reverb_room is not None:
        overrides["Reverb room size"] = initial_reverb_room

    for label, cmd_tmpl, low, high, default in _SLIDERS:
        value = overrides.get(label, default)
        frame = tk.Frame(root)
        frame.pack(fill="x", padx=10, pady=2)
        tk.Label(frame, text=label, width=18, anchor="w").pack(side="left")
        var = tk.DoubleVar(value=value)
        # resolution kept fine so the slider feels smooth.
        resolution = (high - low) / 200 if (high - low) > 0 else 0.01
        scale = tk.Scale(
            frame, from_=low, to=high,
            resolution=resolution,
            orient="horizontal", variable=var,
            showvalue=True, length=240,
            command=lambda v, c=cmd_tmpl: send(c.format(v=float(v))),
        )
        scale.pack(side="left", fill="x", expand=True)

    # Reverb / chorus on-off toggles.
    toggles = tk.Frame(root); toggles.pack(fill="x", padx=10, pady=(8, 4))
    rev_var = tk.IntVar(value=1)
    cho_var = tk.IntVar(value=1)
    tk.Checkbutton(
        toggles, text="Reverb on", variable=rev_var,
        command=lambda: send(f"set synth.reverb.active {rev_var.get()}"),
    ).pack(side="left", padx=6)
    tk.Checkbutton(
        toggles, text="Chorus on", variable=cho_var,
        command=lambda: send(f"set synth.chorus.active {cho_var.get()}"),
    ).pack(side="left", padx=6)

    # Hint label.
    tk.Label(
        root,
        text="Move a slider to tweak the synth live. Close window or hit "
             "Ctrl+C in the terminal to stop.",
        wraplength=360, justify="center", fg="#666",
    ).pack(padx=10, pady=(4, 8))

    if on_close is not None:
        root.protocol("WM_DELETE_WINDOW", lambda: (on_close(), root.destroy()))

    root.mainloop()
