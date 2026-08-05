"""Shared dispatch helpers used by both dialect parsers.

A parse function takes tokenized fields and returns a Record; the dispatch
wrapper turns any failure into a MalformedRecord instead of an exception,
so a single bad line never kills a survey file.
"""
from __future__ import annotations

from collections.abc import Callable

from .framing import Line, tokenize
from .records import (
    Attitude,
    Comment,
    Device,
    Echosounding,
    EndOfHeader,
    EndOfLine,
    FileType,
    FixMark,
    Heading,
    MalformedRecord,
    Message,
    PlannedLine,
    PlannedLineName,
    PlannedLineStart,
    PlannedWaypoint,
    Position,
    Record,
    SurveyInfo,
    Tide,
    TimeDate,
    UnknownRecord,
    Version,
)

ParseFn = Callable[[tuple[str, ...], Line], Record]


def dispatch(line: Line, table: dict[str, ParseFn]) -> Record:
    """Parse one framed line via the dialect's table; degrade, never raise."""
    parse = table.get(line.tag)
    fields = tokenize(line.body)
    if parse is None:
        return UnknownRecord(tag=line.tag, fields=fields, line_number=line.number)
    try:
        return parse(fields, line)
    except (ValueError, IndexError) as error:
        return MalformedRecord(
            tag=line.tag, fields=fields, error=str(error), line_number=line.number
        )


def _f(fields: tuple[str, ...], index: int) -> float:
    return float(fields[index])


def _i(fields: tuple[str, ...], index: int) -> int:
    return int(fields[index])


# ---- parsers for records whose shape is identical in both dialects ----


def parse_ftp(fields: tuple[str, ...], line: Line) -> Record:
    return FileType(tag="FTP", value=line.body.strip())


def parse_ver(fields: tuple[str, ...], line: Line) -> Record:
    return Version(tag="VER", value=line.body.strip())


def parse_inf(fields: tuple[str, ...], line: Line) -> Record:
    trailing = [float(x) for x in fields[4:7]]
    trailing += [None] * (3 - len(trailing))  # type: ignore[list-item]
    return SurveyInfo(
        tag="INF",
        surveyor=fields[0] if len(fields) > 0 else "",
        boat=fields[1] if len(fields) > 1 else "",
        project=fields[2] if len(fields) > 2 else "",
        area=fields[3] if len(fields) > 3 else "",
        tide_correction=trailing[0],
        draft_correction=trailing[1],
        sound_velocity=trailing[2],
    )


def parse_tnd(fields: tuple[str, ...], line: Line) -> Record:
    hh, mm, ss = (int(part) for part in fields[0].split(":"))
    month, day, year = (int(part) for part in fields[1].split("/"))
    return TimeDate(
        tag="TND", hour=hh, minute=mm, second=ss,
        month=month, day=day, year=year, extras=fields[2:],
    )


def parse_dev(fields: tuple[str, ...], line: Line) -> Record:
    return Device(
        tag="DEV", device=_i(fields, 0), capability=_i(fields, 1),
        name=fields[2], extras=fields[3:],
    )


def parse_pos(fields: tuple[str, ...], line: Line) -> Record:
    return Position(
        tag="POS", device=_i(fields, 0), time=_f(fields, 1),
        x=_f(fields, 2), y=_f(fields, 3),
    )


def parse_ec1(fields: tuple[str, ...], line: Line) -> Record:
    return Echosounding(
        tag="EC1", device=_i(fields, 0), time=_f(fields, 1), depth=_f(fields, 2)
    )


def parse_gyr(fields: tuple[str, ...], line: Line) -> Record:
    return Heading(
        tag="GYR", device=_i(fields, 0), time=_f(fields, 1), heading=_f(fields, 2)
    )


def parse_hcp(fields: tuple[str, ...], line: Line) -> Record:
    return Attitude(
        tag="HCP", device=_i(fields, 0), time=_f(fields, 1),
        heave=_f(fields, 2), roll=_f(fields, 3), pitch=_f(fields, 4),
    )


def parse_tid(fields: tuple[str, ...], line: Line) -> Record:
    return Tide(
        tag="TID", device=_i(fields, 0), time=_f(fields, 1), correction=_f(fields, 2)
    )


def parse_fix(fields: tuple[str, ...], line: Line) -> Record:
    x = _f(fields, 3) if len(fields) > 3 else None
    y = _f(fields, 4) if len(fields) > 4 else None
    return FixMark(
        tag="FIX", device=_i(fields, 0), time=_f(fields, 1),
        event=_i(fields, 2), x=x, y=y,
    )


def parse_msg(fields: tuple[str, ...], line: Line) -> Record:
    device = int(fields[0])
    time = float(fields[1])
    _, _, rest = line.body.partition(" ")
    _, _, text = rest.partition(" ")
    return Message(tag="MSG", device=device, time=time, text=text)


def parse_com(fields: tuple[str, ...], line: Line) -> Record:
    return Comment(tag="COM", text=line.body)


def parse_lbp(fields: tuple[str, ...], line: Line) -> Record:
    return PlannedLineStart(tag="LBP", x=_f(fields, 0), y=_f(fields, 1))


def parse_lin(fields: tuple[str, ...], line: Line) -> Record:
    return PlannedLine(tag="LIN", waypoints=_i(fields, 0))


def parse_lnn(fields: tuple[str, ...], line: Line) -> Record:
    return PlannedLineName(tag="LNN", name=line.body.strip())


def parse_pts(fields: tuple[str, ...], line: Line) -> Record:
    return PlannedWaypoint(tag="PTS", x=_f(fields, 0), y=_f(fields, 1))


def parse_eol(fields: tuple[str, ...], line: Line) -> Record:
    return EndOfLine(tag="EOL")


def parse_eoh(fields: tuple[str, ...], line: Line) -> Record:
    return EndOfHeader(tag="EOH")


COMMON_TABLE: dict[str, ParseFn] = {
    "FTP": parse_ftp,
    "VER": parse_ver,
    "INF": parse_inf,
    "TND": parse_tnd,
    "DEV": parse_dev,
    "POS": parse_pos,
    "EC1": parse_ec1,
    "GYR": parse_gyr,
    "HCP": parse_hcp,
    "TID": parse_tid,
    "FIX": parse_fix,
    "MSG": parse_msg,
    "COM": parse_com,
    "LBP": parse_lbp,
    "LIN": parse_lin,
    "LNN": parse_lnn,
    "PTS": parse_pts,
    "EOL": parse_eol,
    "EOH": parse_eoh,
}
