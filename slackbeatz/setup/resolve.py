"""Bind a parsed song against a setup to produce a :class:`ResolvedSong`.

The resolver does three things:

1. **Generator binding** — each ``gen`` line resolves to either an
   :class:`Instrument` or a :class:`Kit`. The handle is matched against
   the setup unless an explicit ``inst=`` / ``kit=`` knob overrides;
   raw ``ch=`` (and ``note=`` for ``rhythm``) act as a sketch-mode
   fallback when no setup is supplied or the handle isn't in it.

2. **Type-checking** — ``rhythm`` requires a one-shot ``Instrument`` (i.e.
   ``note is not None``), pitched types (``bass``/``melody``/``chords``/
   ``candy``) require a pitched ``Instrument`` (``note is None``), and
   ``drums`` requires a ``Kit``. Mismatches surface here, not in the
   middle of playback.

3. **Defaulting** — applies song-wide ``tempo`` / ``key`` / ``seed`` and
   resolves part-level overrides against them. Arrangement is expanded
   from ``(group)*N`` form into a flat list of part names.
"""

from __future__ import annotations

from slackbeatz.dsl.ast import GenDecl, PartDecl, SongAST
from slackbeatz.dsl.parser import expand_arrangement
from slackbeatz.drums.presets import preset_map
from slackbeatz.model.song import ResolvedGen, ResolvedPart, ResolvedSong

from .model import Instrument, Kit, Setup

_PITCHED_TYPES = {"bass", "melody", "chords", "candy"}
_KNOWN_TYPES = {"rhythm", "drums"} | _PITCHED_TYPES


class ResolveError(Exception):
    """Raised when a song can't be bound to a setup."""

    def __init__(self, line_no: int, msg: str) -> None:
        super().__init__(f"line {line_no}: {msg}")
        self.line_no = line_no


# Default fallbacks when the song doesn't set tempo / key.
_DEFAULT_TEMPO = 120
_DEFAULT_KEY = "Am"


# --------------------------------------------------------------------------
# Per-element resolvers
# --------------------------------------------------------------------------

def _resolve_gen(gen: GenDecl, setup: Setup) -> ResolvedGen:
    if gen.type_ not in _KNOWN_TYPES:
        raise ResolveError(
            gen.line,
            f"gen {gen.handle!r}: unknown type {gen.type_!r} "
            f"(known: {sorted(_KNOWN_TYPES)})",
        )

    knobs = dict(gen.knobs)
    inst_override = knobs.pop("inst", None)
    kit_override = knobs.pop("kit", None)
    raw_ch = knobs.pop("ch", None)
    raw_note = knobs.pop("note", None)

    # Drums type: bind to a Kit.
    if gen.type_ == "drums":
        if inst_override is not None:
            raise ResolveError(
                gen.line,
                f"drums gen {gen.handle!r}: use kit= not inst=",
            )
        target = str(kit_override) if kit_override is not None else gen.handle
        kit = setup.kits.get(target)
        if kit is None:
            if raw_ch is not None:
                if not isinstance(raw_ch, int):
                    raise ResolveError(
                        gen.line,
                        f"drums gen {gen.handle!r}: ch= must be int",
                    )
                kit = Kit(name=target, channel=raw_ch, drum_notes=preset_map("gm"))
            else:
                raise ResolveError(
                    gen.line,
                    f"drums gen {gen.handle!r}: no kit named {target!r} in setup "
                    f"(available: {sorted(setup.kits)}) and no ch= fallback",
                )
        return ResolvedGen(
            handle=gen.handle,
            type_=gen.type_,
            style=gen.style,
            knobs=knobs,
            instrument=None,
            kit=kit,
        )

    # Non-drums: bind to an Instrument.
    if kit_override is not None:
        raise ResolveError(
            gen.line,
            f"{gen.type_} gen {gen.handle!r}: kit= only applies to drums type",
        )
    target = str(inst_override) if inst_override is not None else gen.handle
    inst = setup.instruments.get(target)
    if inst is None:
        if raw_ch is not None:
            if not isinstance(raw_ch, int):
                raise ResolveError(
                    gen.line,
                    f"gen {gen.handle!r}: ch= must be int",
                )
            note: int | None = None
            if gen.type_ == "rhythm":
                if raw_note is None:
                    raise ResolveError(
                        gen.line,
                        f"rhythm gen {gen.handle!r}: inline fallback needs both ch= "
                        "and note=",
                    )
                if not isinstance(raw_note, int):
                    raise ResolveError(
                        gen.line,
                        f"rhythm gen {gen.handle!r}: note= must be int",
                    )
                note = raw_note
            inst = Instrument(name=target, channel=raw_ch, note=note)
        else:
            raise ResolveError(
                gen.line,
                f"gen {gen.handle!r}: no instrument named {target!r} in setup "
                f"(available: {sorted(setup.instruments)}) and no ch= fallback",
            )

    # Type-check the resolved instrument shape.
    if gen.type_ == "rhythm" and inst.is_pitched:
        raise ResolveError(
            gen.line,
            f"rhythm gen {gen.handle!r}: instrument {inst.name!r} is pitched "
            "(no note=); rhythm gens need a one-shot drum voice",
        )
    if gen.type_ in _PITCHED_TYPES and inst.is_drum:
        raise ResolveError(
            gen.line,
            f"{gen.type_} gen {gen.handle!r}: instrument {inst.name!r} is a "
            "one-shot drum (has note=); pitched gens need a pitched instrument",
        )

    return ResolvedGen(
        handle=gen.handle,
        type_=gen.type_,
        style=gen.style,
        knobs=knobs,
        instrument=inst,
        kit=None,
    )


def _resolve_part(
    part: PartDecl,
    song_tempo: int,
    song_key: str,
    known_gen_handles: set[str],
) -> ResolvedPart:
    knobs = dict(part.knobs)
    tempo_raw = knobs.pop("tempo", None)
    key_raw = knobs.pop("key", None)
    role_raw = knobs.pop("role", None)
    seed_raw = knobs.pop("seed", None)

    if tempo_raw is None:
        tempo = song_tempo
    elif isinstance(tempo_raw, int):
        tempo = tempo_raw
    else:
        raise ResolveError(part.line, f"part {part.name!r}: tempo must be int")

    if key_raw is None:
        key = song_key
    elif isinstance(key_raw, str):
        key = key_raw
    else:
        raise ResolveError(part.line, f"part {part.name!r}: key must be a name")

    role = role_raw if isinstance(role_raw, str) else part.name

    if seed_raw is None:
        seed_override: int | None = None
    elif isinstance(seed_raw, int):
        seed_override = seed_raw
    else:
        raise ResolveError(part.line, f"part {part.name!r}: seed must be int")

    # All listed gens must exist.
    for h in part.gens:
        if h not in known_gen_handles:
            raise ResolveError(
                part.line,
                f"part {part.name!r}: gen {h!r} not declared at song level",
            )

    return ResolvedPart(
        name=part.name,
        bars=part.bars,
        tempo=tempo,
        key=key,
        role=role,
        seed_override=seed_override,
        gen_handles=list(part.gens),
    )


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def resolve_song(
    song: SongAST,
    setup: Setup,
    *,
    cli_seed: int = 0,
) -> ResolvedSong:
    """Build a :class:`ResolvedSong` from a parsed song and a loaded setup."""

    # Song-wide defaults / seed resolution.
    tempo = song.tempo if song.tempo is not None else _DEFAULT_TEMPO
    if not 1 <= tempo <= 999:
        raise ResolveError(song.line, f"song tempo {tempo} out of 1..999")
    key = song.key if song.key is not None else _DEFAULT_KEY
    base_seed = song.seed if song.seed is not None else cli_seed

    # Gens — duplicates rejected.
    gens: dict[str, ResolvedGen] = {}
    for g in song.gens:
        if g.handle in gens:
            raise ResolveError(g.line, f"duplicate gen handle {g.handle!r}")
        gens[g.handle] = _resolve_gen(g, setup)

    # Parts — duplicates rejected.
    parts: dict[str, ResolvedPart] = {}
    for p in song.parts:
        if p.name in parts:
            raise ResolveError(p.line, f"duplicate part name {p.name!r}")
        parts[p.name] = _resolve_part(p, tempo, key, set(gens))

    # Arrangement — must exist and reference only declared parts.
    if song.play is None:
        raise ResolveError(song.line, "song has no play line")
    arrangement = expand_arrangement(song.play.atoms)
    if not arrangement:
        raise ResolveError(song.play.line, "play line expanded to nothing")
    for part_name in arrangement:
        if part_name not in parts:
            raise ResolveError(
                song.play.line, f"play references undeclared part {part_name!r}"
            )

    return ResolvedSong(
        name=song.name,
        setup=setup,
        tempo=tempo,
        key=key,
        seed=base_seed,
        gens=gens,
        parts=parts,
        arrangement=arrangement,
    )
