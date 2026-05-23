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
    ) -> None:
        self.port_name = port_name
        self.setup_arg = setup_arg
        self.on_state_change = on_state_change or (lambda: None)

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Re-entrancy guard for set_* operations triggered from the GUI
        # thread while a playback thread is mid-stop.
        self._lock = threading.RLock()

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

        # Loop on song end — re-render the same params and play again.
        self.loop: bool = False

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
        return self._thread is not None and self._thread.is_alive()

    def play(self) -> str:
        """Compose/resolve the current source and start the playback
        thread. Stops any in-flight playback first.

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
                target=self._play_loop, args=(resolved,), daemon=True,
            )
            self._thread.start()
            self.on_state_change()
            return f'playing "{self.title}" ({self._params_summary()})'

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

    def reset_overrides(self) -> str:
        """Clear style / tempo / seed overrides; restart with composer
        defaults restored."""
        with self._lock:
            self.style_override = None
            self.tempo_override = None
            self.seed_offset = 0
            return self._restart_after_change("overrides cleared")

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
        """If a song is loaded, regenerate + restart with new params."""
        if self.current_phrase is None and self.current_song_path is None:
            return status  # nothing to restart
        was_playing = self.is_playing
        self._stop_locked()
        if was_playing:
            extra = self.play()
            return f"{status}\n  {extra}"
        return status

    def _resolve_current(self):
        """Build a ResolvedSong from current_phrase or current_song_path."""
        import tempfile
        if self.current_phrase is not None:
            sb_content = compose_from_text(
                self.current_phrase,
                seed_offset=self.seed_offset,
                style_override=self.style_override,
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
                return resolve_song(file_ast.song, setup, cli_seed=0)
            finally:
                tmp_path.unlink(missing_ok=True)
        # File-loaded mode.
        assert self.current_song_path is not None
        file_ast = parse_file(self.current_song_path)
        if file_ast.song is None:
            raise ParseError(0, f"no song block in {self.current_song_path}")
        setup = self._load_setup_for(self.current_song_path, file_ast)
        resolved = resolve_song(file_ast.song, setup, cli_seed=0)
        # File-loaded songs ignore most overrides except tempo: we
        # post-apply tempo_override to the resolved parts so the GUI
        # tempo slider works on .sb files too.
        if self.tempo_override is not None:
            for part in resolved.parts.values():
                # ResolvedPart is a frozen dataclass — use object.__setattr__
                # to slide tempo in. (See the resolver for the equivalent
                # pattern used at construction time.)
                object.__setattr__(part, "tempo", int(self.tempo_override))
        return resolved

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

    def _play_loop(self, resolved) -> None:
        """Worker thread body. Plays *resolved* once (or repeatedly if
        loop is True), respecting :attr:`_stop_event`."""
        try:
            while True:
                sink = RealtimeSink(port_name=self.port_name)
                tempo_map = build_tempo_map(resolved)
                clock = InternalClock(tempo_map)
                scheduler = Scheduler(resolved, sink, clock)
                try:
                    scheduler.run(stop_event=self._stop_event)
                except Exception as exc:  # noqa: BLE001
                    if not self._stop_event.is_set():
                        print(f"playback error: {exc}", file=sys.stderr)
                    break
                if self._stop_event.is_set() or not self.loop:
                    break
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
            # worker exits abnormally.
            try:
                tmp = RealtimeSink(port_name=self.port_name)
                tmp.open()
                tmp.close()  # close() sends all-notes-off across all channels
            except Exception:
                pass

    def _stop_locked(self) -> None:
        """Internal: stop the playback thread. Must hold ``_lock``."""
        if self._thread is None or not self._thread.is_alive():
            self._thread = None
            return
        self._stop_event.set()
        self._thread.join(timeout=2)
        self._thread = None
        # Send all-notes-off to clear any held notes on the synth.
        try:
            tmp = RealtimeSink(port_name=self.port_name)
            tmp.open()
            tmp.close()
        except Exception:
            pass
        # Reset the event so the next play() starts clean.
        self._stop_event.clear()
