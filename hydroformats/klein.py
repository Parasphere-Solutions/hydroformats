"""Klein SDF reader (side scan sonar logging).

An SDF file is the native recording of Klein Marine Systems side scan
sonars (System 3000 and the 3900/NGS family, System 5000, System 7000,
and the 3500 series), as written by the SonarPro topside: data pages
concatenated back to back, each preceded by a 32-bit ping marker that
"never changes and is equal to 0xFFFFFFFF". Each page is one ping: a
header structure shared by every towfish family (176 bytes originally,
grown to 256 bytes at header version 3 and 512 at version 4), then the
family's channel data as variable-length arrays, each led by its own
sample count.

Every layout here and in :mod:`hydroformats.klein_records` is
hand-built from public documents (anchor S14 in
docs/FORMAT-SOURCES.md):

- "SDF Data Page Definitions Specification", L-3 Communications Klein
  Associates, document 15300018 Rev 2.05, 2008-04-15 (the page
  structures, the header typedef, the channel data arrays and the ping
  marker), with Rev 2.03 for cross-checking the version history.
- "SonarPro User Datagram Protocol Interface Specification", L-3
  Klein, document 15300015 Rev 1.3, 2008-04-21 (field units and enum
  tables for the same header).
- ``sdf_reader.m`` from OceanScan-MST's octave-sss repository (MIT
  license, verified; attribution in FORMAT-SOURCES.md), a reference
  reader built on spec revision 4.8: the anchor for the 3500-series
  page versions (3501/3502), their channel encoding and center
  frequency word, and the on-disk byte order.

No GPL or unlicensed parser was consulted. Readings the documents
leave open are resolved as follows (each also noted on the record it
affects):

- **Byte order**: no located Klein document states the disk byte
  order. Little endian is anchored by the MIT reference reader and by
  real SDF bytes; note the UDP companion spec converts the same header
  to network byte order (big endian) for broadcast, a trap this
  reader does not copy.
- **numberBytes excludes the marker**: the page size field counts
  header plus channel data; the next page starts marker + 4 +
  numberBytes in, per the UDP document's wording and the reference
  reader's arithmetic.
- **The SBP count width**: the spec's channel rule is a 2-byte count,
  with one stated exception, "the System 3000 with the Sub Bottom
  Profiler utilizes 4 bytes for the number of samples for the Sub
  Bottom channel". The statement is unconditional, so the 4-byte
  count is applied to both the version 3 and version 4 pages.
- **The version 3 SBP samples are read signed**: the typedef says
  ``short sbp[]`` and the revision history records the deliberate
  change from unsigned ("Changed System 3000 SBP to short from
  unsigned short", Rev 1.01), but the prose below the same typedef
  still says every channel is 16-bit unsigned: an internal
  contradiction, resolved in the typedef's favor.
- **wingAngle is read as a float**, the typedef's type; the UDP
  companion's field table prints U32 for the same word.
- **Sample counts**: each channel's own count prefix is trusted over
  the header's numSamples, which lags by numSamplesExtra on chirp
  waveforms.
- **Markers are required**: the spec defines the .sdf form as marker
  plus page and the marker note is tied to SonarPro-saved files; a
  raw TPU capture without markers is out of scope and degrades to one
  undecodable gap, nothing guessed.
- The System 5000 typedef never says which of the ten processed beam
  channels are port and which starboard; the names are surfaced
  as-is (``chan1`` .. ``chan10``), never mapped to a side.
- The System 7000 channel structure is only "tentatively defined" by
  the spec, so those pages decode header-only with the data region
  verbatim (:class:`~hydroformats.klein_records.Klein7000Page`).
- Later spec revisions assign words inside the 2008 revision's
  reserved region (the 3500-series center frequency at byte 404 is
  proven by the reference reader), so reserved words are never
  assumed zero and unrecognized page versions are skipped tolerantly,
  counted, and never guessed at.

The marker makes resynchronization possible: garbage between pages, a
corrupt size, or a truncated tail degrade to gaps and the scan resumes
at the next marker, never raising. Known page versions whose payload
does not satisfy the layout degrade to
:class:`~hydroformats.records.MalformedRecord`.
"""
from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .klein_records import (
    CHANNEL_PLANS,
    CONFIG_HF_SIDE_SCAN,
    CONFIG_LF_SIDE_SCAN,
    CONFIG_SBP,
    V3_HEADER_SIZE,
    V4_HEADER_SIZE,
    ChannelSpec,
    Klein7000Page,
    KleinChannel,
    KleinPageHeader,
    KleinPing,
    header_fields,
    towfish_name,
)
from .records import MalformedRecord, Record

__all__ = [
    "CONFIG_HF_SIDE_SCAN",
    "CONFIG_LF_SIDE_SCAN",
    "CONFIG_SBP",
    "PAGE_MARKER",
    "SERIES_3500_PAGES",
    "SYSTEM_3000_V3",
    "SYSTEM_3000_V4",
    "SYSTEM_5000_V3",
    "SYSTEM_5000_V4",
    "SYSTEM_7000_V3",
    "SYSTEM_7000_V4",
    "ChannelSpec",
    "Klein7000Page",
    "KleinChannel",
    "KleinChannelSeries",
    "KleinCounters",
    "KleinFrame",
    "KleinGap",
    "KleinPageHeader",
    "KleinPing",
    "KleinSurvey",
    "iter_pages",
    "load_survey",
    "read_klein",
    "towfish_name",
]

PAGE_MARKER = 0xFFFFFFFF   # the ping marker before every page (spec section 3)

SYSTEM_3000_V3 = 3000
SYSTEM_3000_V4 = 3001
SYSTEM_5000_V3 = 5000
SYSTEM_5000_V4 = 5001
SYSTEM_7000_V3 = 7000
SYSTEM_7000_V4 = 7001
SERIES_3500_PAGES = (3501, 3502)

_SYNC = b"\xff\xff\xff\xff"
_PAGE_MIN = 8              # numberBytes + pageVersion
_SYSTEM_7000 = frozenset({SYSTEM_7000_V3, SYSTEM_7000_V4})
_V3_PAGES = frozenset({SYSTEM_3000_V3, SYSTEM_5000_V3, SYSTEM_7000_V3})


# --------------------------------------------------------------------------
# page walking
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KleinFrame:
    """One framed data page: the marker's file offset, the page's
    declared byte count and version, and the page bytes verbatim
    (header plus channel data, the marker excluded, exactly
    ``number_bytes`` long)."""

    offset: int
    number_bytes: int
    page_version: int
    payload: bytes


@dataclass(frozen=True)
class KleinGap:
    """Bytes outside any well-framed page: garbage between pages, a
    marker whose declared size overruns the file, or a truncated final
    page. The scan resumes at the next 0xFFFFFFFF marker."""

    offset: int
    size: int


def _read_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def iter_pages(source: str | Path | bytes) -> Iterator[KleinFrame | KleinGap]:
    """Walk the page stream; never raises on content.

    Yields :class:`KleinFrame` for every marker-led page whose declared
    size fits the file and :class:`KleinGap` for every byte range that
    does not frame, in file order. A marker whose size field is insane
    is treated as part of the surrounding garbage and the scan resumes
    one byte past it, so a corrupt length cannot swallow the pages
    behind it.
    """
    data = _read_bytes(source)
    n = len(data)
    position = 0
    gap_start = 0
    while True:
        sync = data.find(_SYNC, position)
        if sync == -1 or sync + 4 + _PAGE_MIN > n:
            break
        number_bytes, page_version = struct.unpack_from("<2I", data, sync + 4)
        if number_bytes < _PAGE_MIN or number_bytes > n - sync - 4:
            position = sync + 1
            continue
        if sync > gap_start:
            yield KleinGap(offset=gap_start, size=sync - gap_start)
        body = sync + 4
        yield KleinFrame(
            offset=sync, number_bytes=number_bytes,
            page_version=page_version,
            payload=data[body:body + number_bytes],
        )
        position = gap_start = body + number_bytes
    if gap_start < n:
        yield KleinGap(offset=gap_start, size=n - gap_start)


# --------------------------------------------------------------------------
# page decoding
# --------------------------------------------------------------------------


def _walk_channels(
    payload: bytes, start: int, plan: tuple[ChannelSpec, ...],
) -> tuple[tuple[KleinChannel, ...], tuple[str, ...], bytes]:
    """The count-prefixed channel arrays from ``start`` in typedef
    order; raises ValueError when a declared count overruns the page."""
    channels: list[KleinChannel] = []
    absent: tuple[str, ...] = ()
    position = start
    end = len(payload)
    for index, spec in enumerate(plan):
        if end - position < spec.count_width:
            absent = tuple(one.name for one in plan[index:])
            break
        code = "<H" if spec.count_width == 2 else "<I"
        (count,) = struct.unpack_from(code, payload, position)
        size = count * spec.sample_width
        if position + spec.count_width + size > end:
            raise ValueError(
                f"channel {spec.name}: {count} samples of "
                f"{spec.sample_width} bytes overrun the page at offset "
                f"{position + spec.count_width}")
        first = position + spec.count_width
        channels.append(KleinChannel(
            name=spec.name, count=count, sample_width=spec.sample_width,
            signed=spec.signed, sample_bytes=payload[first:first + size],
        ))
        position = first + size
    return tuple(channels), absent, payload[position:end]


def _header_values(frame: KleinFrame) -> dict:
    """The decoded header fields plus the data start offset; raises
    ValueError when the page cannot hold its layout."""
    required = (V3_HEADER_SIZE if frame.page_version in _V3_PAGES
                else V4_HEADER_SIZE)
    if len(frame.payload) < required:
        raise ValueError(
            f"page version {frame.page_version} needs a {required} byte "
            f"header, the page holds {len(frame.payload)} bytes")
    values = header_fields(frame.payload, frame.page_version)
    header_size = values["header_size"]
    if header_size < required or header_size > len(frame.payload):
        raise ValueError(
            f"declared header size {header_size} is outside the valid "
            f"range {required}..{len(frame.payload)}")
    return values


def _decode(frame: KleinFrame) -> Record | None:
    """Typed record for a known page version, None for an unknown one;
    malformed known pages degrade, never raise."""
    known = frame.page_version in CHANNEL_PLANS or \
        frame.page_version in _SYSTEM_7000
    if not known:
        return None
    tag = "SYS7" if frame.page_version in _SYSTEM_7000 else "PING"
    try:
        values = _header_values(frame)
        start = values["header_size"]
        if frame.page_version in _SYSTEM_7000:
            return Klein7000Page(tag=tag, data_bytes=frame.payload[start:],
                                 **values)
        channels, absent, leftover = _walk_channels(
            frame.payload, start, CHANNEL_PLANS[frame.page_version])
        return KleinPing(tag=tag, channels=channels, absent_channels=absent,
                         leftover=leftover, **values)
    except (struct.error, ValueError) as error:
        return MalformedRecord(
            tag=tag,
            fields=(
                f"page_version={frame.page_version}",
                f"offset={frame.offset}",
                f"number_bytes={frame.number_bytes}",
            ),
            error=f"truncated or undecodable page: {error}",
        )


def read_klein(source: str | Path | bytes) -> Iterator[Record]:
    """Stream typed records from an SDF file (path or bytes), in file
    order: one :class:`~hydroformats.klein_records.KleinPing` per
    sidescan data page and one
    :class:`~hydroformats.klein_records.Klein7000Page` per System 7000
    page. Pages with unrecognized page versions and unframeable byte
    runs are skipped tolerantly (:func:`load_survey` counts both);
    known versions whose payload does not satisfy the spec layout
    yield :class:`~hydroformats.records.MalformedRecord`. Never raises
    on content.
    """
    for event in iter_pages(source):
        if isinstance(event, KleinGap):
            continue
        record = _decode(event)
        if record is not None:
            yield record


# --------------------------------------------------------------------------
# survey loading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KleinCounters:
    """Stream accounting from one :func:`load_survey` pass.

    ``pages`` counts every framed page, decoded or not.
    ``unknown_page_versions`` is (page version, count) pairs in
    ascending version order. ``malformed`` counts known-version pages
    whose payload would not decode (dropped from the series;
    :func:`read_klein` shows them). ``bytes_skipped`` counts bytes
    outside any framed page.
    """

    pages: int
    unknown_page_versions: tuple[tuple[int, int], ...]
    malformed: int
    bytes_skipped: int


@dataclass(frozen=True)
class KleinChannelSeries:
    """One channel's ping series: the typedef channel name, the parent
    pings, and the per-ping channel data aligned with them index for
    index."""

    name: str
    pings: tuple[KleinPing, ...]
    data: tuple[KleinChannel, ...]


@dataclass(frozen=True)
class KleinSurvey:
    """One materialized SDF file, split into its working series.

    ``pings`` are the decoded sidescan data pages in file order, each
    carrying its channel arrays; :meth:`channel_series` regroups them
    per channel. ``system_7000`` holds the header-decoded System 7000
    pages.
    """

    pings: tuple[KleinPing, ...]
    system_7000: tuple[Klein7000Page, ...]
    counters: KleinCounters

    def channel_series(self) -> tuple[KleinChannelSeries, ...]:
        """The pings regrouped per channel name, in first-seen file
        order, pairing each channel's data with its parent pings."""
        order: list[str] = []
        groups: dict[str, tuple[list[KleinPing], list[KleinChannel]]] = {}
        for ping in self.pings:
            for channel in ping.channels:
                if channel.name not in groups:
                    order.append(channel.name)
                    groups[channel.name] = ([], [])
                pings, data = groups[channel.name]
                pings.append(ping)
                data.append(channel)
        return tuple(
            KleinChannelSeries(name=name, pings=tuple(groups[name][0]),
                               data=tuple(groups[name][1]))
            for name in order
        )


def load_survey(source: str | Path | bytes) -> KleinSurvey:
    """Materialize a whole SDF file into series (small files, tests).

    Sample bytes stay raw on each channel record
    (:meth:`~hydroformats.klein_records.KleinChannel.values` decodes on
    demand), so loading does not multiply the file's memory footprint.
    Never raises on content. Exported at the package level as
    ``load_klein``.
    """
    pings: list[KleinPing] = []
    system_7000: list[Klein7000Page] = []
    unknown: dict[int, int] = {}
    pages = malformed = skipped = 0
    for event in iter_pages(source):
        if isinstance(event, KleinGap):
            skipped += event.size
            continue
        pages += 1
        record = _decode(event)
        if record is None:
            unknown[event.page_version] = \
                unknown.get(event.page_version, 0) + 1
        elif isinstance(record, MalformedRecord):
            malformed += 1
        elif isinstance(record, KleinPing):
            pings.append(record)
        elif isinstance(record, Klein7000Page):
            system_7000.append(record)
    return KleinSurvey(
        pings=tuple(pings), system_7000=tuple(system_7000),
        counters=KleinCounters(
            pages=pages,
            unknown_page_versions=tuple(sorted(unknown.items())),
            malformed=malformed, bytes_skipped=skipped,
        ),
    )
