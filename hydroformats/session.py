"""Session layer: a parsed survey file with header context resolved.

A Session sniffs the dialect (the binary ``DATAGRAM VERSION`` magic marks
HS2X; an ``HSX`` version record marks HSX), splits header from data at
``EOH`` (synthesized for HS2X), and resolves the device registry so data
records can be attributed to named sensors. Data access is streaming-first
(:meth:`Session.records`); :meth:`Session.load` materializes everything for
small files and tests.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .framing import iter_lines
from .hs2x import MAGIC as _HS2X_MAGIC
from .hs2x import parse_hs2x
from .hsx import parse_hsx
from .raw import parse_raw
from .records import (
    Device,
    EndOfHeader,
    HsxVersion,
    Record,
    SurveyInfo,
    TimeDate,
)

_SNIFF_LINES = 100


def _has_hs2x_magic(path: str | Path) -> bool:
    try:
        with open(path, "rb") as handle:
            head = handle.read(4 + len(_HS2X_MAGIC))
    except OSError:
        return False
    return head[4:] == _HS2X_MAGIC


def sniff_dialect(path: str | Path) -> str:
    """Return ``"hs2x"``, ``"hsx"``, or ``"raw"`` by inspecting the file."""
    if _has_hs2x_magic(path):
        return "hs2x"
    for line in iter_lines(path):
        if line.number > _SNIFF_LINES or line.tag == "EOH":
            break
        if line.tag == "HSX":
            return "hsx"
    suffix = Path(path).suffix.lower()
    if suffix == ".hsx":
        return "hsx"
    if suffix == ".hs2x":
        return "hs2x"
    return "raw"


@dataclass(frozen=True)
class Header:
    """Materialized header region of a survey file."""

    dialect: str
    records: tuple[Record, ...]
    devices: dict[int, Device] = field(default_factory=dict)
    survey_info: SurveyInfo | None = None
    time_date: TimeDate | None = None
    hsx_version: int | None = None

    def device_name(self, number: int) -> str:
        device = self.devices.get(number)
        return device.name if device else f"device {number}"


class Session:
    """One survey file: parsed header plus streaming access to data records."""

    def __init__(self, path: str | Path, dialect: str | None = None):
        self.path = Path(path)
        self.dialect = dialect or sniff_dialect(self.path)
        parsers = {"hsx": parse_hsx, "hs2x": parse_hs2x}
        self._parser = parsers.get(self.dialect, parse_raw)
        self.header = self._read_header()

    def _read_header(self) -> Header:
        records: list[Record] = []
        devices: dict[int, Device] = {}
        survey_info = time_date = None
        hsx_version = None
        for record in self._parser(self.path):
            records.append(record)
            if isinstance(record, Device):
                devices[record.device] = record
            elif isinstance(record, SurveyInfo):
                survey_info = record
            elif isinstance(record, TimeDate):
                time_date = record
            elif isinstance(record, HsxVersion):
                hsx_version = record.version
            if isinstance(record, EndOfHeader):
                break
        return Header(
            dialect=self.dialect, records=tuple(records), devices=devices,
            survey_info=survey_info, time_date=time_date, hsx_version=hsx_version,
        )

    def records(self) -> Iterator[Record]:
        """Stream data records (everything after EOH)."""
        seen_eoh = False
        for record in self._parser(self.path):
            if seen_eoh:
                yield record
            elif isinstance(record, EndOfHeader):
                seen_eoh = True

    def load(self) -> tuple[Record, ...]:
        """Materialize all data records (small files, tests)."""
        return tuple(self.records())

    def summary(self) -> dict:
        """Counts by tag plus header identity — the ``info`` CLI's payload."""
        counts: dict[str, int] = {}
        for record in self.records():
            counts[record.tag] = counts.get(record.tag, 0) + 1
        header = self.header
        started = None
        if header.time_date:
            td = header.time_date
            started = (
                f"{td.year:04d}-{td.month:02d}-{td.day:02d} "
                f"{td.hour:02d}:{td.minute:02d}:{td.second:02d}"
            )
        return {
            "path": str(self.path),
            "dialect": self.dialect,
            "survey_started": started,
            "devices": {n: d.name for n, d in sorted(header.devices.items())},
            "record_counts": dict(sorted(counts.items())),
        }


def open_session(path: str | Path) -> Session:
    """Convenience constructor mirroring :class:`Session`."""
    return Session(path)
