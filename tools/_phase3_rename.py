"""One-shot rename for generator-refactor Phase 3 (#50).

Renames every per-style generator file from style-based to
algorithm-based names, updates every cross-cutting table that
keyed off the old style names, and rewrites the example .sb
files. After the run, the byte-identical MIDI test in
``tests/test_byte_identical_after_refactor.py`` should still pass
because the registry key is just a string — the same algorithm
class is now reachable under a new name, and every reference is
updated atomically.

Usage:  .venv/bin/python tools/_phase3_rename.py

This script is single-use (the rename only happens once). Kept in
``tools/`` for posterity but not run from any test or CI.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Repository root — script lives in ``tools/``.
ROOT = Path(__file__).resolve().parent.parent


# (type, old_style) → new_algorithm. Matches the table in the
# approved plan (~/.claude/plans/maybe-the-effect-i-cozy-piglet.md).
# Renames where the leading char would be a digit get spelled out
# ("two_step" rather than "2step") so Python class / module names
# stay valid; the registry key is the string itself.
RENAMES: dict[tuple[str, str], str] = {
    # rhythm
    ("rhythm", "euclid"):         "euclid_drums",
    ("rhythm", "acid"):           "four_floor_house",
    ("rhythm", "deep_techno"):    "four_floor_deep",
    ("rhythm", "dub_techno"):     "four_floor_dub",
    ("rhythm", "drum_and_bass"):  "breakbeat",
    ("rhythm", "garage"):         "two_step",
    ("rhythm", "lofi"):           "dusty_swing",
    ("rhythm", "psytrance"):      "gallop_kick",
    ("rhythm", "vaporwave"):      "slow_kick",
    # bass
    ("bass", "euclid"):           "rolling",
    ("bass", "acid"):             "acid_303",
    ("bass", "deep_techno"):      "subdrone",
    ("bass", "dub_techno"):       "sustain_drone",
    ("bass", "drum_and_bass"):    "reese",
    ("bass", "garage"):           "two_step_sub",
    ("bass", "lofi"):             "acoustic_walk",
    ("bass", "psytrance"):        "gallop",
    ("bass", "vaporwave"):        "mellow_pick",
    # melody
    ("melody", "euclid"):         "euclid_riff",
    ("melody", "acid"):           "acid_stab",
    ("melody", "deep_techno"):    "sparse_pad_lead",
    ("melody", "dub_techno"):     "distant_lead",
    ("melody", "drum_and_bass"):  "atmos_lead",
    ("melody", "garage"):         "vocal_chop",
    ("melody", "lofi"):           "rhodes_phrase",
    ("melody", "psytrance"):      "psy_lead",
    ("melody", "vaporwave"):      "lazy_sax",
    # chords
    ("chords", "euclid"):         "triad_sustain",
    ("chords", "acid"):           "sustained_dyad",
    ("chords", "deep_techno"):    "pad_drift",
    ("chords", "dub_techno"):     "offbeat_stab",
    ("chords", "drum_and_bass"):  "atmos_pad",
    ("chords", "garage"):         "wurli_chop",
    ("chords", "lofi"):           "rhodes_chord",
    ("chords", "psytrance"):      "psy_swell",
    ("chords", "vaporwave"):      "arp_walk",
}

# Candy stays at 9 files (see #49 scope note); rename them anyway
# so the registry key matches the file name + algorithm naming
# convention.
CANDY_RENAMES: dict[tuple[str, str], str] = {
    ("candy", "euclid"):          "euclid_riser",
    ("candy", "acid"):             "acid_sweep",
    ("candy", "deep_techno"):      "slow_lfo",
    ("candy", "dub_techno"):       "drone_lfo",
    ("candy", "drum_and_bass"):    "atmos_lfo",
    ("candy", "garage"):           "minimal_lfo",
    ("candy", "lofi"):             "crackle_lfo",
    ("candy", "psytrance"):        "psy_sweep",
    ("candy", "vaporwave"):        "bell_lfo",
}

ALL_RENAMES = {**RENAMES, **CANDY_RENAMES}


def _snake_to_camel(s: str) -> str:
    """``four_floor_house`` → ``FourFloorHouse``."""
    return "".join(part.capitalize() for part in s.split("_"))


def _old_class_name(type_: str, old_style: str) -> str:
    """Match the per-file class name we currently ship.

    The existing files use ``<Type><Style>`` PascalCase. For
    ``("bass", "psytrance")`` the class is ``BassPsytrance``;
    for ``("bass", "drum_and_bass")`` it's ``BassDrumAndBass``.
    """
    return _snake_to_camel(type_) + _snake_to_camel(old_style)


def _new_class_name(type_: str, new_algo: str) -> str:
    return _snake_to_camel(type_) + _snake_to_camel(new_algo)


def rename_generator_files() -> None:
    """Phase A: rename each per-style file + update its class /
    registry key in place."""
    for (type_, old_style), new_algo in ALL_RENAMES.items():
        old_path = ROOT / "slackbeatz" / "generators" / type_ / f"{old_style}.py"
        new_path = ROOT / "slackbeatz" / "generators" / type_ / f"{new_algo}.py"
        if not old_path.is_file():
            print(f"  skip {old_path.relative_to(ROOT)} (already renamed?)")
            continue
        text = old_path.read_text()
        # Update the class name + registry key inside the file.
        old_cls = _old_class_name(type_, old_style)
        new_cls = _new_class_name(type_, new_algo)
        text = text.replace(f"class {old_cls}", f"class {new_cls}")
        text = text.replace(
            f'@register_generator("{type_}", "{old_style}")',
            f'@register_generator("{type_}", "{new_algo}")',
        )
        new_path.write_text(text)
        old_path.unlink()
        print(f"  {old_path.relative_to(ROOT)} → {new_path.relative_to(ROOT)}")


def rewrite_init_imports() -> None:
    """Phase B: each per-type __init__.py imports its style
    modules — rewrite the import list to use the new names."""
    for type_ in ("rhythm", "bass", "melody", "chords", "candy"):
        init = ROOT / "slackbeatz" / "generators" / type_ / "__init__.py"
        text = init.read_text()
        for (t, old), new in ALL_RENAMES.items():
            if t != type_:
                continue
            # Match `from . import ... old ...` shapes.
            text = re.sub(
                rf"\b{re.escape(old)}\b",
                new,
                text,
            )
        init.write_text(text)
        print(f"  rewrote {init.relative_to(ROOT)}")


def rewrite_tuple_keys() -> None:
    """Phase C: cross-cutting tables keyed by ``(type, style)`` —
    rewrite each entry to ``(type, algorithm)``.

    Touched files: defaults.py, compose.py (_STYLE_PROFILES,
    style-conditional flair branches), surge_host.py
    (_STYLE_PATCH_FOR_ROLE), engine/midifile.py
    (_GM_PROGRAM_DEFAULTS).
    """
    files = [
        ROOT / "slackbeatz" / "generators" / "defaults.py",
        ROOT / "slackbeatz" / "compose.py",
        ROOT / "slackbeatz" / "surge_host.py",
        ROOT / "slackbeatz" / "engine" / "midifile.py",
    ]
    # `_STYLE_PATCH_FOR_ROLE` is keyed by ROLE (lead / bass / pad /
    # candy / sub) not type, but the second element is still the
    # old style name. So we also need to map (role, style) → (role,
    # algorithm) for the role table. Derive the role mapping from
    # gen-type-to-role: bass→bass, melody→lead, chords→pad,
    # candy→candy, subbass→sub. rhythm has no role surface there.
    type_to_role = {
        "bass": "bass",
        "melody": "lead",
        "chords": "pad",
        "candy": "candy",
    }

    for path in files:
        text = path.read_text()
        # Rewrite (type, style) pairs.
        for (type_, old_style), new_algo in ALL_RENAMES.items():
            text = text.replace(
                f'("{type_}", "{old_style}")',
                f'("{type_}", "{new_algo}")',
            )
            # Also rewrite (role, style) for _STYLE_PATCH_FOR_ROLE.
            role = type_to_role.get(type_)
            if role is not None and path.name == "surge_host.py":
                text = text.replace(
                    f'("{role}", "{old_style}")',
                    f'("{role}", "{new_algo}")',
                )
        # In compose.py, style-conditional flair branches compare
        # `gen_algorithm == "psytrance"` etc. Update those to the
        # renamed key. NOTE: we only rewrite the ones the composer
        # actually references — confirmed today: psytrance bass burble,
        # vaporwave chords arp_prob.
        if path.name == "compose.py":
            text = text.replace(
                'gen_algorithm == "psytrance"',
                f'gen_algorithm == "{ALL_RENAMES[("bass", "psytrance")]}"',
            )
            text = text.replace(
                'gen_algorithm == "vaporwave"',
                f'gen_algorithm == "{ALL_RENAMES[("chords", "vaporwave")]}"',
            )
        path.write_text(text)
        print(f"  rewrote {path.relative_to(ROOT)}")


def rewrite_style_profiles_gens() -> None:
    """Phase D: update the GenSpec algorithm column in compose.py's
    _STYLE_PROFILES dict.

    Today every entry looks like:
      GenSpec("kick", "rhythm", "psytrance"),
    After rename:
      GenSpec("kick", "rhythm", "gallop_kick"),

    The third string literal is the algorithm name; we match the
    GenSpec("…", "<type>", "<old_style>") shape and rewrite.
    """
    path = ROOT / "slackbeatz" / "compose.py"
    text = path.read_text()
    # Regex: GenSpec( "handle", "<type>", "<old_style>" ... )
    pattern = re.compile(
        r'(GenSpec\(\s*"[^"]+"\s*,\s*"(\w+)"\s*,\s*")(\w+)(")'
    )
    def _sub(m):
        prefix = m.group(1)
        type_ = m.group(2)
        old = m.group(3)
        suffix = m.group(4)
        new = ALL_RENAMES.get((type_, old), old)
        return f"{prefix}{new}{suffix}"
    new_text = pattern.sub(_sub, text)
    path.write_text(new_text)
    print(f"  rewrote GenSpec entries in {path.relative_to(ROOT)}")


def rewrite_example_sb_files() -> None:
    """Phase E: rewrite gen lines in every .sb example.

    Each line of the form:
      gen <handle> <type> <old_style> [knobs...]
    becomes:
      gen <handle> <type> <new_algorithm> [knobs...]
    """
    examples = ROOT / "examples"
    rewritten = 0
    for sb in examples.rglob("*.sb"):
        lines = sb.read_text().splitlines(keepends=True)
        out: list[str] = []
        changed = False
        for line in lines:
            stripped = line.lstrip()
            if not stripped.startswith("gen "):
                out.append(line)
                continue
            # gen <handle> <type> <style> [knobs...]
            indent = line[: len(line) - len(stripped)]
            tokens = stripped.split()
            if len(tokens) < 4:
                out.append(line)
                continue
            _, handle, type_, style = tokens[0], tokens[1], tokens[2], tokens[3]
            new_algo = ALL_RENAMES.get((type_, style))
            if new_algo is None:
                out.append(line)
                continue
            # Reconstruct the line preserving original spacing where
            # possible — the existing example files line up tokens in
            # neat columns. Simplest robust approach: split on
            # whitespace, rejoin with single spaces. Knob ordering is
            # preserved.
            tokens[3] = new_algo
            out.append(indent + " ".join(tokens) + "\n")
            changed = True
        if changed:
            sb.write_text("".join(out))
            rewritten += 1
            print(f"  rewrote {sb.relative_to(ROOT)}")
    print(f"  rewrote {rewritten} example .sb file(s)")


def main() -> int:
    print("Phase A: rename per-style generator files…")
    rename_generator_files()
    print("Phase B: rewrite per-type __init__.py imports…")
    rewrite_init_imports()
    print("Phase C: rewrite (type, style) tuple keys in cross-cutting tables…")
    rewrite_tuple_keys()
    print("Phase D: rewrite GenSpec algorithm column in _STYLE_PROFILES…")
    rewrite_style_profiles_gens()
    print("Phase E: rewrite example .sb gen lines…")
    rewrite_example_sb_files()
    print("Done. Run `pytest tests/ -q` to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
