"""Line-based tokenizer for `.sb` files.

The DSL is line-oriented: each non-blank, non-comment line is one logical
statement. Indentation is significant only as "this line belongs to the
block above" — we collapse it to a boolean (indented or not) since the
grammar never nests more than one level deep.

Output is a stream of :class:`Line` records: line number, indented flag,
and a list of token strings. The parser does the keyword dispatch on the
first token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Line:
    """One logical line of source, post-strip."""

    line_no: int  # 1-based, matches editor display
    indented: bool  # True if this line had leading whitespace
    tokens: list[str]


class LexError(Exception):
    """Raised on malformed input the tokenizer can't recover from."""


# A `play` line uses `(`, `)`, and `*` as structural characters. We pad them
# with spaces before splitting on whitespace so they become standalone tokens
# (and so things like `drop*2` come out as ["drop", "*", "2"]).
_STRUCT_CHARS = re.compile(r"([()*])")


def _split_preserving_strings(s: str) -> list[str]:
    """Split *s* on whitespace, keeping `"..."` substrings intact.

    Only double quotes are honoured. Backslash escapes are not supported (we
    don't need them — names are short and ASCII).
    """
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c == '"':
            end = s.find('"', i + 1)
            if end < 0:
                raise LexError(f"unterminated string starting at column {i + 1}")
            out.append(s[i : end + 1])
            i = end + 1
            continue
        # Non-string token — read up to next whitespace.
        j = i
        while j < n and not s[j].isspace() and s[j] != '"':
            j += 1
        out.append(s[i:j])
        i = j
    return out


def tokenize(text: str) -> Iterable[Line]:
    """Yield :class:`Line` records for non-blank, non-comment lines of *text*."""

    for line_no, raw in enumerate(text.splitlines(), start=1):
        # Strip `#` comments (outside of strings — we don't have `#` in strings
        # in practice, so a naive split is fine for v1).
        if "#" in raw:
            # Find an unquoted `#`.
            in_str = False
            cut = -1
            for idx, ch in enumerate(raw):
                if ch == '"':
                    in_str = not in_str
                elif ch == "#" and not in_str:
                    cut = idx
                    break
            if cut >= 0:
                raw = raw[:cut]

        stripped = raw.strip()
        if not stripped:
            continue

        indented = bool(raw) and raw[0] in (" ", "\t")

        # Pad structural chars so `(build drop)*2` tokenises cleanly.
        padded = _STRUCT_CHARS.sub(r" \1 ", stripped)

        try:
            tokens = _split_preserving_strings(padded)
        except LexError as exc:
            raise LexError(f"line {line_no}: {exc}") from None

        yield Line(line_no=line_no, indented=indented, tokens=tokens)


def tokenize_file(path: str | Path) -> list[Line]:
    """Read *path* and return the tokenized lines as a list."""
    text = Path(path).read_text(encoding="utf-8")
    return list(tokenize(text))
