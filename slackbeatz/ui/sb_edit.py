"""Line-level mutations on ``.sb`` source files.

The LFO editor (and future structural editors — voice add/remove,
arrangement edit) round-trip through these helpers so user changes
show up directly in the .sb file the Player loaded. The Player
re-resolves from disk on the next ``_resolve_current`` call,
picking up the edits seamlessly.

Why line-level rather than parse-and-emit? Two reasons:

1. The .sb file may carry user-meaningful formatting (blank lines,
   comments) we shouldn't blow away with a full round-trip.
2. The parser is one-way today (text → AST); there's no
   AST → canonical-text serialiser. Hand-rolling line ops keeps
   the diff small + reviewable.

All functions take ``path: Path`` and mutate in place. They raise
:class:`SbEditError` on malformed input (missing song, duplicate
name, etc.) so the caller can surface a clean dialog.
"""

from __future__ import annotations

import re
from pathlib import Path


class SbEditError(RuntimeError):
    """Raised when an .sb mutation can't be applied cleanly."""


def _read(path: Path) -> list[str]:
    return path.read_text().splitlines()


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


def _part_line_re() -> re.Pattern:
    return re.compile(r"^part\s+")


def _lfo_line_re(name: str | None = None) -> re.Pattern:
    if name is None:
        return re.compile(r"^lfo\s+")
    return re.compile(rf"^lfo\s+{re.escape(name)}(\s|$)")


def _apply_line_re(lfo_name: str | None = None) -> re.Pattern:
    if lfo_name is None:
        return re.compile(r"^\s*apply\s+")
    return re.compile(rf"^\s*apply\s+{re.escape(lfo_name)}(\s|$)")


def add_lfo(path: Path, name: str, knobs: dict[str, str]) -> None:
    """Insert ``lfo NAME k=v...`` before the first ``part`` line.

    Raises if a lfo with the same name already exists. Knob values
    are written verbatim — caller is responsible for stringifying
    them appropriately (e.g. floats with the expected precision).
    """
    if not name or " " in name:
        raise SbEditError(f"invalid lfo name {name!r}")
    lines = _read(path)
    existing_re = _lfo_line_re(name)
    for ln in lines:
        if existing_re.match(ln):
            raise SbEditError(f"lfo {name!r} already exists")

    knob_str = " ".join(f"{k}={v}" for k, v in knobs.items())
    new_line = f"lfo {name} {knob_str}".rstrip()

    part_re = _part_line_re()
    lfo_re = _lfo_line_re()
    last_lfo_idx = -1
    first_part_idx = -1
    for i, ln in enumerate(lines):
        if first_part_idx < 0 and part_re.match(ln):
            first_part_idx = i
        if lfo_re.match(ln):
            last_lfo_idx = i
    if last_lfo_idx >= 0:
        lines.insert(last_lfo_idx + 1, new_line)
    elif first_part_idx >= 0:
        insertion = [new_line, ""]
        if first_part_idx > 0 and lines[first_part_idx - 1].strip() != "":
            insertion = ["", new_line, ""]
        lines[first_part_idx:first_part_idx] = insertion
    else:
        lines.append(new_line)
    _write(path, lines)


def update_lfo(path: Path, name: str, knobs: dict[str, str]) -> None:
    """Rewrite the ``lfo NAME ...`` line with new knobs.

    Preserves position in the file. Raises if the LFO doesn't exist.
    """
    lines = _read(path)
    lfo_re = _lfo_line_re(name)
    knob_str = " ".join(f"{k}={v}" for k, v in knobs.items())
    new_line = f"lfo {name} {knob_str}".rstrip()
    found = False
    for i, ln in enumerate(lines):
        if lfo_re.match(ln):
            lines[i] = new_line
            found = True
            break
    if not found:
        raise SbEditError(f"no lfo {name!r} to update")
    _write(path, lines)


def remove_lfo(path: Path, name: str) -> None:
    """Remove the ``lfo NAME ...`` declaration AND every
    ``apply NAME ...`` line that references it.

    Silent no-op if the LFO doesn't exist.
    """
    lines = _read(path)
    lfo_re = _lfo_line_re(name)
    apply_re = _apply_line_re(name)
    kept = [ln for ln in lines if not (lfo_re.match(ln) or apply_re.match(ln))]
    if kept == lines:
        return
    _write(path, kept)


def add_apply(
    path: Path, part_name: str, lfo_name: str, target_ref: str,
) -> None:
    """Insert ``apply LFO target=REF`` inside the named part block.

    Block runs from ``part NAME`` header through the next blank line
    or next top-level keyword. New ``apply`` is added at the END of
    the part body with indent matching existing lines.

    Raises :class:`SbEditError` if the part doesn't exist.
    """
    lines = _read(path)
    part_header_re = re.compile(rf"^part\s+{re.escape(part_name)}(\s|$)")
    start = None
    for i, ln in enumerate(lines):
        if part_header_re.match(ln):
            start = i
            break
    if start is None:
        raise SbEditError(f"no such part {part_name!r}")

    end = len(lines)
    indent_re = re.compile(r"^\s+\S")
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.strip() == "":
            continue
        if not indent_re.match(ln):
            end = i
            break

    indent = "  "
    for i in range(start + 1, end):
        m = re.match(r"^(\s+)", lines[i])
        if m:
            indent = m.group(1)
            break

    new_line = f"{indent}apply {lfo_name} target={target_ref}"
    insert_idx = end
    while insert_idx > start + 1 and lines[insert_idx - 1].strip() == "":
        insert_idx -= 1
    lines.insert(insert_idx, new_line)
    _write(path, lines)


def remove_apply(path: Path, part_name: str, lfo_name: str) -> None:
    """Remove the ``apply LFO ...`` line(s) for *lfo_name* inside
    *part_name*. Silent no-op when none match.
    """
    lines = _read(path)
    part_header_re = re.compile(rf"^part\s+{re.escape(part_name)}(\s|$)")
    apply_re = _apply_line_re(lfo_name)
    indent_re = re.compile(r"^\s+\S")
    in_part = False
    out: list[str] = []
    for ln in lines:
        if part_header_re.match(ln):
            in_part = True
            out.append(ln)
            continue
        if in_part:
            if ln.strip() != "" and not indent_re.match(ln):
                in_part = False
            elif apply_re.match(ln):
                continue
        out.append(ln)
    if out != lines:
        _write(path, out)
