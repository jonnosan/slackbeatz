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
from slackbeatz.theory.meter import COMMON_TIME, Meter

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
    # Polymeter: gen-level meter override pops out of the knob dict so
    # it doesn't end up in the algorithm's raw `knobs` view.
    meter_raw = knobs.pop("meter", None)
    gen_meter: Meter | None = None
    if meter_raw is not None:
        if not isinstance(meter_raw, str):
            raise ResolveError(gen.line, f"gen {gen.handle!r}: meter must be N/M")
        try:
            gen_meter = Meter.parse(meter_raw)
        except ValueError as e:
            raise ResolveError(gen.line, f"gen {gen.handle!r}: {e}") from None

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
            meter=gen_meter,
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
        meter=gen_meter,
    )


_MODULATION_OFFSETS: dict[str, int] = {
    # Named modulations expressed as a semitone offset to apply to the
    # current tonic. Mode (minor / major suffix) is handled separately.
    "dominant":        7,    # up a perfect 5th
    "subdominant":     5,    # up a perfect 4th
    "fifth_up":        7,
    "fifth_down":     -7,
    "whole_up":        2,
    "whole_down":     -2,
    "half_up":         1,
    "half_down":      -1,
    # Special cases handled inline below: relative_major, relative_minor,
    # parallel_major, parallel_minor.
}


def _resolve_modulation(song_key: str, target: str, part) -> str:
    """Compute a new key string from *song_key* under the named
    *target* modulation. Falls back to song_key for unknown names
    rather than raising — pairing a parser error message with a
    typo'd modulation would surprise users."""
    from slackbeatz.theory.keys import parse_key

    tonic, mode = parse_key(song_key)
    is_minor = mode == "minor"

    if target == "relative_major":
        # Minor → its relative major (= up a m3). Major key stays put.
        if is_minor:
            new_tonic = (tonic + 3) % 12
            return _format_key(new_tonic, "major")
        return song_key
    if target == "relative_minor":
        if not is_minor:
            new_tonic = (tonic - 3) % 12
            return _format_key(new_tonic, "minor")
        return song_key
    if target == "parallel_major":
        return _format_key(tonic, "major")
    if target == "parallel_minor":
        return _format_key(tonic, "minor")

    semitones = _MODULATION_OFFSETS.get(target)
    if semitones is None:
        # Unknown target — fall back to current key.
        return song_key
    new_tonic = (tonic + semitones) % 12
    return _format_key(new_tonic, "minor" if is_minor else "major")


_PITCH_NAMES_SHARP = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _format_key(tonic: int, mode: str) -> str:
    """Format a (tonic, mode) pair back into a key string slackbeatz
    accepts ('Am', 'C', 'F#m', etc.)."""
    name = _PITCH_NAMES_SHARP[tonic % 12]
    if mode == "minor":
        return f"{name}m"
    return name


def _resolve_part(
    part: PartDecl,
    song_tempo: int,
    song_key: str,
    song_meter: Meter,
    known_gen_handles: set[str],
) -> ResolvedPart:
    knobs = dict(part.knobs)
    tempo_raw = knobs.pop("tempo", None)
    key_raw = knobs.pop("key", None)
    role_raw = knobs.pop("role", None)
    seed_raw = knobs.pop("seed", None)
    scale_raw = knobs.pop("scale", None)
    transpose_prob_raw = knobs.pop("transpose_prob", None)
    bars_max_raw = knobs.pop("bars_max", None)  # synthetic from `bars=N..M`
    tension_raw = knobs.pop("tension", None)  # issue #14
    meter_raw = knobs.pop("meter", None)  # time signature override
    modulate_to_raw = knobs.pop("modulate_to", None)  # named modulation

    if tempo_raw is None:
        tempo = song_tempo
    elif isinstance(tempo_raw, int):
        tempo = tempo_raw
    else:
        raise ResolveError(part.line, f"part {part.name!r}: tempo must be int")

    # Key resolution: explicit `key=` wins, then `modulate_to=` is
    # resolved against the song key, then fall back to song key.
    if key_raw is None and modulate_to_raw is None:
        key = song_key
    elif key_raw is not None:
        if not isinstance(key_raw, str):
            raise ResolveError(part.line, f"part {part.name!r}: key must be a name")
        key = key_raw
    else:
        if not isinstance(modulate_to_raw, str):
            raise ResolveError(
                part.line, f"part {part.name!r}: modulate_to must be a name",
            )
        key = _resolve_modulation(song_key, modulate_to_raw, part)

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

    scale_override = scale_raw if isinstance(scale_raw, str) else None
    transpose_prob = 0.0
    if transpose_prob_raw is not None:
        if not isinstance(transpose_prob_raw, (int, float)):
            raise ResolveError(part.line, f"part {part.name!r}: transpose_prob must be a number")
        transpose_prob = float(transpose_prob_raw)
        if not 0.0 <= transpose_prob <= 1.0:
            raise ResolveError(
                part.line, f"part {part.name!r}: transpose_prob must be 0..1, got {transpose_prob}"
            )

    bars_max: int | None = None
    if bars_max_raw is not None:
        if not isinstance(bars_max_raw, int):
            raise ResolveError(part.line, f"part {part.name!r}: bars_max must be int")
        if bars_max_raw < part.bars:
            raise ResolveError(
                part.line,
                f"part {part.name!r}: bars range upper bound {bars_max_raw} < lower {part.bars}",
            )
        bars_max = bars_max_raw

    tension: float | None = None
    if tension_raw is not None:
        if not isinstance(tension_raw, (int, float)):
            raise ResolveError(part.line, f"part {part.name!r}: tension must be a number")
        tension = float(tension_raw)
        if not 0.0 <= tension <= 1.0:
            raise ResolveError(
                part.line, f"part {part.name!r}: tension must be 0..1, got {tension}",
            )

    # Meter: explicit `meter=N/M` on the part wins; else inherit from song.
    if meter_raw is None:
        meter = song_meter
    else:
        if not isinstance(meter_raw, str):
            raise ResolveError(part.line, f"part {part.name!r}: meter must be N/M")
        try:
            meter = Meter.parse(meter_raw)
        except ValueError as e:
            raise ResolveError(part.line, f"part {part.name!r}: {e}") from None

    return ResolvedPart(
        name=part.name,
        bars=part.bars,
        tempo=tempo,
        key=key,
        role=role,
        seed_override=seed_override,
        scale_override=scale_override,
        transpose_prob=transpose_prob,
        bars_max=bars_max,
        tension=tension,
        meter=meter,
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
    # Song-level meter — parsed from "N/M" string, default 4/4.
    if song.meter is None:
        song_meter = COMMON_TIME
    else:
        try:
            song_meter = Meter.parse(song.meter)
        except ValueError as e:
            raise ResolveError(song.line, str(e)) from None

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
        parts[p.name] = _resolve_part(p, tempo, key, song_meter, set(gens))

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
        scale_override=song.scale,
        meter=song_meter,
    )
