"""Line framing and tokenizing shared by the RAW and HSX dialects.

Both formats are line-oriented ASCII: a three-character tag, a space, then
space-separated fields. Strings may be double-quoted and may contain spaces
(device names, survey metadata). Files in the wild mix CRLF/LF, contain
blank lines, and occasionally truncated final lines — framing never raises
on those; it yields what it can and lets the dialect layer decide.
"""
from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Line:
    """One framed input line: tag, remainder, and provenance."""

    number: int
    tag: str
    body: str
    raw: str


def iter_lines(source: str | Path | io.TextIOBase) -> Iterator[Line]:
    """Yield framed lines from a path or open text stream.

    Blank lines are skipped. Lines shorter than a tag are yielded with the
    whole content as the tag (the dialect layer treats them as unknown).
    Decoding is latin-1: logger output is ASCII in practice, and latin-1
    never raises, which matters more than symbol fidelity in comments.
    """
    if isinstance(source, (str, Path)):
        with open(source, encoding="latin-1", newline="") as handle:
            yield from _iter_stream(handle)
    else:
        yield from _iter_stream(source)


def _iter_stream(handle: io.TextIOBase) -> Iterator[Line]:
    for number, raw in enumerate(handle, start=1):
        stripped = raw.rstrip("\r\n")
        if not stripped.strip():
            continue
        tag, _, body = stripped.partition(" ")
        yield Line(number=number, tag=tag, body=body, raw=stripped)


def tokenize(body: str) -> tuple[str, ...]:
    """Split a record body into fields, honoring double-quoted strings.

    ``INF "Jane" "Boat 5" "" "Area" 0.0`` -> ('Jane', 'Boat 5', '', 'Area', '0.0')
    Quotes are stripped from quoted fields; unquoted fields split on runs of
    whitespace. An unterminated quote consumes the rest of the line (never
    raises).
    """
    fields: list[str] = []
    i, n = 0, len(body)
    while i < n:
        if body[i].isspace():
            i += 1
            continue
        if body[i] == '"':
            end = body.find('"', i + 1)
            if end == -1:
                fields.append(body[i + 1 :])
                return tuple(fields)
            fields.append(body[i + 1 : end])
            i = end + 1
        else:
            end = i
            while end < n and not body[end].isspace():
                end += 1
            fields.append(body[i:end])
            i = end
    return tuple(fields)
