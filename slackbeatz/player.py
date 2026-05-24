"""Live transport for ``slackbeatz repl`` (and ``live --gui``).

``Player`` owns the currently-playing song and the worker thread that
streams its MIDI events. The same instance is shared by:

* the REPL's ``input()`` loop — which feeds it phrases and slash
  commands (``/play``, ``/stop``, ``/tempo N``, ``/style X``, ...);
* the Tk control window — whose widgets call ``player.set_tempo(120)``,
  ``player.set_style("acid")``, ``player.toggle_play()``, etc.

Parameter changes (``set_tempo``, ``set_style``, ``set_seed_offset``)
re-compose the current phrase with the new value and restart playback
from bar 0. This is the simplest correct model — slackbeatz songs are
fully reproducible from ``(phrase, seed_offset, style, tempo)``, so a
restart is the natural way to apply a new value. Future work: seek to
the current bar after a parameter change instead of always restarting
at 0.
"""

from __future__ import annotations

import random
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from slackbeatz.compose import compose_from_text
from slackbeatz.dsl.parser import ParseError, parse_file
from slackbeatz.engine.clock_source import InternalClock
from slackbeatz.engine.scheduler import Scheduler, build_tempo_map
from slackbeatz.setup.loader import SetupError, load_setup
from slackbeatz.setup.resolve import ResolveError, resolve_song
from slackbeatz.sinks.realtime import RealtimeSink


# Valid style names — used to validate /style X commands + populate the
# GUI dropdown. Sourced from defaults.STYLE_BASE_VEL keys; if a new
# style is added there this list updates automatically.
def _known_styles() -> list[str]:
    from slackbeatz.generators.defaults import STYLE_BASE_VEL
    seen: list[str] = []
    for (_type, style) in STYLE_BASE_VEL.keys():
        if style not in seen:
            seen.append(style)
    return seen


KNOWN_STYLES = _known_styles()


# Per-type list of knobs the live tweaker exposes. Each knob spec is
# ``(name, low, high, default, kind)`` — kind is "int", "float", or
# "enum". For numeric kinds, ``default`` is the scalar default; for
# enum, ``default`` is the sentinel "(default)" and the legal choices
# live in :data:`KNOB_CHOICES`. The GUI renders sliders for int/float
# and a Combobox for enum. Knobs not listed for a type can still be
# set via /knob; the GUI just won't show them.
KNOB_SPECS: dict[str, list[tuple[str, float, float, object, str]]] = {
    "rhythm": [
        ("humanize",      0,    10,    2,    "int"),
        ("accent",        0,    16,    0,    "int"),
        ("drop_prob",     0.0,  0.5,   0.0,  "float"),
        ("intensity",     0.0,  1.5,   1.0,  "float"),
        ("swing",         0.0,  0.3,   0.0,  "float"),
        ("evolution",     0.0,  1.0,   0.0,  "float"),
        # Round 9 — groove / phrase / fill / variation.
        ("ghost",         0.0,  0.6,   0.0,  "float"),
        ("ghost_vel",     0.1,  0.8,   0.25, "float"),
        ("hat_variant",   0.0,  0.5,   0.0,  "float"),
        ("fill_every",    0,    16,    4,    "int"),
        ("phrase_lift",   0,    16,    0,    "int"),
        ("mistakes",      0.0,  0.1,   0.0,  "float"),
        ("stutter",       0.0,  1.0,   0.0,  "float"),
        ("polyrhythm",    0,    16,    0,    "int"),
        ("groove",        0,    0,     "(default)", "enum"),
        ("fill_style",    0,    0,     "(default)", "enum"),
    ],
    "bass": [
        ("intensity",     0.0,  1.5,   1.0,  "float"),
        ("gate",          0.1,  1.0,   0.85, "float"),
        ("gate_jitter",   0.0,  0.5,   0.0,  "float"),
        ("octave_jump",   0.0,  0.5,   0.0,  "float"),
        ("mute_prob",     0.0,  0.5,   0.0,  "float"),
        ("burble_prob",   0.0,  0.3,   0.0,  "float"),
        ("evolution",     0.0,  1.0,   0.0,  "float"),
        # Round 8 — chord-following + walking-bass variety.
        ("fifth_prob",    0.0,  1.0,   0.0,  "float"),
        ("third_prob",    0.0,  0.5,   0.0,  "float"),
        ("walking",       0.0,  1.0,   0.0,  "float"),
        ("pickup",        0.0,  1.0,   0.0,  "float"),
        ("bars_per_chord", 1,   32,    4,    "int"),
        ("progression",   0,    0,     "(default)", "enum"),
    ],
    "melody": [
        ("intensity",     0.0,  1.5,   1.0,  "float"),
        ("gate",          0.1,  1.0,   0.6,  "float"),
        ("passing_tones", 0.0,  0.4,   0.0,  "float"),
        ("motif_memory",  0,    8,     0,    "int"),
        ("mute_prob",     0.0,  0.5,   0.0,  "float"),
        ("evolution",     0.0,  1.0,   0.0,  "float"),
    ],
    "chords": [
        ("intensity",     0.0,  1.5,   1.0,  "float"),
        ("gate",          0.1,  1.0,   0.95, "float"),
        ("mute_prob",     0.0,  0.5,   0.0,  "float"),
        ("arp_prob",      0.0,  0.5,   0.0,  "float"),
        ("evolution",     0.0,  1.0,   0.0,  "float"),
        ("bars_per_chord", 1,    32,    4,    "int"),
        ("inversion",     0,    3,     0,    "int"),
        ("tension_dyn",   0.0,  1.0,   0.0,  "float"),
        ("drop_intensity", 0.0,  1.0,   0.0,  "float"),
        ("phrase_lift",   0,    16,    0,    "int"),
        ("voice_lead",    0,    1,     0,    "int"),
        ("progression",   0,    0,     "(default)", "enum"),
        ("voicing",       0,    0,     "(default)", "enum"),
    ],
    "candy": [
        ("intensity",     0.0,  1.5,   1.0,  "float"),
        ("density",       0.0,  1.0,   0.5,  "float"),
    ],
    "speech": [
        ("note_base",       0,    127,   60,   "int"),
        ("phrase_interval", 1,    32,    8,    "int"),
        ("velocity",        1,    127,   80,   "int"),
    ],
    "sample": [
        ("note_base", 0,   127,  36,    "int"),
        ("velocity",  1,   127,  90,    "int"),
        ("pulses",    0,   32,   4,     "int"),
        ("steps",     1,   32,   16,    "int"),
        ("pattern",   0,   0,    "(default)", "enum"),
    ],
}


# String-valued knobs — picked from a closed set of options instead
# of a numeric range. Per gen type so /knob lists only what's
# meaningful for that gen. Currently only ``chords`` has these
# (progression name + voicing name); other types accept the knob
# but the value just has to be a recognised string.
KNOB_CHOICES: dict[str, dict[str, list[str]]] = {
    "chords": {
        "progression": [
            "i-VI-ii-IV", "i-iv", "i-v", "i-VII-VI-V",
            "ii-V-I", "I-V-vi-IV", "12-bar", "andalusian",
        ],
        "voicing": [
            "triad", "seventh", "ninth", "sus2", "sus4",
            "shell", "power", "open",
        ],
    },
    "bass": {
        "progression": [
            "i-VI-ii-IV", "i-iv", "i-v", "i-VII-VI-V",
            "ii-V-I", "I-V-vi-IV", "12-bar", "andalusian",
        ],
    },
    "rhythm": {
        "groove": [
            "linear", "shuffle", "dilla", "trap16", "behind", "rush",
        ],
        "fill_style": [
            "(off)", "snare_roll", "tom_roll", "kick_double", "silence",
        ],
    },
    "sample": {
        "pattern": ["euclid", "every_bar"],
    },
}


# Extend the string-knobs set in :meth:`Player.set_knob` so the new
# enum entries above are stored verbatim rather than coerced via
# :func:`knob_kind`. ``pattern`` joins the existing
# ``progression`` / ``voicing`` / ``groove`` / ``fill_style`` group.
_STRING_KNOBS_EXTRA = ("pattern",)


def knob_kind(knob_name: str) -> str:
    """Look up whether *knob_name* is conventionally int or float.
    Used by /knob REPL parsing so '5' gets stored as int(5) for
    humanize but float(0.5) for drop_prob."""
    for specs in KNOB_SPECS.values():
        for name, _lo, _hi, _def, kind in specs:
            if name == knob_name:
                return kind
    return "float"  # safe default


def _rewrite_song_tempo(sb_src: str, new_tempo: int) -> str:
    """Replace the song block's ``tempo N`` line with *new_tempo*.

    The DSL only accepts ``tempo`` at indent-level-1 inside a ``song``
    block (it's part of the song-attribute section before ``gen`` /
    ``part`` lines). We do a simple line-walk: enter "song mode" on
    the ``song "..."`` opener, replace the first ``tempo`` line we see
    inside, exit on the next un-indented non-blank line. If no tempo
    line is found, append one to the song block.
    """
    lines = sb_src.splitlines(keepends=True)
    out: list[str] = []
    in_song = False
    song_indent: int | None = None
    replaced = False
    song_block_end_idx: int | None = None
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        leading = len(line) - len(stripped)
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        if not in_song and stripped.startswith("song"):
            in_song = True
            song_indent = leading
            out.append(line)
            continue
        if in_song and not replaced and stripped.startswith("tempo"):
            assert song_indent is not None
            indent_str = " " * (song_indent + 2)
            out.append(f"{indent_str}tempo {new_tempo}\n")
            replaced = True
            continue
        # End of song-attribute block: first line at the song indent.
        if in_song and leading <= (song_indent or 0):
            if not replaced and song_block_end_idx is None:
                song_block_end_idx = len(out)
            in_song = False
        out.append(line)
    # If we exited the song block without finding a tempo line, inject one.
    if in_song and not replaced and song_indent is not None:
        indent_str = " " * (song_indent + 2)
        out.append(f"{indent_str}tempo {new_tempo}\n")
    elif not replaced and song_block_end_idx is not None and song_indent is not None:
        indent_str = " " * (song_indent + 2)
        out.insert(song_block_end_idx, f"{indent_str}tempo {new_tempo}\n")
    return "".join(out)


class Player:
    """Thread-safe holder for the currently-loaded song + playback thread.

    Parameters
    ----------
    port_name:
        MIDI output port name (e.g. the FluidSynth port slackbeatz
        spawned). Each playback opens its own :class:`RealtimeSink`
        on this port so all-notes-off cleans up between sessions.
    setup_arg:
        Value of the ``--setup`` CLI flag (or ``None``). Used when
        loading bundled / inline setups for composed songs.
    on_state_change:
        Optional callback fired whenever transport state changes
        (play/stop, parameter overrides). The GUI uses this to refresh
        the "now playing" label.
    """

    def __init__(
        self,
        *,
        port_name: str,
        setup_arg: Optional[str] = None,
        on_state_change: Optional[Callable[[], None]] = None,
        osc_routing: bool = False,
    ) -> None:
        self.port_name = port_name
        self.setup_arg = setup_arg
        self.on_state_change = on_state_change or (lambda: None)
        # When True, every playback opens a CompositeSink that splits
        # the pitched channels onto dedicated virtual MIDI ports (one
        # per ``OSC_CHANNELS`` entry) — so each headless synth instance
        # (and the sampler) listens on its own port. Drums + anything
        # else stay on ``port_name`` (FluidSynth). The "osc_" prefix is
        # a hint that the dedicated-port model exists for OSC-driven
        # synths today, not because it's coupled to OSC itself.
        self.osc_routing = osc_routing
        # Lazily-created shared MultiPortSink — we create it once and
        # reuse it across playback runs so the virtual ports survive
        # song restarts (otherwise the listening synth would lose its
        # MIDI subscription each time the user tweaks a param).
        self._shared_routing_sink = None

        # True while _play_loop is dispatching events; False when
        # the worker thread enters its `finally` cleanup. See the
        # is_playing property for the rationale (avoids the
        # is_alive() flicker around thread teardown + makes natural
        # finish observable to the GUI).
        self._is_playing: bool = False

        # Per-channel activity tracking — both updated from the
        # audio-thread sink wrapper (:class:`_ActivityTapSink`);
        # read lock-free by the GUI to drive per-channel flash
        # indicators. Both keyed by 1-indexed channel.
        #
        # `last_note_on_time` catches short notes — note_on +
        # note_off within one poll cycle (~80 ms) leaves no held
        # note for is_channel_active to find, but the 150 ms
        # timestamp grace window covers it.
        #
        # `held_notes` catches long pads — a single note_on followed
        # by a note_off seconds later. Without this we'd flash the
        # indicator at note_on then drop it after 150 ms even though
        # the sustained pad is still audibly playing.
        #
        # Dict + set writes are atomic in CPython; the worst case
        # is one stale read for one poll cycle. Acceptable.
        self.last_note_on_time: dict[int, float] = {}
        self.held_notes: dict[int, set[int]] = {}

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Re-entrancy guard for set_* operations triggered from the GUI
        # thread while a playback thread is mid-stop.
        self._lock = threading.RLock()

        # Channel state. The scheduler reads ``muted_channels`` by
        # reference on every event — we never reassign it, only
        # mutate in-place, so the worker thread always sees the
        # current set without re-passing it.
        #
        # Effective mutes are computed from two underlying sources:
        # ``_user_mutes`` (channels the user explicitly muted) and
        # ``_solo`` (channels the user solo'd). When ``_solo`` is
        # non-empty, only solo'd channels are audible (DAW-style
        # solo); otherwise the user-mute set takes effect.
        self.muted_channels: set[int] = set()
        self._user_mutes: set[int] = set()
        self._solo: set[int] = set()
        # Reference to the currently-running Scheduler. Used by the
        # transport to read its ``current_tick`` for seek-preserving
        # parameter changes.
        self._current_scheduler = None
        # Whether parameter changes (tempo/style/seed) restart from
        # the current bar (True) or from tick 0 (False). Default True
        # for the "live tweaking" feel — toggleable from the GUI / CLI.
        self.preserve_position: bool = True

        # Per-gen knob overrides. Layered on top of the gens' baked-in
        # knobs each time _resolve_current is called, so they survive
        # re-composition (style / seed / tempo changes). Schema:
        # {gen_handle: {knob_name: value}}.
        self._knob_overrides: dict[str, dict[str, object]] = {}

        # Cached most-recently-resolved song — saved on every
        # _resolve_current so the GUI doesn't have to re-resolve just
        # to read the gen layout. Re-resolves are expensive (compose +
        # parse + resolve = 5-50ms each); doing one per state change
        # plus one per GUI refresh kept the Tk thread saturated and
        # produced beachballs during slider drags.
        self.current_resolved = None
        # Cached most-recently-loaded :class:`Setup` — surfaced by the
        # 🎛 Setup tab for inst / kit visibility. Populated alongside
        # ``current_resolved`` on each _resolve_current.
        self.current_setup = None

        # MIDI Clock output. When True, the playback worker spawns a
        # ClockEmitter sibling thread that broadcasts 0xF8 pulses at
        # 24 PPQN plus Start/Stop/Continue bytes so downstream MIDI
        # gear can lock to slackbeatz's tempo.
        self.emit_clock: bool = False

        # Currently-loaded source. Either a phrase (composed) or a path
        # to a .sb file (live mode). One of these is non-None when a
        # song has been loaded.
        self.current_phrase: Optional[str] = None
        self.current_song_path: Optional[Path] = None
        self.title: Optional[str] = None

        # Composition overrides. None = use the composer's default
        # (sentiment / hash-derived value).
        self.style_override: Optional[str] = None
        self.tempo_override: Optional[int] = None
        self.seed_offset: int = 0

        # Per-gen-type style overrides — the Builder's 🎨 Per-voice
        # style disclosure populates this with entries like
        # ``{"chords": "lofi", "bass": "psytrance"}``. Threaded into
        # :func:`compose_from_text` so each gen line in the rendered
        # .sb gets its type-specific style. The primary
        # :attr:`style_override` still drives the song profile
        # (gen layout, tempo, arrangement); per-type entries just
        # change which per-style algorithm each gen runs.
        self.style_per_type: Optional[dict[str, str]] = None

        # Arrangement-level overrides applied AFTER resolution but
        # BEFORE the scheduler reads it. Set by the GUI's Builder
        # tab; both are empty when no skip / no per-voice meter is in
        # play, in which case _resolve_current is a no-op for these.
        # `skip_parts` strips any matching part name from
        # resolved.arrangement (skips ALL occurrences if a part
        # appears multiple times — fine for v1, the user can save +
        # hand-edit if they need finer control).
        # `gen_meter_overrides` mutates each named gen's meter in
        # place via object.__setattr__ (ResolvedGen is frozen but
        # the field is settable through that escape hatch — same
        # pattern as tempo_override on file-loaded songs). Enables
        # polyrhythm: drums in 4/4 while bass runs in 5/4 within
        # the same part.
        self.skip_parts: set[str] = set()
        self.gen_meter_overrides: dict[str, str] = {}

        # Arrangement override — when non-None, replaces
        # ``resolved.arrangement`` after resolution. Used by the 🎬
        # Arrangement tab so the user can reorder / add / remove
        # parts without editing the .sb. List of part names already
        # expanded (group repeats flattened); applied after
        # ``skip_parts`` so a user-set arrangement wins.
        self.arrangement_override: Optional[list[str]] = None

        # Per-part attribute overrides, set from the Builder's Parts
        # panel — schema {part_name: {attr_name: value}}. Applied in
        # _resolve_current via object.__setattr__ on the frozen
        # ResolvedPart (same escape hatch as tempo_override on
        # file-loaded songs). Recognised attrs: tempo, key, role,
        # tension, transpose_prob, scale_override.
        self._part_overrides: dict[str, dict[str, object]] = {}

        # Loop on song end — re-render the same params and play again.
        self.loop: bool = False

        # Per-part loop. When non-None, ``_play_loop`` confines the
        # scheduler to that arrangement position's tick span and
        # restarts at its start tick on every iteration — the Builder
        # Parts panel's 🔁 toggle sets this. Mutually exclusive with
        # the whole-song loop above (the per-part loop wins). Cleared
        # automatically when the user picks Stop, generates a new
        # song, or toggles the same row's 🔁 off.
        self.loop_position: Optional[int] = None

    # ------------------------------------------------------------------
    # Source loading
    # ------------------------------------------------------------------

    def load_phrase(self, phrase: str) -> None:
        """Set *phrase* as the active source. Does not start playback —
        the caller decides via :meth:`play`."""
        with self._lock:
            self.current_phrase = phrase
            self.current_song_path = None
            self.on_state_change()

    def load_file(self, path: Path) -> None:
        """Set a .sb file as the active source."""
        with self._lock:
            self.current_song_path = Path(path)
            self.current_phrase = None
            self.on_state_change()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    @property
    def is_playing(self) -> bool:
        """True iff the worker thread is actively dispatching events.

        Backed by an explicit ``_is_playing`` flag set inside
        :meth:`_play_loop` rather than ``Thread.is_alive()``, so the
        GUI sees a clean False the instant the scheduler exits — even
        for the brief window between "_play_loop returns" and "Python
        actually reaps the thread" where ``is_alive()`` flickers.

        Crucially: a song that finishes NATURALLY (loop=off + end of
        arrangement) now also flips this to False + fires
        ``on_state_change``, so the Transport tab's "▶ Play / ■ Stop"
        button label updates instead of staying stuck on "■ Stop" —
        which previously made the next click look like Stop but
        actually trigger a Play-from-beginning."""
        return self._is_playing

    def play(self, *, from_tick: int = 0) -> str:
        """Compose/resolve the current source and start the playback
        thread. Stops any in-flight playback first.

        *from_tick* (default 0) resumes playback at a non-zero tick —
        used by parameter changes when :attr:`preserve_position` is on.

        Returns a one-line status string suitable for printing.
        """
        with self._lock:
            if self.current_phrase is None and self.current_song_path is None:
                return "no song loaded — type a phrase first"
            self._stop_locked()
            try:
                resolved = self._resolve_current()
            except (ParseError, ResolveError, SetupError) as e:
                return f"error: {e}"
            self.title = resolved.name
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._play_loop, args=(resolved, from_tick), daemon=True,
            )
            # Mark playing BEFORE start() so the immediate
            # on_state_change below sees the right value. Without
            # this, the GUI's refresh would fire while the worker
            # hasn't yet reached its `self._is_playing = True` line,
            # see is_playing=False, and flash the button back to
            # "▶ Play" until the next poll tick.
            self._is_playing = True
            self._thread.start()
            self.on_state_change()
            extra = ""
            if from_tick > 0:
                bar = self._tick_to_bar_label(resolved, from_tick)
                extra = f" (from bar {bar})"
            return f'playing "{self.title}" ({self._params_summary()}){extra}'

    # ------------------------------------------------------------------
    # Per-channel mute
    # ------------------------------------------------------------------

    def mute(self, channel: int) -> str:
        with self._lock:
            self._user_mutes.add(int(channel))
            self._recompute_mutes()
            self.on_state_change()
            return self._mute_status_line()

    def unmute(self, channel: int) -> str:
        with self._lock:
            self._user_mutes.discard(int(channel))
            self._recompute_mutes()
            self.on_state_change()
            return self._mute_status_line()

    def toggle_mute(self, channel: int) -> str:
        with self._lock:
            if int(channel) in self._user_mutes:
                return self.unmute(channel)
            return self.mute(channel)

    def solo(self, channel: int) -> str:
        """Add *channel* to the solo set. While the solo set is non-
        empty, only solo'd channels are audible (DAW-style: solo'ing a
        second channel ADDS it to what's playing rather than replacing
        the first). Calling :meth:`unsolo` (no arg) or
        :meth:`unsolo_channel` removes channels from the solo set."""
        with self._lock:
            self._solo.add(int(channel))
            self._recompute_mutes()
            self.on_state_change()
            return self._mute_status_line()

    def toggle_solo(self, channel: int) -> str:
        with self._lock:
            if int(channel) in self._solo:
                return self.unsolo_channel(channel)
            return self.solo(channel)

    def unsolo(self) -> str:
        """Clear the entire solo set. User mutes take effect again."""
        with self._lock:
            self._solo.clear()
            self._recompute_mutes()
            self.on_state_change()
            return self._mute_status_line()

    def unsolo_channel(self, channel: int) -> str:
        """Remove *channel* from the solo set without clearing the
        others."""
        with self._lock:
            self._solo.discard(int(channel))
            self._recompute_mutes()
            self.on_state_change()
            return self._mute_status_line()

    def _recompute_mutes(self) -> None:
        """Recalculate ``muted_channels`` from ``_user_mutes`` + ``_solo``.

        Caller holds ``_lock``. Mutates the existing set in-place so
        the scheduler's by-reference read stays current.

        Newly-muted channels get an immediate CC 123 so any held notes
        stop ringing; newly-unmuted channels need no signal (the next
        note_on plays on its own).
        """
        if self._solo:
            new_mutes = {ch for ch in range(1, 17) if ch not in self._solo}
        else:
            new_mutes = set(self._user_mutes)
        # Diff: channels that just became muted need a kill signal.
        newly_muted = new_mutes - self.muted_channels
        self.muted_channels.clear()
        self.muted_channels.update(new_mutes)
        for ch in newly_muted:
            self._silence_channel(ch)

    def _mute_status_line(self) -> str:
        parts: list[str] = []
        if self._solo:
            parts.append(f"solo: {sorted(self._solo)}")
        if self._user_mutes:
            parts.append(f"muted: {sorted(self._user_mutes)}")
        if not parts:
            return "no mutes / solos"
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # Seek
    # ------------------------------------------------------------------

    def seek(self, *, bar: int = 0, beat: float = 0.0) -> str:
        """Jump the playhead to a specific (bar, beat) position.

        *bar* is 1-indexed (bar 1 = start of song). *beat* is a
        fractional beat offset within that bar (e.g. ``beat=0.5`` =
        half-way through the first beat). If the song isn't playing,
        ``play(from_tick=...)`` is called to start it at the target.
        Otherwise the worker thread is stopped + restarted at the new
        position.
        """
        with self._lock:
            if self.current_phrase is None and self.current_song_path is None:
                return "no song loaded"
            try:
                resolved = self._resolve_current()
            except (ParseError, ResolveError, SetupError) as e:
                return f"error: {e}"
            tick = self._bar_beat_to_tick(resolved, bar=bar, beat=beat)
            was_playing = self.is_playing
            self._stop_locked()
            if not was_playing:
                # Still rebuild the song below so play() picks up new params.
                return f"seek queued to bar {bar} beat {beat:.1f} — type /play"
            return self.play(from_tick=tick)

    def set_preserve_position(self, on: bool) -> str:
        with self._lock:
            self.preserve_position = bool(on)
            return (
                f"preserve position {'on' if self.preserve_position else 'off'}"
            )

    # ------------------------------------------------------------------
    # Position-slider API — read current/total tick, seek to a raw tick.
    # The Tk Transport tab polls these to keep its position slider in
    # sync with playback + commits drag-to-seek on mouse release.
    # ------------------------------------------------------------------

    def get_current_tick(self) -> int:
        """Current playback tick, or 0 if no scheduler is running."""
        sch = self._current_scheduler
        if sch is None:
            return 0
        return int(sch.current_tick)

    def get_total_ticks(self) -> int:
        """Length of the currently-loaded song in ticks. Returns 0 if
        no song is loaded (so callers can guard div-by-zero)."""
        resolved = self.current_resolved
        if resolved is None:
            return 0
        try:
            return int(build_tempo_map(resolved).end_tick)
        except Exception:  # noqa: BLE001 — defensive against bad state
            return 0

    def get_total_bars(self) -> int:
        """Length of the currently-loaded song in bars (summed across
        all arrangement instances). Returns 0 if no song is loaded.

        Used by the GUI's position pointer to draw a 'bar N / M' style
        readout. Counts per-arrangement-instance — a 4-bar part played
        twice contributes 8 to the total."""
        resolved = self.current_resolved
        if resolved is None:
            return 0
        try:
            return sum(
                resolved.parts[part_name].bars
                for part_name in resolved.arrangement
            )
        except (KeyError, AttributeError):
            return 0

    def get_position_label(self, tick: int) -> str:
        """Human-readable 'bar N beat M' string for *tick* in the
        currently-loaded song, or '—' if nothing's loaded."""
        resolved = self.current_resolved
        if resolved is None:
            return "—"
        try:
            return self._tick_to_bar_label(resolved, max(0, int(tick)))
        except Exception:  # noqa: BLE001
            return "—"

    def get_part_position_spans(self) -> list[tuple[int, int]]:
        """``[(start_tick, end_tick_exclusive), ...]`` for each
        arrangement position in the current resolved song.

        Used by the Builder's Parts panel to map a row back to its
        absolute tick range — drives both the "currently-playing"
        highlight (``current_playing_position``) and the per-row
        ``▶`` jump button (``jump_to_part_position``). Returns an
        empty list when no song is loaded.
        """
        from slackbeatz.engine.clock import bars_to_ticks
        resolved = self.current_resolved
        if resolved is None:
            return []
        out: list[tuple[int, int]] = []
        cursor = 0
        for part_name in resolved.arrangement:
            part = resolved.parts.get(part_name)
            if part is None:
                continue
            span = bars_to_ticks(part.bars, meter=part.meter)
            out.append((cursor, cursor + span))
            cursor += span
        return out

    def current_playing_position(self) -> Optional[int]:
        """Index in ``current_resolved.arrangement`` containing the
        playhead, or ``None`` if not playing / no song / position
        falls outside any part. Lock-free read — safe to call from
        the Tk poll loop."""
        if not self.is_playing:
            return None
        tick = self.get_current_tick()
        for i, (start, end) in enumerate(self.get_part_position_spans()):
            if start <= tick < end:
                return i
        return None

    def jump_to_part_position(self, index: int) -> str:
        """Start (or restart) playback at the beginning of arrangement
        position *index*. Mirrors what the user gets from clicking a
        ``▶`` button next to a Parts-panel row — always begins
        playback, even when stopped."""
        spans = self.get_part_position_spans()
        if not (0 <= index < len(spans)):
            return f"position {index} out of range (have {len(spans)})"
        start_tick, _ = spans[index]
        with self._lock:
            if self.current_phrase is None and self.current_song_path is None:
                return "no song loaded"
            self._stop_locked()
            return self.play(from_tick=start_tick)

    def set_loop_position(self, index: Optional[int]) -> str:
        """Loop the given arrangement position forever. ``index=None``
        clears the loop (playback continues to advance naturally past
        the current position's end). Starts (or restarts) playback at
        the position's start tick when a non-None index is set —
        matches the Parts panel's 🔁 toggle UX."""
        if index is None:
            with self._lock:
                self.loop_position = None
            self.on_state_change()
            return "part loop off"
        spans = self.get_part_position_spans()
        if not (0 <= index < len(spans)):
            return f"position {index} out of range (have {len(spans)})"
        start_tick, _ = spans[index]
        with self._lock:
            if self.current_phrase is None and self.current_song_path is None:
                return "no song loaded"
            self.loop_position = index
            self._stop_locked()
            return self.play(from_tick=start_tick)

    def seek_to_tick(self, tick: int) -> str:
        """Jump the playhead to absolute *tick*.

        Like :meth:`seek` but takes a raw tick — the natural API for
        the position slider (which works in continuous tick space).
        Stops the current playback worker if running + restarts at the
        target tick. Clamps to [0, total_ticks)."""
        with self._lock:
            if self.current_phrase is None and self.current_song_path is None:
                return "no song loaded"
            tick = max(0, int(tick))
            try:
                resolved = self._resolve_current()
            except (ParseError, ResolveError, SetupError) as e:
                return f"error: {e}"
            total = int(build_tempo_map(resolved).end_tick)
            if total > 0 and tick >= total:
                tick = max(0, total - 1)
            was_playing = self.is_playing
            self._stop_locked()
            if not was_playing:
                return f"seek queued to tick {tick} — type /play"
            return self.play(from_tick=tick)

    # ------------------------------------------------------------------
    # Per-gen knob overrides
    # ------------------------------------------------------------------

    def set_knob(self, handle: str, knob: str, value) -> str:
        """Override a single knob on a single gen.

        Stored persistently — survives re-composition (style / tempo /
        seed changes still apply the override) until either explicit
        :meth:`unset_knob` / :meth:`reset_overrides`, or the user
        loads a new phrase whose gen layout doesn't include *handle*
        (the override silently no-ops on missing gens).
        """
        with self._lock:
            # String-valued knobs (progression, voicing): keep the
            # string verbatim. Defaults helpers will validate against
            # their option list and silently fall back if a typo
            # creeps in, so we don't reject unknown values here.
            string_knobs = {
                "progression", "voicing", "groove", "fill_style", "pattern",
            }
            if knob in string_knobs:
                value = str(value)
            elif isinstance(value, str):
                # Coerce numeric strings to int/float per the knob's
                # conventional kind. Lets /knob kick humanize 5 store
                # an int (slackbeatz tests `isinstance(v, int)` in
                # places).
                kind = knob_kind(knob)
                try:
                    value = int(value) if kind == "int" else float(value)
                except ValueError:
                    return f"error: {value!r} not a number"
            elif knob_kind(knob) == "int":
                value = int(value)
            self._knob_overrides.setdefault(handle, {})[knob] = value
            return self._restart_after_change(
                f"knob {handle}.{knob} → {value}",
            )

    def unset_knob(self, handle: str, knob: str | None = None) -> str:
        """Clear an override. ``knob=None`` clears all overrides on
        *handle*; otherwise just that knob."""
        with self._lock:
            if knob is None:
                removed = self._knob_overrides.pop(handle, None)
                if not removed:
                    return f"no overrides on {handle}"
                return self._restart_after_change(
                    f"cleared {len(removed)} override(s) on {handle}",
                )
            gen_overrides = self._knob_overrides.get(handle, {})
            if knob not in gen_overrides:
                return f"no override for {handle}.{knob}"
            del gen_overrides[knob]
            if not gen_overrides:
                self._knob_overrides.pop(handle, None)
            return self._restart_after_change(
                f"cleared {handle}.{knob} override",
            )

    def get_knob_overrides(self) -> dict[str, dict[str, object]]:
        """Snapshot of the current overrides — used by the GUI to
        prepopulate sliders. Returns a *shallow copy* so the caller
        can't accidentally mutate Player state."""
        with self._lock:
            return {h: dict(k) for h, k in self._knob_overrides.items()}

    # ------------------------------------------------------------------
    # Save current state to a .sb file
    # ------------------------------------------------------------------

    def save_state(self, path) -> str:
        """Write a ``.sb`` file capturing the current source + overrides.

        Phrase-composed sessions re-run ``compose_from_text`` with the
        current ``seed_offset / style_override / tempo_override`` and
        write the result. File-loaded sessions copy the source and
        rewrite its ``tempo`` line if a tempo override is active.

        Mute / solo / per-channel program overrides set via the GUI do
        not round-trip yet — those live on the synth side, not in the
        song. The returned status message says so when relevant so
        users don't think their mute set is being silently lost.
        """
        with self._lock:
            if self.current_phrase is None and self.current_song_path is None:
                return "error: no song loaded"
            out = Path(path).expanduser()
            try:
                content = self._serialize_current_state()
            except Exception as e:  # noqa: BLE001 — surface to caller
                return f"error: {e}"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content)
            warnings: list[str] = []
            if self._user_mutes:
                warnings.append(
                    f"mute set {sorted(self._user_mutes)} not saved"
                )
            if self._solo:
                warnings.append(
                    f"solo set {sorted(self._solo)} not saved"
                )
            warn_suffix = f"  (note: {'; '.join(warnings)})" if warnings else ""
            return f"wrote {out} ({self._params_summary()}){warn_suffix}"

    def _serialize_current_state(self) -> str:
        """Build the ``.sb`` text reflecting the current source +
        compose overrides + tempo override."""
        if self.current_phrase is not None:
            sb = compose_from_text(
                self.current_phrase,
                seed_offset=self.seed_offset,
                style_override=self.style_override,
                style_per_type=self.style_per_type,
                tempo_override=self.tempo_override,
            )
            return self._with_state_header(sb)
        assert self.current_song_path is not None
        src = self.current_song_path.read_text()
        if self.tempo_override is not None:
            src = _rewrite_song_tempo(src, int(self.tempo_override))
        return self._with_state_header(src)

    def _with_state_header(self, sb: str) -> str:
        """Prepend a comment header documenting the override chain so
        the saved file is self-explanatory when re-opened months later."""
        from datetime import datetime

        bits: list[str] = []
        if self.current_phrase is not None:
            bits.append(f"phrase: {self.current_phrase!r}")
        if self.style_override:
            bits.append(f"style_override={self.style_override}")
        if self.tempo_override is not None:
            bits.append(f"tempo_override={self.tempo_override}")
        if self.seed_offset:
            bits.append(f"seed_offset={self.seed_offset}")
        if not bits:
            bits.append("no overrides")
        header = (
            f"# Saved by slackbeatz on {datetime.now().isoformat(timespec='seconds')}.\n"
            f"# State: {', '.join(bits)}\n\n"
        )
        return header + sb.lstrip()

    def stop(self) -> str:
        """Stop the playback thread, send all-notes-off, return."""
        with self._lock:
            was_playing = self.is_playing
            self._stop_locked()
            self.on_state_change()
            return "stopped" if was_playing else "(not playing)"

    def toggle(self) -> str:
        if self.is_playing:
            return self.stop()
        return self.play()

    # ------------------------------------------------------------------
    # Parameter setters — each restarts playback if a song is loaded
    # ------------------------------------------------------------------

    def set_tempo(self, bpm: Optional[int]) -> str:
        """Override the BPM (None = restore composer default)."""
        with self._lock:
            self.tempo_override = None if bpm is None else int(bpm)
            return self._restart_after_change(
                f"tempo → {self.tempo_override or 'auto'}",
            )

    def set_skip_part(self, part_name: str, skip: bool) -> str:
        """Add or remove *part_name* from the arrangement skip list.

        Used by the 🎼 Builder tab's part-row checkboxes. Skipping
        affects EVERY arrangement occurrence of that part name —
        good enough for "skip the intro" but doesn't differentiate
        when the same part appears multiple times in the
        arrangement (e.g. two drops). Hand-edit the saved .sb for
        finer control."""
        with self._lock:
            if skip:
                self.skip_parts.add(part_name)
            else:
                self.skip_parts.discard(part_name)
            verb = "muted" if skip else "live"
            return self._restart_after_change(f"part {part_name!r} {verb}")

    def set_part_attr(self, part_name: str, attr: str, value) -> str:
        """Override one attribute on one part (tempo / key / role /
        tension / transpose_prob / scale_override). ``value=None``
        clears the override for that attribute. Applied in
        :meth:`_resolve_current` via :func:`object.__setattr__` on
        the frozen :class:`ResolvedPart`."""
        allowed = {"tempo", "key", "role", "tension",
                   "transpose_prob", "scale_override", "bars"}
        if attr not in allowed:
            return f"error: unknown part attr {attr!r} (try {sorted(allowed)})"
        with self._lock:
            attrs = self._part_overrides.setdefault(part_name, {})
            if value is None:
                attrs.pop(attr, None)
                if not attrs:
                    self._part_overrides.pop(part_name, None)
                return self._restart_after_change(
                    f"cleared {part_name}.{attr}",
                )
            attrs[attr] = value
            return self._restart_after_change(
                f"{part_name}.{attr} → {value}",
            )

    def get_part_overrides(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {p: dict(a) for p, a in self._part_overrides.items()}

    def set_arrangement(self, atoms: Optional[list[str]]) -> str:
        """Override the song's arrangement with *atoms* (a flat list of
        part names). ``atoms=None`` clears the override and the
        arrangement reverts to whatever the .sb's ``play`` line
        produced. Unknown part names are dropped silently at resolve
        time (the same way ``skip_parts`` no-ops on missing parts)."""
        with self._lock:
            if atoms is None:
                self.arrangement_override = None
                return self._restart_after_change("arrangement → default")
            self.arrangement_override = list(atoms)
            return self._restart_after_change(
                f"arrangement → {' '.join(self.arrangement_override) or '(empty)'}",
            )

    def set_gen_meter(self, gen_handle: str, meter: Optional[str]) -> str:
        """Override the meter (time signature) of one named gen
        (voice). ``meter=None`` clears the override and the gen
        reverts to whatever the composer / .sb file set — which by
        default means "inherit the part's meter".

        Enables polyrhythm compositions — different voices can run
        in different time signatures within the same part (e.g.
        drums in 4/4 while bass runs in 5/4). The DSL already
        supports this via ``gen kick rhythm psytrance meter=3/4``;
        this Player method exposes the same control at runtime via
        the Generators UI."""
        with self._lock:
            if meter:
                # Validate now so a bad string from the GUI doesn't
                # silently break playback on the next resolve.
                from slackbeatz.theory.meter import Meter
                try:
                    Meter.parse(meter)
                except ValueError as e:
                    return f"error: {e}"
                self.gen_meter_overrides[gen_handle] = meter
            else:
                self.gen_meter_overrides.pop(gen_handle, None)
            label = meter or "default"
            return self._restart_after_change(
                f"gen {gen_handle!r} meter → {label}",
            )

    def set_style(self, style: Optional[str]) -> str:
        """Override the style (None = restore composer's keyword pick).

        Only applies to phrase-composed songs. File-loaded .sb songs
        already have their gens declared with explicit styles.
        """
        with self._lock:
            if style is not None and style not in KNOWN_STYLES:
                return f"unknown style {style!r} — known: {', '.join(KNOWN_STYLES)}"
            if self.current_song_path is not None:
                return "style override only applies to phrase-composed songs"
            self.style_override = style
            return self._restart_after_change(
                f"style → {self.style_override or 'auto'}",
            )

    def set_style_for_type(self, type_: str, style: Optional[str]) -> str:
        """Per-gen-type style override (Builder 🎨 Per-voice section).
        ``style=None`` clears the override for *type_*. Validates *style*
        against :data:`KNOWN_STYLES` — a per-type bad style is rejected
        the same way :meth:`set_style` rejects the global one."""
        with self._lock:
            if self.current_song_path is not None:
                return "style override only applies to phrase-composed songs"
            if style is None:
                if self.style_per_type:
                    self.style_per_type.pop(type_, None)
                    if not self.style_per_type:
                        self.style_per_type = None
                return self._restart_after_change(
                    f"cleared {type_} style override",
                )
            if style not in KNOWN_STYLES:
                return f"unknown style {style!r} — known: {', '.join(KNOWN_STYLES)}"
            if self.style_per_type is None:
                self.style_per_type = {}
            self.style_per_type[type_] = style
            return self._restart_after_change(
                f"{type_} style → {style}",
            )

    def clear_styles_per_type(self) -> str:
        """Wipe every entry in :attr:`style_per_type`. Used by the
        Builder when the user picks a new global style — we don't
        silently keep stale per-voice overrides around."""
        with self._lock:
            if not self.style_per_type:
                return "no per-voice style overrides"
            n = len(self.style_per_type)
            self.style_per_type = None
            return self._restart_after_change(
                f"cleared {n} per-voice style override(s)",
            )

    def set_seed_offset(self, offset: int) -> str:
        with self._lock:
            self.seed_offset = int(offset)
            return self._restart_after_change(f"seed offset → {self.seed_offset}")

    def reroll_seed(self) -> str:
        """Pick a fresh random seed_offset + restart."""
        with self._lock:
            self.seed_offset = random.randint(1, 2**31 - 1)
            return self._restart_after_change(
                f"reroll → seed offset {self.seed_offset}",
            )

    def set_loop(self, on: bool) -> str:
        with self._lock:
            self.loop = bool(on)
            return f"loop {'on' if self.loop else 'off'}"

    def set_emit_clock(self, on: bool) -> str:
        """Toggle MIDI Clock emission. Takes effect on the next song
        restart (toggling mid-song is a no-op for the currently-playing
        emitter — restart via /play or any param change to re-arm)."""
        with self._lock:
            self.emit_clock = bool(on)
            was_playing = self.is_playing
            # Restart so the new clock state takes effect immediately.
            if was_playing:
                self._restart_after_change(
                    f"midi clock {'on' if self.emit_clock else 'off'}",
                )
            return f"midi clock {'on' if self.emit_clock else 'off'}"

    def reset_overrides(self) -> str:
        """Clear style / tempo / seed + per-gen knob overrides; restart
        with composer defaults restored."""
        with self._lock:
            self.style_override = None
            self.tempo_override = None
            self.seed_offset = 0
            n_knobs = sum(len(k) for k in self._knob_overrides.values())
            self._knob_overrides.clear()
            extra = f" (+ {n_knobs} knob override(s))" if n_knobs else ""
            return self._restart_after_change(f"overrides cleared{extra}")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> str:
        with self._lock:
            playing = "playing" if self.is_playing else "stopped"
            src = (
                f'"{self.current_phrase}"' if self.current_phrase else
                (str(self.current_song_path) if self.current_song_path else "(none)")
            )
            return (
                f"{playing}: {src}\n"
                f"  title:  {self.title!r}\n"
                f"  style:  {self.style_override or '(auto)'}\n"
                f"  tempo:  {self.tempo_override or '(auto)'}\n"
                f"  seed:   {self.seed_offset}\n"
                f"  loop:   {'on' if self.loop else 'off'}"
            )

    def _params_summary(self) -> str:
        bits: list[str] = []
        if self.style_override:
            bits.append(f"style={self.style_override}")
        if self.tempo_override is not None:
            bits.append(f"tempo={self.tempo_override}")
        if self.seed_offset:
            bits.append(f"seed={self.seed_offset}")
        if self.loop:
            bits.append("loop")
        return ", ".join(bits) if bits else "defaults"

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _restart_after_change(self, status: str) -> str:
        """If a song is loaded, regenerate + restart with new params.

        When :attr:`preserve_position` is on (the default), the new
        worker resumes at the previous scheduler's current tick rounded
        down to the bar boundary — so changing tempo / style / seed
        mid-bar doesn't jolt back to the start.
        """
        if self.current_phrase is None and self.current_song_path is None:
            return status  # nothing to restart
        was_playing = self.is_playing
        # Capture current tick *before* stopping so it survives the
        # scheduler instance going away.
        resume_tick = 0
        if (
            self.preserve_position
            and was_playing
            and self._current_scheduler is not None
        ):
            resume_tick = max(0, int(self._current_scheduler.current_tick))
        self._stop_locked()
        if was_playing:
            # Round resume_tick down to the bar boundary so we restart
            # cleanly. For a freshly-composed song the part meter may
            # change, but bar-aligned is still the right snap.
            try:
                resolved = self._resolve_current()
                resume_tick = self._round_to_bar(resolved, resume_tick)
            except Exception:
                pass
            extra = self.play(from_tick=resume_tick)
            return f"{status}\n  {extra}"
        return status

    def _resolve_current(self):
        """Build a ResolvedSong from current_phrase or current_song_path.

        After resolve, applies per-gen knob overrides (from
        :attr:`_knob_overrides`) by mutating each gen's knobs dict in
        place — ResolvedGen is frozen at the dataclass level but its
        knobs field is a regular mutable dict, so updating it works
        and the scheduler sees the new values immediately.
        """
        import tempfile
        if self.current_phrase is not None:
            sb_content = compose_from_text(
                self.current_phrase,
                seed_offset=self.seed_offset,
                style_override=self.style_override,
                style_per_type=self.style_per_type,
                tempo_override=self.tempo_override,
            )
            with tempfile.NamedTemporaryFile(
                suffix=".sb", delete=False, mode="w", encoding="utf-8",
            ) as tf:
                tf.write(sb_content)
                tmp_path = Path(tf.name)
            try:
                file_ast = parse_file(tmp_path)
                if file_ast.song is None:
                    raise ParseError(0, "composer produced no song block")
                setup = self._load_setup_for(tmp_path, file_ast)
                resolved = resolve_song(file_ast.song, setup, cli_seed=0)
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            # File-loaded mode.
            assert self.current_song_path is not None
            file_ast = parse_file(self.current_song_path)
            if file_ast.song is None:
                raise ParseError(0, f"no song block in {self.current_song_path}")
            setup = self._load_setup_for(self.current_song_path, file_ast)
            resolved = resolve_song(file_ast.song, setup, cli_seed=0)
            # File-loaded songs ignore most overrides except tempo.
            if self.tempo_override is not None:
                for part in resolved.parts.values():
                    object.__setattr__(part, "tempo", int(self.tempo_override))

        # Per-part attribute overrides (from the Builder's Parts panel).
        # ResolvedPart is frozen at the class level but its fields are
        # settable through :func:`object.__setattr__` — same escape
        # hatch the tempo_override branch above uses.
        if self._part_overrides:
            for pname, attrs in self._part_overrides.items():
                part = resolved.parts.get(pname)
                if part is None:
                    continue
                for attr, val in attrs.items():
                    try:
                        object.__setattr__(part, attr, val)
                    except Exception:
                        # Stale override against a freshly-loaded song
                        # whose Part doesn't have this attr — skip
                        # rather than crash playback.
                        continue
                # `bars` is the lower bound of `bars=N..M` ranges; if
                # the user pins it via override, also clear bars_max
                # so the scheduler doesn't randint() back into the old
                # upper bound.
                if "bars" in attrs:
                    try:
                        object.__setattr__(part, "bars_max", None)
                    except Exception:
                        pass

        # Apply per-gen knob overrides (last so they win against
        # everything baked into the composed / loaded .sb).
        self._apply_knob_overrides(resolved)

        # Per-gen meter overrides (polyrhythm via the Generators UI).
        # ResolvedGen is frozen at the class level but its meter
        # field is settable through object.__setattr__ — same escape
        # hatch the tempo_override branch uses above. ``meter=None``
        # on a gen means "inherit the part meter", so writing a
        # concrete Meter here forces that voice to run on its own
        # cycle while every other voice in the part stays on the
        # part meter.
        if self.gen_meter_overrides:
            from slackbeatz.theory.meter import Meter
            for handle, meter_str in self.gen_meter_overrides.items():
                gen = resolved.gens.get(handle)
                if gen is None:
                    continue
                try:
                    new_meter = Meter.parse(meter_str)
                except ValueError:
                    # Malformed string from a stale GUI state — skip
                    # rather than crash playback.
                    continue
                object.__setattr__(gen, "meter", new_meter)

        # Arrangement filter (skip-part toggles from the Builder).
        # In-place mutation because resolved.arrangement is a list +
        # the dataclass is frozen at the class level only. Skips ALL
        # occurrences of each named part — fine for "skip the intro";
        # the user can save + hand-edit the .sb if they want finer
        # control over individual arrangement positions.
        if self.skip_parts:
            resolved.arrangement[:] = [
                p for p in resolved.arrangement if p not in self.skip_parts
            ]

        # Arrangement override (set by the 🎬 Arrangement tab). Replaces
        # the resolved arrangement wholesale. Unknown part names are
        # filtered out here so a stale override against a freshly loaded
        # song degrades gracefully instead of crashing the scheduler.
        if self.arrangement_override is not None:
            resolved.arrangement[:] = [
                p for p in self.arrangement_override if p in resolved.parts
            ]

        self.current_resolved = resolved
        self.current_setup = setup
        return resolved

    def _apply_knob_overrides(self, resolved) -> None:
        for handle, knobs in self._knob_overrides.items():
            if handle not in resolved.gens:
                continue
            gen = resolved.gens[handle]
            # gen.knobs is a regular dict — update in place so the
            # frozen dataclass guard isn't tripped.
            for name, value in knobs.items():
                gen.knobs[name] = value

    def _load_setup_for(self, song_path: Path, file_ast):
        """Mirror cli._load_setup_for_song — load the song's referenced
        setup or fall back to the CLI's --setup arg."""
        if self.setup_arg is not None:
            return load_setup(self.setup_arg, base_path=song_path)
        if file_ast.setup is not None:
            from slackbeatz.setup.loader import setup_from_ast
            return setup_from_ast(file_ast.setup)
        if file_ast.song is not None and file_ast.song.setup_ref:
            return load_setup(file_ast.song.setup_ref, base_path=song_path)
        # Fallback: empty setup. The resolver will fail for songs that
        # need it, which is the right behaviour.
        return load_setup("gm", base_path=song_path)

    def ensure_osc_routing_ready(self) -> None:
        """Eagerly create + open the shared MultiPortSink so its
        virtual MIDI ports exist on the system. Required before
        spawning surge-xt-cli (its ``--list-devices`` only sees ports
        that already exist at spawn time) and before the sampler
        subscribes to the voice / fx virtual ports."""
        if not self.osc_routing or self._shared_routing_sink is not None:
            return
        from slackbeatz.sinks.multiport import MultiPortSink
        from slackbeatz.synthhost import OSC_CHANNELS
        ch_to_port = {
            ch_1idx - 1: port_name
            for (ch_1idx, port_name, _patch) in OSC_CHANNELS.values()
        }
        multi = MultiPortSink(ch_to_port)
        multi.open()
        self._shared_routing_sink = multi

    def _make_sink(self):
        """Build the sink for one playback run.

        Returns :class:`RealtimeSink` when ``osc_routing`` is off, or
        a :class:`CompositeSink` that routes pitched channels onto
        dedicated virtual ports (for the headless synths + sampler to
        subscribe to) when on. The CompositeSink reuses a shared
        MultiPortSink across runs so the virtual ports persist
        between songs.

        The returned sink is always wrapped in an :class:`_ActivityTapSink`
        so :attr:`last_note_on_time` updates on every dispatched
        note_on. The GUI polls that dict to drive per-channel activity
        indicators on the Mixer + Sound tabs — negligible cost per
        message (one dict write).
        """
        base = RealtimeSink(port_name=self.port_name)
        if not self.osc_routing:
            return _ActivityTapSink(base, self)
        from slackbeatz.sinks.composite import CompositeSink
        from slackbeatz.sinks.multiport import MultiPortSink
        from slackbeatz.synthhost import OSC_CHANNELS
        # 0-indexed channel → virtual port name. Drums (channel 10 /
        # 0-indexed 9) are NOT in this map — they fall through to the
        # default sink (= FluidSynth).
        ch_to_port = {
            ch_1idx - 1: port_name
            for (ch_1idx, port_name, _patch) in OSC_CHANNELS.values()
        }
        if self._shared_routing_sink is None:
            # Open once, lazily — the virtual ports stay alive across
            # song restarts so each subscribed synth's MIDI input
            # doesn't blink out every time the user nudges a slider.
            multi = MultiPortSink(ch_to_port)
            multi.open()
            self._shared_routing_sink = multi
        overrides = {ch: self._shared_routing_sink for ch in ch_to_port}
        # manage_overrides=False so the per-playback open()/close()
        # cycle doesn't touch the shared MultiPortSink.
        composite = CompositeSink(
            default=base,
            channel_overrides=overrides,
            manage_overrides=False,
        )
        return _ActivityTapSink(composite, self)

    def is_channel_active(self, channel_1idx: int, window_ms: float = 150.0) -> bool:
        """True if *channel_1idx* has notes currently sounding —
        either a held note (note_on with no matching note_off yet)
        or a recently-released short note (within ``window_ms`` of
        its note_on).

        Two-pronged so the GUI activity indicator lights up for
        both **long pads** (one note_on at the start of an 8-bar
        chord, note_off way later — held_notes catches that) AND
        **fast 16ths** (note_on/note_off pair within one poll cycle
        — the timestamp grace window catches those).

        Lock-free read from the per-channel dict + set. Worst case
        is one stale value for one polling cycle (~80 ms),
        imperceptible."""
        ch = int(channel_1idx)
        held = self.held_notes.get(ch)
        if held:
            return True
        last = self.last_note_on_time.get(ch)
        if last is None:
            return False
        import time as _time
        return (_time.monotonic() - last) * 1000.0 < window_ms

    def _play_loop(self, resolved, from_tick: int = 0) -> None:
        """Worker thread body. Plays *resolved* once (or repeatedly if
        loop is True), respecting :attr:`_stop_event`."""
        first_iteration_from_tick = from_tick
        # Flip the public "is_playing" flag here, not in play() — that
        # way the GUI sees us as "not playing" the moment the worker
        # enters its finally block, whether we exited cleanly (song
        # finished) or via the stop event (user clicked Stop). Without
        # this, natural finish left the Transport button stuck on
        # "■ Stop" + the next click triggered play-from-beginning
        # instead of Stop.
        self._is_playing = True
        self.on_state_change()
        try:
            while True:
                sink = self._make_sink()
                tempo_map = build_tempo_map(resolved)
                clock = InternalClock(tempo_map)
                scheduler = Scheduler(resolved, sink, clock)
                self._current_scheduler = scheduler
                # Per-part loop bound, if active. Read each iteration
                # so toggling the Parts-panel 🔁 takes effect on the
                # next loop without needing to restart playback.
                part_loop_span: Optional[tuple[int, int]] = None
                if self.loop_position is not None:
                    spans = self.get_part_position_spans()
                    if 0 <= self.loop_position < len(spans):
                        part_loop_span = spans[self.loop_position]
                # MIDI Clock output, if enabled.
                emitter = None
                if self.emit_clock:
                    from slackbeatz.clock_emitter import ClockEmitter
                    emitter = ClockEmitter(
                        port_name=self.port_name,
                        tempo_map=tempo_map,
                        stop_event=self._stop_event,
                        start_at_tick=first_iteration_from_tick,
                    )
                    emitter.start()
                try:
                    scheduler.run(
                        stop_event=self._stop_event,
                        resume_from_tick=first_iteration_from_tick,
                        stop_at_tick=(
                            part_loop_span[1] if part_loop_span else None
                        ),
                        muted_channels=self.muted_channels,
                    )
                except Exception as exc:  # noqa: BLE001
                    if not self._stop_event.is_set():
                        print(f"playback error: {exc}", file=sys.stderr)
                    break
                finally:
                    if emitter is not None:
                        emitter.stop()
                    self._current_scheduler = None
                if self._stop_event.is_set():
                    break
                # Part loop wins over whole-song loop: when active,
                # restart at the position's start tick regardless of
                # self.loop. Re-read loop_position each cycle so the
                # user toggling 🔁 off mid-iteration exits cleanly.
                if self.loop_position is not None and part_loop_span is not None:
                    first_iteration_from_tick = part_loop_span[0]
                    try:
                        with self._lock:
                            resolved = self._resolve_current()
                    except Exception as exc:  # noqa: BLE001
                        print(f"part-loop re-resolve failed: {exc}", file=sys.stderr)
                        break
                    continue
                # Part-loop was just cleared while the bounded
                # scheduler was running — continue playback past the
                # part end into the rest of the song instead of
                # stopping. (Without this, toggling 🔁 off would
                # silently exit at the part boundary.)
                if part_loop_span is not None and self.loop_position is None:
                    part_end = part_loop_span[1]
                    total = int(build_tempo_map(resolved).end_tick)
                    if part_end < total:
                        first_iteration_from_tick = part_end
                        continue
                if not self.loop:
                    break
                # Subsequent loop iterations always start from 0.
                first_iteration_from_tick = 0
                # Loop: re-resolve so seed / overrides re-apply if the
                # user changed something while this iteration was
                # running. (Stop wasn't requested, so just continue.)
                try:
                    with self._lock:
                        resolved = self._resolve_current()
                except Exception as exc:  # noqa: BLE001
                    print(f"loop re-resolve failed: {exc}", file=sys.stderr)
                    break
        finally:
            # Defensive: ensure no notes hang on the synth if the
            # worker exits abnormally. Use _make_sink() so the
            # all-notes-off broadcasts reach the per-channel virtual
            # ports too when osc_routing is on.
            try:
                tmp = self._make_sink()
                tmp.open()
                tmp.close()  # close() sends all-notes-off across all channels
            except Exception:
                pass
            # Mark playback finished + notify the GUI. Critical for
            # the natural-finish path — the song hit end-of-arrangement
            # with loop=off, the while loop exited cleanly, the user
            # never pressed Stop. Without this signal the Transport
            # button label stayed on "■ Stop" + the next click
            # interpreted as toggle → is_playing → False → start a
            # new playback from bar 1, which felt like "Stop sent me
            # to the beginning".
            self._is_playing = False
            self.on_state_change()

    # ------------------------------------------------------------------
    # Helpers — bar/tick conversion + channel-silence on mute
    # ------------------------------------------------------------------

    def _silence_channel(self, channel: int) -> None:
        """Send CC 123 (all-notes-off) on *channel* so muting takes
        effect immediately for currently-held notes. Goes through
        ``_make_sink()`` so it reaches the right destination (the
        per-channel virtual port for pitched channels under
        osc_routing, FluidSynth otherwise)."""
        try:
            import mido
            tmp = self._make_sink()
            tmp.open()
            tmp.send(
                mido.Message("control_change", channel=channel - 1, control=123, value=0)
            )
            tmp.close()
        except Exception:
            pass  # synth gone, port closed, etc.

    def _bar_beat_to_tick(self, resolved, *, bar: int, beat: float) -> int:
        """Resolve a (1-indexed bar, fractional beat) to an absolute
        tick in *resolved*'s arrangement. Bars cumulate per part using
        each part's meter."""
        from slackbeatz.engine.clock import PPQ, bars_to_ticks

        # Bar 1 = start of song. Walk parts until we've consumed the
        # requested bar count.
        bars_left = max(0, bar - 1)
        cursor = 0
        for part_name in resolved.arrangement:
            part = resolved.parts[part_name]
            part_bars = part.bars
            if bars_left >= part_bars:
                cursor += bars_to_ticks(part_bars, meter=part.meter)
                bars_left -= part_bars
                continue
            # Land inside this part.
            cursor += bars_to_ticks(bars_left, meter=part.meter)
            # Convert beat fraction to ticks (beat = quarter note = PPQ ticks).
            cursor += int(beat * PPQ)
            return cursor
        # Past end of song — clamp to start of last bar.
        return max(0, cursor - 1)

    def _round_to_bar(self, resolved, tick: int) -> int:
        """Round *tick* down to the nearest bar boundary in *resolved*."""
        from slackbeatz.engine.clock import bars_to_ticks

        cursor = 0
        for part_name in resolved.arrangement:
            part = resolved.parts[part_name]
            ticks_per_bar = bars_to_ticks(1, meter=part.meter)
            for _ in range(part.bars):
                if cursor + ticks_per_bar > tick:
                    return cursor
                cursor += ticks_per_bar
        return cursor

    def _tick_to_bar_label(self, resolved, tick: int) -> str:
        """Pretty 'bar N' (or 'bar N beat M') label for a tick."""
        from slackbeatz.engine.clock import PPQ, bars_to_ticks

        cursor = 0
        bar_idx = 1
        for part_name in resolved.arrangement:
            part = resolved.parts[part_name]
            ticks_per_bar = bars_to_ticks(1, meter=part.meter)
            for _ in range(part.bars):
                if cursor + ticks_per_bar > tick:
                    beats = (tick - cursor) / PPQ
                    if beats < 0.05:
                        return f"{bar_idx}"
                    return f"{bar_idx} beat {beats + 1:.1f}"
                cursor += ticks_per_bar
                bar_idx += 1
        return f"end ({bar_idx})"

    def _stop_locked(self) -> None:
        """Internal: stop the playback thread. Must hold ``_lock``."""
        if self._thread is None or not self._thread.is_alive():
            self._thread = None
            return
        self._stop_event.set()
        self._thread.join(timeout=2)
        self._thread = None
        # Hard-cut any audible sound across every routed backend
        # (FluidSynth port + per-Surge virtual ports + sampler ports).
        # The previous implementation only sent all-notes-off to the
        # FluidSynth port — Surge instances + the Sampler kept
        # ringing because they're subscribed to the MultiPortSink.
        self._send_all_sound_off()
        # Reset the event so the next play() starts clean.
        self._stop_event.clear()

    def _send_all_sound_off(self) -> None:
        """Send CC 120 (All Sound Off — hard cut, ignore release) +
        CC 123 (All Notes Off — release-respecting) on every channel
        of every output port the player has open. Covers:

        * the FluidSynth port at ``self.port_name`` — drums + any
          channel routed there as a fall-through.
        * each virtual MIDI port managed by ``self._shared_routing_sink``
          (the MultiPortSink under ``osc_routing``) — surge-xt-cli
          instances subscribed to those ports + the in-process
          Sampler.

        We send 120 + 123 to be belt-and-suspenders compatible: some
        synths only honour one; the Sampler honours 120 specifically
        as "hard kill all voices" (see :meth:`Sampler._reader_loop`).

        Does NOT close any ports — the MultiPortSink stays alive
        across stops so each Surge XT instance's MIDI input
        subscription doesn't blink out."""
        import mido

        def _panic(send_fn, label: str) -> None:
            try:
                for ch in range(16):
                    send_fn(mido.Message("control_change", channel=ch, control=120, value=0))
                    send_fn(mido.Message("control_change", channel=ch, control=123, value=0))
            except Exception as e:  # noqa: BLE001 — surface but don't crash stop()
                import sys as _sys
                print(
                    f"slackbeatz: panic CCs on {label!r} failed ({e})",
                    file=_sys.stderr,
                )

        # FluidSynth (and anything else listening on the default
        # output port). Open a fresh transient mido output so we
        # don't have to coordinate with the worker thread's sink.
        try:
            with mido.open_output(self.port_name) as out:
                _panic(out.send, self.port_name)
        except Exception:
            pass

        # Shared MultiPortSink — covers Surge instances + Sampler.
        # MultiPortSink.send(msg) routes per-message-channel to the
        # right virtual port; iterating 0..15 hits every routed
        # channel without us having to know the port → channel map.
        if self._shared_routing_sink is not None:
            _panic(self._shared_routing_sink.send, "MultiPortSink")

        # Clear the activity dicts directly — _send_all_sound_off
        # bypasses _ActivityTapSink (the worker's sink is being torn
        # down concurrently), so the tap's CC-120/123 handler won't
        # fire to clear them. Without this the Mixer activity dots
        # would stay green after Stop until the next song's events
        # naturally cleared the held set.
        for held in self.held_notes.values():
            held.clear()
        self.last_note_on_time.clear()


# --------------------------------------------------------------------------
# Activity-tap sink — records per-channel note_on timestamps on the Player
# --------------------------------------------------------------------------


class _ActivityTapSink:
    """Sink wrapper that updates :attr:`Player.last_note_on_time` on
    every dispatched ``note_on`` (with velocity > 0), then forwards
    the message to the inner sink unchanged.

    Lives here rather than under :mod:`slackbeatz.sinks` because it
    references the Player by design — this is the "scheduler thread
    → GUI thread" handoff for per-channel activity indicators on the
    Mixer + Sound tabs. The dict write is atomic in CPython; the GUI
    reads lock-free.

    Doesn't inherit :class:`Sink` formally to avoid an import cycle;
    the duck-typed open / close / send trio is all the scheduler
    needs.
    """

    def __init__(self, inner, player) -> None:
        self._inner = inner
        self._player = player

    def open(self) -> None:
        self._inner.open()

    def close(self) -> None:
        self._inner.close()

    def send(self, msg) -> None:
        # Track per-channel held notes (sustained pads) + last-on
        # timestamps (short notes). Everything else (CC, pitchbend,
        # program_change, meta) just forwards. CC 120 / CC 123 also
        # clear held-notes since they release everything on the
        # downstream synth — keeps the activity indicator honest
        # when the Player sends a panic.
        mtype = msg.type
        if mtype == "note_on" or mtype == "note_off":
            ch_1idx = msg.channel + 1  # mido 0-indexed → slackbeatz 1-indexed
            note = msg.note
            velocity = getattr(msg, "velocity", 0)
            held = self._player.held_notes.setdefault(ch_1idx, set())
            if mtype == "note_on" and velocity > 0:
                held.add(note)
                import time as _time
                self._player.last_note_on_time[ch_1idx] = _time.monotonic()
            else:
                # note_off OR note_on with velocity 0 (release)
                held.discard(note)
        elif mtype == "control_change":
            controller = getattr(msg, "control", -1)
            if controller in (120, 123):
                ch_1idx = msg.channel + 1
                held = self._player.held_notes.get(ch_1idx)
                if held is not None:
                    held.clear()
        self._inner.send(msg)

    @property
    def channel_overrides(self):
        """Pass-through for CompositeSink introspection from cli.py
        (used to find the MultiPortSink for eager open in --surge
        mode). Returns the inner sink's overrides if it has them,
        else empty dict."""
        return getattr(self._inner, "channel_overrides", {})
