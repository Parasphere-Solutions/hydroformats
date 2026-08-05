"""HYPACK RAW dialect parser (single-beam survey logging).

Anchors: USGS field-activity metadata (record semantics), the Hydromagic
HYPACK import/export documentation (record inventory + a complete example
file). ``EC2`` is attested by Hydromagic's export list but its field layout
is not anchored by any public source we hold, so it deliberately parses as
UnknownRecord rather than a guess.
"""
from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

from ._dispatch import COMMON_TABLE, ParseFn, _f, _i, dispatch
from .framing import Line, iter_lines
from .records import (
    DeviceOffsets,
    HeaderMisc,
    KinematicTide,
    Quality,
    RawPosition,
    Record,
)


def _parse_raw_position(fields: tuple[str, ...], line: Line) -> Record:
    return RawPosition(
        tag="RAW", device=_i(fields, 0), time=_f(fields, 1), count=_i(fields, 2),
        latitude_raw=_f(fields, 3), longitude_raw=_f(fields, 4),
        altitude=_f(fields, 5), utc=fields[6] if len(fields) > 6 else "",
    )


def _parse_off(fields: tuple[str, ...], line: Line) -> Record:
    return DeviceOffsets(
        tag="OFF", device=_i(fields, 0),
        starboard=_f(fields, 1), forward=_f(fields, 2), vertical=_f(fields, 3),
        yaw=_f(fields, 4), roll=_f(fields, 5), pitch=_f(fields, 6),
        latency=_f(fields, 7),
    )


def _parse_qua(fields: tuple[str, ...], line: Line) -> Record:
    # Real loggers write the integer fields float-formatted ("12.000"),
    # observed in USGS 2014-009-FA data — parse via float, store as int.
    return Quality(
        tag="QUA", device=_i(fields, 0), time=_f(fields, 1), count=_i(fields, 2),
        m=_f(fields, 3), hdop=_f(fields, 4), satellites=int(float(fields[5])),
        mode=int(float(fields[6])), extras=tuple(float(x) for x in fields[7:]),
    )


def _parse_ktc(fields: tuple[str, ...], line: Line) -> Record:
    return KinematicTide(
        tag="KTC", device=_i(fields, 0), time=_f(fields, 1), count=_i(fields, 2),
        ellipsoid_height=_f(fields, 3), local_height=_f(fields, 4),
        undulation=_f(fields, 5), k_value=_f(fields, 6),
        antenna_offset=_f(fields, 7), draft=_f(fields, 8), final_tide=_f(fields, 9),
    )


def _header_misc(tag: str) -> ParseFn:
    def parse(fields: tuple[str, ...], line: Line) -> Record:
        return HeaderMisc(tag=tag, fields=fields)

    return parse


RAW_TABLE: dict[str, ParseFn] = {
    **COMMON_TABLE,
    "RAW": _parse_raw_position,
    "OFF": _parse_off,
    "QUA": _parse_qua,
    "KTC": _parse_ktc,
    # Attested header records carried verbatim (see HeaderMisc docstring).
    "ELL": _header_misc("ELL"),
    "PRO": _header_misc("PRO"),
    "DTM": _header_misc("DTM"),
    "GEO": _header_misc("GEO"),
    "HVU": _header_misc("HVU"),
    "LTP": _header_misc("LTP"),
    # EC2 intentionally absent: attested type, unanchored layout -> Unknown.
}


def parse_raw(source: str | Path | io.TextIOBase) -> Iterator[Record]:
    """Stream records from a HYPACK RAW file. Never raises on content."""
    for line in iter_lines(source):
        yield dispatch(line, RAW_TABLE)
