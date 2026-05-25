"""Parser for `.sb` files. Consumes the line stream from :mod:`lexer` and
produces a :class:`FileAST`.

The grammar is small enough that a hand-rolled state machine reads more
clearly than a generator stack: at indent 0 each line opens or continues a
top-level block (`setup`, `song`, `inst`, `kit`, `gen`, `part`, `play`); at
indent 1 each line is a child of the most recently opened block of the
right shape (song attributes, kit overrides, part gens).
"""

from __future__ import annotations

from pathlib import Path

from .ast import (
    ArrAtom,
    FileAST,
    GenDecl,
    InstDecl,
    KitDecl,
    KnobValue,
    LfoDecl,
    PartDecl,
    PlayLine,
    SceneAST,
    SceneEntry,
    SetupAST,
    SongAST,
)
from .lexer import Line, tokenize, tokenize_file

# Set of known knob keys per declaration kind. Unknown keys raise at parse
# time so typos surface immediately rather than being silently ignored by
# the generator algorithm.
_GEN_KNOBS = frozenset(
    {"ch", "note", "inst", "kit", "intensity", "swing", "octave", "gate",
     "density", "seed", "program", "cc", "cycle",
     # Per-hit shaping (rhythm / drums) and sidechain (bass):
     "humanize", "drop_prob", "accent", "duck",
     # Pattern + macro chance (issues #2, #8, #9):
     "density_drift", "mute_prob", "evolution",
     # Style-default overrides (issue #19):
     "base_vel", "base_octave",
     # CC expansion (issue #7):
     "pan", "reverb", "modwheel", "resonance", "bend",
     # Round 2 — issue #1 (gate_jitter), #5 (arp_prob),
     # #15 (psytrance bass burble), #22 (per-gen scale override):
     "gate_jitter", "arp_prob", "burble_prob", "scale",
     # Round 3 — issue #3 (octave_jump), #11 (motif_memory),
     # #17 (deep_techno kick-triggered filter env):
     "octave_jump", "motif_memory", "kick_env",
     # Round 4 — issue #16 (vaporwave 8-bar arpeggio period):
     "arp_period",
     # Round 5 — issues #4, #6, #12, #13:
     #   passing_tones (melody chromatic neighbours)
     #   voice_lead    (chords nearest-tone snapping)
     #   polyrhythm    (rhythm secondary euclid layer)
     #   pair          (melody call-and-response handle)
     "passing_tones", "voice_lead", "polyrhythm", "pair",
     # Round 6 — meter override for polymeter (per-gen meter):
     "meter",
     # Round 7 — chord-progression variation knobs:
     #   progression      named chord progression (i-iv, i-VI-ii-IV, …)
     #   bars_per_chord   how slowly the progression advances
     #   voicing          chord voicing shape (triad, seventh, sus2, …)
     #   inversion        which chord tone is in the bass (0-3)
     "progression", "bars_per_chord", "voicing", "inversion",
     # Round 8 — bass variety knobs (also exposed in the GUI knob panel):
     #   walking          chance of chromatic step-up at chord changes
     #   pickup           chance of 8th-note anticipation before changes
     #   fifth_prob       chance of playing chord 5th instead of root
     #   third_prob       chance of playing chord 3rd (colour note)
     "walking", "pickup", "fifth_prob", "third_prob",
     # Round 9 — groove / phrase / fill / variation:
     #   groove           named timing template (shuffle, dilla, trap16, …)
     #   ghost            quiet "ghost" hits between main hits
     #   ghost_vel        velocity ratio for ghost notes
     #   hat_variant      chance of open/pedal hat instead of closed
     #   fill_every       fill every Nth bar (default 4)
     #   fill_style       fill pattern (snare_roll/tom_roll/kick_double/silence)
     #   phrase_lift      velocity bump on bar 0 of each phrase
     #   harmonize_with   handle of another melody gen to harmonise with
     #   interval         harmonisation interval (scale degrees)
     #   modulate_to      key to modulate the part to (relative_major, etc.)
     #   tension_dyn      chord-tension-aware dynamics (0..1)
     #   drop_intensity   automated drop sweep intensity (0..1)
     #   stutter          stutter-effect probability at section boundary
     #   mistakes         "live mistake" probability for humanity
     #   slide_prob       portamento per-note probability (acid 303 only)
     "groove", "ghost", "ghost_vel", "hat_variant",
     "fill_every", "fill_style", "phrase_lift",
     "harmonize_with", "interval", "modulate_to",
     "tension_dyn", "drop_intensity", "stutter", "mistakes",
     "slide_prob",
     # SH-101-style arp (melody:sh101_arp): the pitch sequence the
     # gen cycles through, comma-separated scale degrees.
     "pitches",
     # TTS list-of-strings (issue #26) + speech (#27) + sample
     # (#28) generator knobs.
     "phrases", "voice", "phrase_interval", "note_base", "velocity",
     "bank", "pattern", "pulses", "steps"}
)
# Part-level knobs:
#   transpose_prob — per-instance roll for transposition (issue #10)
#   scale          — single-part scale override (issue #22)
#   tension        — part-level energy scalar (issue #14); default
#                    auto-derived from role if not set
#   meter          — time signature for the part (overrides song)
_PART_KNOBS = frozenset(
    {"tempo", "key", "role", "seed", "transpose_prob", "scale", "tension",
     "meter",
     # Round 9 — named modulation between parts. modulate_to=NAME
     # resolves to a concrete key relative to the song's key. Names:
     #   relative_major / relative_minor
     #   parallel_major / parallel_minor
     #   dominant (up P5), subdominant (up P4)
     #   fifth_up / fifth_down (M5)
     #   whole_up / whole_down (whole step)
     "modulate_to",
     # Phase 4 — per-part style shorthand. `style=psytrance` on a
     # part expands at resolve time to per-handle algorithm overrides
     # sourced from the named style's StyleProfile.gens table.
     "style"}
)
_INST_KNOBS = frozenset({"ch", "note"})
_KIT_KNOBS = frozenset({"ch", "preset"})
# Scene-entry knobs accepted on a `ch <N> ...` line inside a `scene` block.
# Persisted mixer state — booleans for mute / solo, floats for vol / pan,
# int for the GM program-change. Future scope kinds (surge / sampler /
# part) will define their own knob sets when wired.
_SCENE_CH_KNOBS = frozenset({"vol", "pan", "program", "mute", "solo"})
# Issue #65 — knobs accepted on a top-level `lfo NAME ...` line.
# ``shape`` is required; one of ``bars`` / ``hz`` must be set; the
# rest are optional with shape-appropriate defaults.
_LFO_KNOBS = frozenset({"shape", "bars", "hz", "width", "height", "offset"})
# Scene-entry knobs accepted on a `ch <N> ...` line inside a `scene` block.
# Persisted mixer state — booleans for mute / solo, floats for vol / pan,
# int for the GM program-change. Future scope kinds (surge / sampler /
# part) will define their own knob sets when wired.
_SCENE_CH_KNOBS = frozenset({"vol", "pan", "program", "mute", "solo"})


class ParseError(Exception):
    """Raised on grammar violations. Includes line number and a hint."""

    def __init__(self, line_no: int, msg: str) -> None:
        super().__init__(f"line {line_no}: {msg}")
        self.line_no = line_no


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------

def parse(text: str, *, source_path: str | None = None) -> FileAST:
    """Parse the contents of a `.sb` file."""
    lines = list(tokenize(text))
    return _Parser(lines, source_path=source_path).run()


def parse_file(path: str | Path) -> FileAST:
    """Parse a `.sb` file from disk."""
    lines = tokenize_file(path)
    return _Parser(lines, source_path=str(path)).run()


# --------------------------------------------------------------------------
# Helpers shared between the parser and the public knob-validation API
# --------------------------------------------------------------------------

def _parse_knob_value(raw: str) -> KnobValue:
    """Coerce a knob RHS into bool / int / float / str."""
    if raw == "true":
        return True
    if raw == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _parse_string_list(raw: str, line_no: int) -> tuple[str, ...]:
    """Parse a ``[...]`` literal as a tuple of strings.

    Accepts ``[]`` (empty) and ``["a", "b", ...]``. Strings must use
    double quotes — single quotes aren't part of the DSL. Whitespace
    between tokens is ignored. Trailing commas (``["a",]``) are
    rejected because they're a common typo for "I forgot something
    here".
    """
    inner = raw.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        raise ParseError(line_no, f"expected [..], got {raw!r}")
    inner = inner[1:-1].strip()
    if not inner:
        return ()
    out: list[str] = []
    i = 0
    expect_value = True
    n = len(inner)
    while i < n:
        c = inner[i]
        if c.isspace():
            i += 1
            continue
        if c == ",":
            if expect_value:
                raise ParseError(
                    line_no, f"unexpected ',' in list {raw!r}",
                )
            expect_value = True
            i += 1
            continue
        if c != '"':
            raise ParseError(
                line_no,
                f"expected STRING (in double quotes) in list {raw!r}",
            )
        end = inner.find('"', i + 1)
        if end < 0:
            raise ParseError(line_no, f"unterminated string in list {raw!r}")
        out.append(inner[i + 1:end])
        i = end + 1
        expect_value = False
    if expect_value:
        # E.g. ``["a", ]`` — comma then nothing.
        raise ParseError(line_no, f"trailing ',' in list {raw!r}")
    return tuple(out)


def _coalesce_list_tokens(
    head_value: str, tokens: list[str], start: int, line_no: int,
) -> tuple[str, int]:
    """Return ``(joined, next_index)`` for a multi-token list value
    whose first piece (the RHS of the ``key=`` token) is *head_value*
    and whose subsequent pieces live at ``tokens[start:]``.

    The lexer doesn't know about list syntax, so a value like
    ``phrases=["a", "b"]`` arrives as the tokens
    ``['phrases=[', '"a"', ',', '"b"', ']']`` — caller has already
    stripped the ``phrases=`` prefix and passes ``[`` as *head_value*.
    This helper walks forward until the closing ``]`` and joins the
    pieces back into a single ``[..]`` string.
    """
    pieces = [head_value]
    depth = head_value.count("[") - head_value.count("]")
    i = start
    while depth > 0:
        if i >= len(tokens):
            raise ParseError(line_no, "unterminated list value")
        pieces.append(tokens[i])
        depth += tokens[i].count("[") - tokens[i].count("]")
        i += 1
    return " ".join(pieces), i


def _parse_kv_pairs(
    tokens: list[str],
    *,
    allowed: frozenset[str],
    line_no: int,
) -> dict[str, KnobValue]:
    """Parse the trailing `k=v` portion of a declaration.

    Each token must contain exactly one `=`. Unknown keys (not in *allowed*)
    raise :class:`ParseError`. Values starting with ``[`` are treated as
    a list-of-strings spanning subsequent tokens (until the matching
    ``]``).
    """
    out: dict[str, KnobValue] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if "=" not in tok:
            raise ParseError(line_no, f"expected key=value, got {tok!r}")
        key, _, value = tok.partition("=")
        if not key:
            raise ParseError(line_no, f"empty key in {tok!r}")
        if key not in allowed:
            raise ParseError(
                line_no,
                f"unknown knob {key!r} (allowed: {sorted(allowed)})",
            )
        if key in out:
            raise ParseError(line_no, f"duplicate knob {key!r}")
        # List value? Coalesce subsequent tokens until ``]``.
        if value.startswith("["):
            joined, next_i = _coalesce_list_tokens(
                value, tokens, i + 1, line_no,
            )
            out[key] = _parse_string_list(joined, line_no)
            i = next_i
            continue
        out[key] = _parse_knob_value(value)
        i += 1
    return out


def _unquote(tok: str, line_no: int) -> str:
    """Strip surrounding double quotes from a STRING token."""
    if len(tok) < 2 or tok[0] != '"' or tok[-1] != '"':
        raise ParseError(line_no, f"expected quoted string, got {tok!r}")
    return tok[1:-1]


# --------------------------------------------------------------------------
# Parser state machine
# --------------------------------------------------------------------------

class _Parser:
    """Two-state machine driven by indent and first-token keyword."""

    def __init__(self, lines: list[Line], *, source_path: str | None) -> None:
        self.lines = lines
        self.source_path = source_path
        self.file = FileAST(source_path=source_path)

        # Tracks the most recently opened block of each shape, so an
        # indented child line can be routed to the right one.
        self._open_block: str | None = None  # "song" | "kit" | "part" | "voice" | "scene" | None
        self._open_kit: KitDecl | None = None
        self._open_part: PartDecl | None = None
        self._open_voice_type: str | None = None
        self._open_scene = None  # SceneAST

    # ------------------------------------------------------------------
    # Top-level driver
    # ------------------------------------------------------------------

    def run(self) -> FileAST:
        for ln in self.lines:
            if ln.indented:
                self._handle_indented(ln)
                continue
            # Indent 0 closes any block that only accepts indented children
            # (kit overrides, part gens). It does NOT close a song block —
            # gen/part/play lines at indent 0 still feed the song.
            if self._open_block in ("kit", "part", "voice", "scene"):
                self._open_block = None
                self._open_kit = None
                self._open_part = None
                self._open_voice_type = None
                self._open_scene = None
            self._handle_top(ln)
        return self.file

    # ------------------------------------------------------------------
    # Top-level (indent 0) line dispatch
    # ------------------------------------------------------------------

    def _handle_top(self, ln: Line) -> None:
        if not ln.tokens:
            return
        kw = ln.tokens[0]
        tail = ln.tokens[1:]
        if kw == "setup":
            self._handle_setup_header(tail, ln.line_no)
        elif kw == "song":
            self._handle_song_header(tail, ln.line_no)
        elif kw == "inst":
            self._handle_inst(tail, ln.line_no)
        elif kw == "kit":
            self._handle_kit_header(tail, ln.line_no)
        elif kw == "backend":
            self._handle_backend(tail, ln.line_no)
        elif kw == "gen":
            self._handle_gen(tail, ln.line_no)
        elif kw == "part":
            self._handle_part_header(tail, ln.line_no)
        elif kw == "voice":
            self._handle_voice_header(tail, ln.line_no)
        elif kw == "scene":
            self._handle_scene_header(tail, ln.line_no)
        elif kw == "lfo":
            self._handle_lfo(tail, ln.line_no)
        elif kw == "play":
            self._handle_play(tail, ln.line_no)
        else:
            raise ParseError(ln.line_no, f"unexpected statement {kw!r}")

    # ------------------------------------------------------------------
    # Block headers
    # ------------------------------------------------------------------

    def _handle_setup_header(self, tail: list[str], line_no: int) -> None:
        if self.file.setup is not None:
            raise ParseError(line_no, "more than one setup block in file")
        if len(tail) != 1:
            raise ParseError(line_no, 'expected: setup "name"')
        name = _unquote(tail[0], line_no)
        self.file.setup = SetupAST(name=name, line=line_no)

    def _handle_backend(self, tail: list[str], line_no: int) -> None:
        if self.file.setup is None:
            raise ParseError(line_no, "backend outside of a setup block")
        if len(tail) != 1:
            raise ParseError(line_no, "expected: backend <surge|external>")
        name = tail[0]
        if name not in ("surge", "external"):
            raise ParseError(
                line_no,
                f"unknown backend {name!r} (allowed: surge, external)",
            )
        if self.file.setup.backend is not None:
            raise ParseError(line_no, "more than one backend directive in setup")
        self.file.setup.backend = name

    def _handle_song_header(self, tail: list[str], line_no: int) -> None:
        if self.file.song is not None:
            raise ParseError(line_no, "more than one song block in file")
        if len(tail) != 1:
            raise ParseError(line_no, 'expected: song "name"')
        name = _unquote(tail[0], line_no)
        self.file.song = SongAST(name=name, line=line_no)
        self._open_block = "song"

    # ------------------------------------------------------------------
    # inst / kit
    # ------------------------------------------------------------------

    def _handle_inst(self, tail: list[str], line_no: int) -> None:
        if self.file.setup is None:
            raise ParseError(line_no, "inst outside of a setup block")
        if not tail:
            raise ParseError(line_no, "inst requires a name")
        name, *rest = tail
        knobs = _parse_kv_pairs(rest, allowed=_INST_KNOBS, line_no=line_no)
        if "ch" not in knobs:
            raise ParseError(line_no, "inst requires ch=<channel>")
        self.file.setup.instruments.append(
            InstDecl(name=name, knobs=knobs, line=line_no)
        )

    def _handle_kit_header(self, tail: list[str], line_no: int) -> None:
        if self.file.setup is None:
            raise ParseError(line_no, "kit outside of a setup block")
        if not tail:
            raise ParseError(line_no, "kit requires a name")
        name, *rest = tail
        knobs = _parse_kv_pairs(rest, allowed=_KIT_KNOBS, line_no=line_no)
        if "ch" not in knobs:
            raise ParseError(line_no, "kit requires ch=<channel>")
        kit = KitDecl(name=name, knobs=knobs, overrides={}, line=line_no)
        self.file.setup.kits.append(kit)
        self._open_block = "kit"
        self._open_kit = kit

    # ------------------------------------------------------------------
    # gen / part / play
    # ------------------------------------------------------------------

    def _handle_gen(self, tail: list[str], line_no: int) -> None:
        if self.file.song is None:
            raise ParseError(line_no, "gen outside of a song block")
        if len(tail) < 3:
            raise ParseError(line_no, "expected: gen <handle> <type> <style> [k=v...]")
        handle, type_, style, *rest = tail
        knobs = _parse_kv_pairs(rest, allowed=_GEN_KNOBS, line_no=line_no)
        self.file.song.gens.append(
            GenDecl(handle=handle, type_=type_, style=style, knobs=knobs, line=line_no)
        )

    def _handle_part_header(self, tail: list[str], line_no: int) -> None:
        if self.file.song is None:
            raise ParseError(line_no, "part outside of a song block")
        if len(tail) < 2:
            raise ParseError(line_no, "expected: part <name> <bars> [k=v...]")
        name, bars_tok, *rest = tail
        # Issue #21: `bars` may be a range `N..M` for probabilistic
        # per-arrangement-instance length. We store the lo/hi here and
        # let the resolver / scheduler pick the actual count later.
        if ".." in bars_tok:
            lo_s, _, hi_s = bars_tok.partition("..")
            try:
                lo = int(lo_s); hi = int(hi_s)
            except ValueError:
                raise ParseError(
                    line_no,
                    f"bars range must be int..int, got {bars_tok!r}",
                ) from None
            if lo < 1 or hi < lo:
                raise ParseError(line_no, f"bars range invalid: {bars_tok!r}")
            # We stash both ends in the AST by encoding the upper bound
            # into a sentinel knob. The PartDecl.bars holds the lower
            # bound; the resolver reads "bars_max" out of the knobs.
            bars = lo
            knobs = _parse_kv_pairs(rest, allowed=_PART_KNOBS, line_no=line_no)
            knobs["bars_max"] = hi
        else:
            try:
                bars = int(bars_tok)
            except ValueError:
                raise ParseError(line_no, f"bars must be an integer, got {bars_tok!r}") from None
            knobs = _parse_kv_pairs(rest, allowed=_PART_KNOBS, line_no=line_no)
        part = PartDecl(name=name, bars=bars, knobs=knobs, line=line_no)
        self.file.song.parts.append(part)
        self._open_block = "part"
        self._open_part = part

    def _handle_scene_header(self, tail: list[str], line_no: int) -> None:
        if self.file.song is None:
            raise ParseError(line_no, "scene block outside of a song block")
        if tail:
            raise ParseError(line_no, "expected: scene (no arguments)")
        if self.file.song.scene is not None:
            raise ParseError(line_no, "more than one scene block in song")
        self.file.song.scene = SceneAST(line=line_no)
        self._open_block = "scene"
        self._open_scene = self.file.song.scene

    def _handle_play(self, tail: list[str], line_no: int) -> None:
        if self.file.song is None:
            raise ParseError(line_no, "play outside of a song block")
        if self.file.song.play is not None:
            raise ParseError(line_no, "more than one play line in song")
        atoms = _parse_arrangement(tail, line_no)
        self.file.song.play = PlayLine(atoms=atoms, line=line_no)

    def _handle_voice_header(self, tail: list[str], line_no: int) -> None:
        if self.file.song is None:
            raise ParseError(line_no, "voice block outside of a song block")
        if len(tail) != 1:
            raise ParseError(line_no, "expected: voice <type>")
        voice_type = tail[0]
        if voice_type in self.file.song.voice_defaults:
            raise ParseError(
                line_no, f"more than one voice block for type {voice_type!r}",
            )
        # Initialise to empty knob dict; indented lines fill it in.
        # Type validity is checked at resolve time so the parser
        # doesn't have to know which generator types are registered.
        self.file.song.voice_defaults[voice_type] = {}
        self._open_block = "voice"
        self._open_voice_type = voice_type

    # ------------------------------------------------------------------
    # Indented (indent > 0) lines — children of the currently open block
    # ------------------------------------------------------------------

    def _handle_indented(self, ln: Line) -> None:
        if self._open_block == "song":
            self._handle_song_attr(ln)
        elif self._open_block == "kit":
            self._handle_kit_override(ln)
        elif self._open_block == "part":
            self._handle_part_gen(ln)
        elif self._open_block == "voice":
            self._handle_voice_attr(ln)
        elif self._open_block == "scene":
            self._handle_scene_entry(ln)
        else:
            raise ParseError(
                ln.line_no, "indented line with no surrounding block"
            )

    def _handle_voice_attr(self, ln: Line) -> None:
        assert self._open_voice_type is not None
        assert self.file.song is not None
        if not ln.tokens:
            return
        # Voice block accepts the same knob set as a song-level gen
        # line. Knob names are validated; values flow through
        # ``_parse_kv_pairs`` so they pick up int / float / str coercion
        # consistently with the rest of the DSL.
        knobs = _parse_kv_pairs(ln.tokens, allowed=_GEN_KNOBS, line_no=ln.line_no)
        existing = self.file.song.voice_defaults[self._open_voice_type]
        for k, v in knobs.items():
            if k in existing:
                raise ParseError(
                    ln.line_no,
                    f"duplicate knob {k!r} in voice {self._open_voice_type!r}",
                )
            existing[k] = v

    def _handle_lfo(self, tail: list[str], line_no: int) -> None:
        """Issue #65 — ``lfo NAME shape=... bars=... [...]`` at top level."""
        if self.file.song is None:
            raise ParseError(line_no, "lfo outside of a song block")
        if not tail:
            raise ParseError(line_no, "lfo requires a name")
        name, *rest = tail
        if any(decl.name == name for decl in self.file.song.lfos):
            raise ParseError(line_no, f"duplicate lfo name {name!r}")
        knobs = _parse_kv_pairs(rest, allowed=_LFO_KNOBS, line_no=line_no)
        if "shape" not in knobs:
            raise ParseError(line_no, "lfo requires shape=<sine|sawtooth|square|pulse|noise>")
        if "bars" not in knobs and "hz" not in knobs:
            raise ParseError(line_no, "lfo requires bars=<N> or hz=<N>")
        self.file.song.lfos.append(
            LfoDecl(name=name, knobs=knobs, line=line_no),
        )

    def _handle_scene_entry(self, ln: Line) -> None:
        assert self._open_scene is not None
        if not ln.tokens:
            return
        scope, *rest = ln.tokens
        if scope == "ch":
            # `ch <N> [k=v...]` — N is the 1-based MIDI channel.
            if not rest:
                raise ParseError(ln.line_no, "expected: ch <channel> [k=v...]")
            ch_tok, *kv_tokens = rest
            try:
                channel = int(ch_tok)
            except ValueError:
                raise ParseError(
                    ln.line_no, f"channel must be int, got {ch_tok!r}",
                ) from None
            if not 1 <= channel <= 16:
                raise ParseError(
                    ln.line_no, f"channel out of 1..16: {channel}",
                )
            knobs = _parse_kv_pairs(
                kv_tokens, allowed=_SCENE_CH_KNOBS, line_no=ln.line_no,
            )
            self._open_scene.entries.append(SceneEntry(
                scope="ch", selector=channel, knobs=knobs, line=ln.line_no,
            ))
        else:
            # Reject unknown scope keywords loudly — scene format is
            # forward-incompatible, and a typo on a future kind (e.g.
            # `surge` once wired) should fail rather than silently no-op.
            raise ParseError(
                ln.line_no,
                f"unknown scene scope {scope!r} (supported: ch)",
            )

    def _handle_song_attr(self, ln: Line) -> None:
        assert self.file.song is not None
        toks = ln.tokens
        if not toks:
            return
        kw = toks[0]
        if kw == "setup":
            if len(toks) != 2:
                raise ParseError(ln.line_no, 'expected: setup "<name-or-path>"')
            self.file.song.setup_ref = _unquote(toks[1], ln.line_no)
        elif kw == "tempo":
            if len(toks) != 2:
                raise ParseError(ln.line_no, "expected: tempo <bpm>")
            try:
                self.file.song.tempo = int(toks[1])
            except ValueError:
                raise ParseError(ln.line_no, f"tempo must be int, got {toks[1]!r}") from None
        elif kw == "key":
            if len(toks) != 2:
                raise ParseError(ln.line_no, "expected: key <name>")
            self.file.song.key = toks[1]
        elif kw == "seed":
            if len(toks) != 2:
                raise ParseError(ln.line_no, "expected: seed <int>")
            try:
                self.file.song.seed = int(toks[1])
            except ValueError:
                raise ParseError(ln.line_no, f"seed must be int, got {toks[1]!r}") from None
        elif kw == "scale":
            if len(toks) != 2:
                raise ParseError(ln.line_no, "expected: scale <name>")
            self.file.song.scale = toks[1]
        elif kw == "meter":
            if len(toks) != 2:
                raise ParseError(ln.line_no, "expected: meter <N/M>")
            self.file.song.meter = toks[1]
        else:
            raise ParseError(ln.line_no, f"unknown song attribute {kw!r}")

    def _handle_kit_override(self, ln: Line) -> None:
        assert self._open_kit is not None
        if len(ln.tokens) != 2:
            raise ParseError(ln.line_no, "expected: <drum-name> <note>")
        name, note_tok = ln.tokens
        try:
            note = int(note_tok)
        except ValueError:
            raise ParseError(ln.line_no, f"note must be int, got {note_tok!r}") from None
        if name in self._open_kit.overrides:
            raise ParseError(ln.line_no, f"duplicate override for {name!r}")
        self._open_kit.overrides[name] = note

    def _handle_part_gen(self, ln: Line) -> None:
        assert self._open_part is not None
        # Indented gen line is one of:
        #   <handle>
        #   <handle> <algorithm>
        #   <handle> <k=v> [<k=v>...]
        #   <handle> <algorithm> <k=v> [<k=v>...]
        # Also accept the LFO ``apply`` form for per-part automation
        # (issue #65):
        #   apply <lfo_name> target="..."
        # Algorithm token (when present) is the first tail token that
        # doesn't contain '='; everything after is parsed as knobs.
        if not ln.tokens:
            raise ParseError(ln.line_no, "empty part-gen line")
        if ln.tokens[0] == "apply":
            self._handle_part_apply(ln)
            return
        handle, *tail = ln.tokens
        algorithm: str | None = None
        kv_tokens: list[str] = []
        for i, tok in enumerate(tail):
            if "=" in tok:
                kv_tokens = tail[i:]
                break
            if algorithm is not None:
                raise ParseError(
                    ln.line_no,
                    f"unexpected token {tok!r} after algorithm — "
                    "knob overrides must use k=v form",
                )
            algorithm = tok
        if algorithm is not None:
            if handle in self._open_part.algorithm_overrides:
                raise ParseError(
                    ln.line_no,
                    f"duplicate algorithm override for {handle!r}",
                )
            self._open_part.algorithm_overrides[handle] = algorithm
        if kv_tokens:
            knobs = _parse_kv_pairs(kv_tokens, allowed=_GEN_KNOBS, line_no=ln.line_no)
            if handle in self._open_part.knob_overrides:
                raise ParseError(
                    ln.line_no,
                    f"duplicate knob overrides for {handle!r} in this part",
                )
            self._open_part.knob_overrides[handle] = knobs
        self._open_part.gens.append(handle)

    def _handle_part_apply(self, ln: Line) -> None:
        """Issue #65 — ``apply <lfo_name> target=...`` inside a part.

        The target reference is bare (not quoted) so the lexer doesn't
        split it on internal ``:`` / ``/`` characters. Form:
        ``apply slow_filter target=midi:ch:2/cc:74``.
        """
        assert self._open_part is not None
        toks = ln.tokens
        if len(toks) < 3:
            raise ParseError(
                ln.line_no,
                "expected: apply <lfo_name> target=<ref>",
            )
        _, lfo_name, *rest = toks
        knobs = _parse_kv_pairs(rest, allowed=frozenset({"target"}), line_no=ln.line_no)
        if "target" not in knobs:
            raise ParseError(ln.line_no, "apply requires target=<ref>")
        target = knobs["target"]
        if not isinstance(target, str):
            raise ParseError(ln.line_no, f"target must be a string, got {target!r}")
        self._open_part.lfo_apply_lines.append((lfo_name, target, ln.line_no))


# --------------------------------------------------------------------------
# Arrangement parsing (play line)
# --------------------------------------------------------------------------

def _parse_arrangement(tokens: list[str], line_no: int) -> list[ArrAtom]:
    """Parse the token stream that follows `play`.

    Supports IDENT, `*` N, `(` … `)`. Returns a list of :class:`ArrAtom`.
    """
    pos = 0

    def fail(msg: str) -> "ParseError":
        return ParseError(line_no, f"play: {msg}")

    def parse_atoms(depth: int) -> list[ArrAtom]:
        nonlocal pos
        out: list[ArrAtom] = []
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == ")":
                if depth == 0:
                    raise fail("unmatched ')'")
                return out
            if tok == "(":
                pos += 1
                inner = parse_atoms(depth + 1)
                if pos >= len(tokens) or tokens[pos] != ")":
                    raise fail("missing ')'")
                pos += 1
                repeat = _maybe_repeat()
                out.append(ArrAtom(group=inner, repeat=repeat))
                continue
            if tok == "*":
                raise fail("'*' without preceding atom")
            # IDENT atom
            ref = tok
            pos += 1
            repeat = _maybe_repeat()
            out.append(ArrAtom(ref=ref, repeat=repeat))
        return out

    def _maybe_repeat() -> int:
        nonlocal pos
        if pos < len(tokens) and tokens[pos] == "*":
            pos += 1
            if pos >= len(tokens):
                raise fail("'*' at end of line — expected an integer")
            try:
                n = int(tokens[pos])
            except ValueError:
                raise fail(f"'*' followed by non-integer {tokens[pos]!r}") from None
            if n < 1:
                raise fail(f"'*{n}' — repeat count must be >= 1")
            pos += 1
            return n
        return 1

    atoms = parse_atoms(depth=0)
    if not atoms:
        raise ParseError(line_no, "play: expected at least one atom")
    return atoms


# --------------------------------------------------------------------------
# Arrangement expansion — useful both at runtime and for tests
# --------------------------------------------------------------------------

def expand_arrangement(atoms: list[ArrAtom]) -> list[str]:
    """Flatten a parsed arrangement into the linear sequence of part names.

    >>> from slackbeatz.dsl.ast import ArrAtom
    >>> expand_arrangement([
    ...     ArrAtom(ref="intro"),
    ...     ArrAtom(group=[ArrAtom(ref="build"), ArrAtom(ref="drop")], repeat=2),
    ... ])
    ['intro', 'build', 'drop', 'build', 'drop']
    """
    out: list[str] = []
    for atom in atoms:
        if atom.ref is not None:
            out.extend([atom.ref] * atom.repeat)
        else:
            inner = expand_arrangement(atom.group)
            for _ in range(atom.repeat):
                out.extend(inner)
    return out
