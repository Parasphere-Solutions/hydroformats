"""Triton eXtended Triton Format (XTF) reader (sidescan interchange).

XTF is the broadest sidescan interchange format in the industry: Klein,
EdgeTech, Kongsberg, Benthos, CMAX, Imagenex and many more acquisition
chains export it. A file is one XTFFILEHEADER (1024 bytes, growing in
1024-byte steps when more than six channels are declared) holding one
CHANINFO block of static metadata per channel, then an asynchronous
"pool" of packets to the end of the file. Every packet starts with the
16-bit magic word 0xFACE, a header type byte, a sub-channel byte, a
channel count word, two reserved words, and the packet's total byte
count; the spec says the magic "can be used to align the data stream to
the start of a packet", which is how this reader resynchronizes after
corruption. All multi-byte values are little-endian (Intel byte
ordering, spec section 2.5) except vendor bathymetry payloads, which are
carried raw and untouched.

Every layout here and in :mod:`hydroformats.xtf_records` is hand-built
from the specification document only (anchor S10 in
docs/FORMAT-SOURCES.md):

- Triton Imaging, Inc., "eXtended Triton Format (XTF) Rev. 41",
  September 2016. Distributed by Triton from
  tritonimaginginc.com/site/content/public/downloads/FileFormatInfo/;
  archived copy:
  https://web.archive.org/web/20170418082139/http://www.tritonimaginginc.com/site/content/public/downloads/FileFormatInfo/Xtf%20File%20Format_X41.pdf

No third-party XTF parser was consulted. Readings the document leaves
open, and its two internal table inconsistencies, are resolved as
follows (each also noted on the record it affects):

- The XTFATTITUDEDATA table lists HeaderType at offset 1 and
  SubChannelNumber at 2, but the magic at offset 0 is a WORD spanning
  bytes 0-1, and every other packet table puts them at 2 and 3. The
  uniform 14-byte prefix is used for every packet.
- The XTFPINGHEADER table lists ReservedSpace2[6] at offset 245,
  overlapping the OptionalOffset word (245) and CableOutHundredths
  (249) it also defines. Reserved space is read as bytes 250-255, the
  only reading that fills the stated 256 bytes exactly.
- The sample formats say only "integer"; whether samples are signed is
  never stated. Integer samples decode unsigned when the channel's
  UniPolar word is nonzero and signed when it is zero ("0=data is
  polar"), and :meth:`~hydroformats.xtf_records.XtfPingChannel.values`
  takes a ``signed`` override. Raw sample bytes always ride along
  untouched.
- Sample format 1 (4-byte IBM float) is not decoded: the spec names the
  format without defining its bit layout, so those channels keep raw
  bytes only.
- A file whose header cannot be read is a single undecodable gap:
  without the CHANINFO blocks no channel data can be sized, so nothing
  downstream is guessed at.

XTF's known sins are surfaced, not resolved. Navigation is duplicated
between the ship position (``ship_x``/``ship_y``), the sensor position
(``sensor_x``/``sensor_y``) and the towfish geometry (``layback_m``,
``cable_out_m``, ``fish_position_delta_*``); which of them to
georeference against, and whether to swing the layback from the ship
track, is a survey-specific policy decision that belongs to the
consumer, so every field is decoded and none is blessed. The ping
header's SoundVelocity is likewise carried verbatim: the spec warns it
is one-way (750 m/s) in Isis files but stored as 1500 in others.

Units and axis conventions, per the spec tables: coordinates are
degrees when the file header's NavUnits is 3 and projected meters when
it is 0; sensor_depth_m is meters below the sea surface, positive down;
sensor altitudes are meters above the seafloor; heave is positive up;
pitch is positive nose up; roll is positive to starboard; mounting
offsets are meters with X positive to starboard, Y positive forward and
Z positive down.
"""
from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .records import MalformedRecord, Record
from .xtf_records import (
    SNP0_FIELDS,
    SNP0_ID,
    SNP1,
    SNP1_ID,
    XtfAttitude,
    XtfBathySnippet,
    XtfChannelInfo,
    XtfNotes,
    XtfPing,
    XtfPingChannel,
    XtfRawBathy,
    XtfRawSerial,
    XtfSnippetBeam,
    XtfSnp0,
    XtfSonarPing,
    parse_chan_info,
    parse_channel,
    ping_fields,
    sample_width,
    text_field,
)

__all__ = [
    "FILE_FORMAT",
    "HEADER_ATTITUDE",
    "HEADER_BATHY",
    "HEADER_BATHY_SNIPPET",
    "HEADER_NOTES",
    "HEADER_RAW_SERIAL",
    "HEADER_SONAR",
    "MAGIC",
    "XtfAttitude",
    "XtfBathySnippet",
    "XtfChannelInfo",
    "XtfChannelSeries",
    "XtfCounters",
    "XtfFileHeader",
    "XtfNotes",
    "XtfPing",
    "XtfPingChannel",
    "XtfRawBathy",
    "XtfRawSerial",
    "XtfSnippetBeam",
    "XtfSnp0",
    "XtfSonarPing",
    "XtfSurvey",
    "load_survey",
    "read_xtf",
]

FILE_FORMAT = 123  # leading byte of every XTF file (spec Table C)
MAGIC = 0xFACE     # leading word of every packet (spec Tables E through P)

HEADER_SONAR = 0
HEADER_NOTES = 1
HEADER_BATHY = 2
HEADER_ATTITUDE = 3
HEADER_RAW_SERIAL = 6
HEADER_BATHY_SNIPPET = 19

_TAGS = {
    HEADER_SONAR: "PING",
    HEADER_NOTES: "NOTE",
    HEADER_BATHY: "BATHY",
    HEADER_ATTITUDE: "ATT",
    HEADER_RAW_SERIAL: "SER",
    HEADER_BATHY_SNIPPET: "SNIP",
}

_PREFIX = struct.Struct("<HBBH")  # magic, header type, sub-channel, chans
_SYNC = b"\xce\xfa"               # the magic word 0xFACE, little endian
_PACKET_MIN = 14                  # the shared packet prefix

_NAV_UNITS = {0: "meters", 3: "degrees"}


# --------------------------------------------------------------------------
# file header (spec Table C)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class XtfFileHeader(Record):
    """The one XTFFILEHEADER at offset zero (spec Table C).

    1024 bytes holding six CHANINFO slots; when the declared sonar plus
    bathymetry channels exceed six, the header grows in 1024-byte steps
    (eight more slots each) and ``header_size`` reflects the growth.
    ``nav_units`` selects the coordinate units for every position in the
    file: 0 projected meters, 3 latitude/longitude degrees.
    ``navigation_latency_ms`` is the logging chain's declared nav
    latency. The nav and MRU mounting offsets are meters and degrees in
    the X-starboard, Y-forward, Z-down frame. Unparsed regions (the
    unused projection and spheroid fields, reserved words) remain
    reachable through ``header_bytes``.
    """

    file_format: int
    system_type: int
    recording_program_name: str
    recording_program_version: str
    sonar_name: str
    sonar_type: int
    note_string: str
    this_file_name: str
    nav_units: int
    num_sonar_channels: int
    num_bathymetry_channels: int
    num_snippet_channels: int
    num_forward_look_arrays: int
    num_echo_strength_channels: int
    num_interferometry_channels: int
    reference_point_height: float
    navigation_latency_ms: int
    nav_offset_y: float
    nav_offset_x: float
    nav_offset_z: float
    nav_offset_yaw: float
    mru_offset_y: float
    mru_offset_x: float
    mru_offset_z: float
    mru_offset_yaw: float
    mru_offset_pitch: float
    mru_offset_roll: float
    channels: tuple[XtfChannelInfo, ...]
    header_size: int
    header_bytes: bytes

    @property
    def nav_units_name(self) -> str | None:
        """meters (0) or degrees (3); None for an unknown code."""
        return _NAV_UNITS.get(self.nav_units)


def _parse_file_header(data: bytes) -> XtfFileHeader | _Gap:
    if len(data) < 1024:
        return _Gap(tag="HDR", offset=0, size=len(data),
                    error=f"file header needs 1024 bytes, got {len(data)}")
    if data[0] != FILE_FORMAT:
        return _Gap(tag="HDR", offset=0, size=len(data),
                    error=f"leading byte {data[0]} is not the XTF file "
                          f"format value {FILE_FORMAT}")
    nav_units, n_sonar, n_bathy = struct.unpack_from("<3H", data, 164)
    declared = n_sonar + n_bathy
    size = 1024
    if declared > 6:
        size += 1024 * ((declared - 6 + 7) // 8)
    if len(data) < size:
        return _Gap(tag="HDR", offset=0, size=len(data),
                    error=f"{declared} declared channels need a {size} byte "
                          f"header, got {len(data)} bytes")
    n_snippet, n_forward = struct.unpack_from("<2B", data, 170)
    (n_echo,) = struct.unpack_from("<H", data, 172)
    (n_interferometry,) = struct.unpack_from("<B", data, 174)
    (reference_height,) = struct.unpack_from("<f", data, 178)
    (latency,) = struct.unpack_from("<i", data, 204)
    nav_offsets = struct.unpack_from("<4f", data, 216)
    mru_offsets = struct.unpack_from("<6f", data, 232)
    count = min(declared, (size - 256) // 128)
    channels = tuple(parse_chan_info(data, 256 + 128 * i, i)
                     for i in range(count))
    return XtfFileHeader(
        tag="HDR", file_format=data[0], system_type=data[1],
        recording_program_name=text_field(data[2:10]),
        recording_program_version=text_field(data[10:18]),
        sonar_name=text_field(data[18:34]),
        sonar_type=struct.unpack_from("<H", data, 34)[0],
        note_string=text_field(data[36:100]),
        this_file_name=text_field(data[100:164]),
        nav_units=nav_units, num_sonar_channels=n_sonar,
        num_bathymetry_channels=n_bathy, num_snippet_channels=n_snippet,
        num_forward_look_arrays=n_forward,
        num_echo_strength_channels=n_echo,
        num_interferometry_channels=n_interferometry,
        reference_point_height=reference_height,
        navigation_latency_ms=latency,
        nav_offset_y=nav_offsets[0], nav_offset_x=nav_offsets[1],
        nav_offset_z=nav_offsets[2], nav_offset_yaw=nav_offsets[3],
        mru_offset_y=mru_offsets[0], mru_offset_x=mru_offsets[1],
        mru_offset_z=mru_offsets[2], mru_offset_yaw=mru_offsets[3],
        mru_offset_pitch=mru_offsets[4], mru_offset_roll=mru_offsets[5],
        channels=channels, header_size=size, header_bytes=data[:size],
    )


# --------------------------------------------------------------------------
# packet walking
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Frame:
    """One framed packet: prefix fields plus the whole packet verbatim."""

    offset: int
    header_type: int
    sub_channel: int
    num_chans: int
    payload: bytes


@dataclass(frozen=True)
class _Gap:
    """Bytes that could not be framed; load_survey counts them and
    read_xtf reports the file-header gap as a MalformedRecord."""

    tag: str
    offset: int
    size: int
    error: str


def _read_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def _walk(source: str | Path | bytes) -> Iterator[XtfFileHeader | _Frame | _Gap]:
    """File header, then framed packets and gaps in file order.

    Packets advance by their declared total size. A wrong magic word or
    an impossible size resynchronizes on the next 0xFACE byte pair (the
    spec sanctions the magic for realignment); a declared size running
    past the end of the file is a truncated tail. Never raises on
    content.
    """
    data = _read_bytes(source)
    header = _parse_file_header(data)
    yield header
    if isinstance(header, _Gap):
        return
    n = len(data)
    position = gap_start = header.header_size
    while position + _PACKET_MIN <= n:
        magic, header_type, sub_channel, num_chans = _PREFIX.unpack_from(
            data, position)
        if magic != MAGIC:
            found = data.find(_SYNC, position + 1)
            position = found if found != -1 else n
            continue
        (num_bytes,) = struct.unpack_from("<I", data, position + 10)
        if num_bytes < _PACKET_MIN:
            found = data.find(_SYNC, position + 2)
            position = found if found != -1 else n
            continue
        if position + num_bytes > n:
            break  # truncated final packet: the tail gap below covers it
        if position > gap_start:
            yield _Gap(tag="PKT", offset=gap_start,
                       size=position - gap_start, error="unframed bytes")
        yield _Frame(offset=position, header_type=header_type,
                     sub_channel=sub_channel, num_chans=num_chans,
                     payload=data[position:position + num_bytes])
        position = gap_start = position + num_bytes
    if gap_start < n:
        yield _Gap(tag="PKT", offset=gap_start, size=n - gap_start,
                   error="unframed or truncated tail")


# --------------------------------------------------------------------------
# per-type decoders (frame -> Record)
# --------------------------------------------------------------------------


def _decode_sonar(frame: _Frame,
                  channels: tuple[XtfChannelInfo, ...]) -> Record:
    payload = frame.payload
    values = ping_fields(payload)
    parsed: list[XtfPingChannel] = []
    skipped: list[tuple[int, int]] = []
    position = 256
    end = len(payload)
    for _ in range(frame.num_chans):
        if position + 64 > end:
            raise ValueError(f"channel header at offset {position} overruns "
                             f"a {end} byte packet")
        (channel_number,) = struct.unpack_from("<H", payload, position)
        info = (channels[channel_number]
                if 0 <= channel_number < len(channels) else None)
        if info is None or sample_width(info) is None:
            skipped.append((channel_number, end - position - 64))
            break  # without a width the next channel cannot be located
        channel = parse_channel(payload, position, end, info)
        parsed.append(channel)
        position += 64 + len(channel.sample_bytes)
    return XtfSonarPing(tag="PING", channels=tuple(parsed),
                        skipped_channels=tuple(skipped), **values)


def _decode_bathy(frame: _Frame, _: tuple) -> Record:
    values = ping_fields(frame.payload)
    return XtfRawBathy(tag="BATHY", payload=frame.payload[256:], **values)


def _decode_snippet(frame: _Frame, _: tuple) -> Record:
    payload = frame.payload
    values = ping_fields(payload)
    if len(payload) < 256 + 74:
        raise ValueError(f"snippet packet of {len(payload)} bytes cannot "
                         f"hold the 74 byte SNP0 block")
    (snp0_id,) = struct.unpack_from("<I", payload, 256)
    if snp0_id != SNP0_ID:
        raise ValueError(f"SNP0 identifier 0x{snp0_id:08X} is not "
                         f"0x{SNP0_ID:08X}")
    fields = {name: struct.unpack_from(fmt, payload, 256 + offset)[0]
              for name, offset, fmt in SNP0_FIELDS}
    fields["sonar_id"] = struct.unpack_from("<2H", payload, 256 + 22)
    fields["flags"] = struct.unpack_from("<2B", payload, 256 + 68)
    snp0 = XtfSnp0(**fields)
    beams: list[XtfSnippetBeam] = []
    position = 256 + 74
    end = len(payload)
    for _index in range(snp0.beam_count):
        if position + SNP1.size > end:
            break
        (snp1_id, header_size, data_size, ping_number, beam, samples,
         gain_start, gain_end, frag_offset,
         frag_samples) = SNP1.unpack_from(payload, position)
        if snp1_id != SNP1_ID or position + SNP1.size + data_size > end:
            break
        beams.append(XtfSnippetBeam(
            header_size=header_size, data_size=data_size,
            ping_number=ping_number, beam=beam, snippet_samples=samples,
            gain_start=gain_start, gain_end=gain_end,
            fragment_offset=frag_offset, fragment_samples=frag_samples,
            fragment_bytes=payload[position + SNP1.size:
                                   position + SNP1.size + data_size],
        ))
        position += SNP1.size + data_size
    return XtfBathySnippet(tag="SNIP", snp0=snp0, beams=tuple(beams),
                           leftover=payload[position:], **values)


def _decode_attitude(frame: _Frame, _: tuple) -> Record:
    payload = frame.payload
    epoch_us, source_epoch = struct.unpack_from("<2I", payload, 22)
    pitch, roll, heave = struct.unpack_from("<3f", payload, 30)
    (yaw,) = struct.unpack_from("<f", payload, 42)
    (time_tag,) = struct.unpack_from("<I", payload, 46)
    (heading,) = struct.unpack_from("<f", payload, 50)
    (year,) = struct.unpack_from("<H", payload, 54)
    month, day, hour, minute, second = struct.unpack_from("<5B", payload, 56)
    (milliseconds,) = struct.unpack_from("<H", payload, 61)
    return XtfAttitude(
        tag="ATT", epoch_microseconds=epoch_us, source_epoch=source_epoch,
        pitch_degrees=pitch, roll_degrees=roll, heave_m=heave,
        yaw_degrees=yaw, time_tag_ms=time_tag, heading_degrees=heading,
        year=year, month=month, day=day, hour=hour, minute=minute,
        second=second, milliseconds=milliseconds,
    )


def _decode_notes(frame: _Frame, _: tuple) -> Record:
    payload = frame.payload
    (year,) = struct.unpack_from("<H", payload, 14)
    month, day, hour, minute, second = struct.unpack_from("<5B", payload, 16)
    if len(payload) < 256:
        raise ValueError(f"notes packet of {len(payload)} bytes is shorter "
                         f"than the fixed 256")
    return XtfNotes(
        tag="NOTE", sub_channel=frame.sub_channel, year=year, month=month,
        day=day, hour=hour, minute=minute, second=second,
        text=text_field(payload[56:256]),
    )


def _decode_raw_serial(frame: _Frame, _: tuple) -> Record:
    payload = frame.payload
    (year,) = struct.unpack_from("<H", payload, 14)
    (month, day, hour, minute, second,
     hseconds) = struct.unpack_from("<6B", payload, 16)
    (julian_day,) = struct.unpack_from("<H", payload, 22)
    (time_tag,) = struct.unpack_from("<I", payload, 24)
    (string_size,) = struct.unpack_from("<H", payload, 28)
    if 30 + string_size > len(payload):
        raise ValueError(f"serial string of {string_size} bytes overruns a "
                         f"{len(payload)} byte packet")
    text = payload[30:30 + string_size].decode("latin-1").rstrip("\x00\r\n")
    return XtfRawSerial(
        tag="SER", serial_port=frame.sub_channel, year=year, month=month,
        day=day, hour=hour, minute=minute, second=second, hseconds=hseconds,
        julian_day=julian_day, time_tag_ms=time_tag, text=text,
    )


_DECODERS = {
    HEADER_SONAR: _decode_sonar,
    HEADER_NOTES: _decode_notes,
    HEADER_BATHY: _decode_bathy,
    HEADER_ATTITUDE: _decode_attitude,
    HEADER_RAW_SERIAL: _decode_raw_serial,
    HEADER_BATHY_SNIPPET: _decode_snippet,
}


def _decode(frame: _Frame,
            channels: tuple[XtfChannelInfo, ...]) -> Record | None:
    """Typed record for a known header type, None for an unknown one."""
    decoder = _DECODERS.get(frame.header_type)
    if decoder is None:
        return None
    try:
        return decoder(frame, channels)
    except (struct.error, ValueError) as error:
        return MalformedRecord(
            tag=_TAGS[frame.header_type],
            fields=(
                f"header_type={frame.header_type}",
                f"offset={frame.offset}",
                f"packet_size={len(frame.payload)}",
            ),
            error=f"truncated or undecodable packet: {error}",
        )


def read_xtf(source: str | Path | bytes) -> Iterator[Record]:
    """Stream typed records from an XTF file (path or bytes), in file
    order: the :class:`XtfFileHeader`, then one record per decoded
    packet. An unreadable file header yields a single
    :class:`~hydroformats.records.MalformedRecord` and ends the stream
    (nothing downstream can be sized without the channel blocks).
    Packets with unknown header types and unframeable byte runs are
    skipped tolerantly (:func:`load_survey` counts both); known types
    whose payload does not satisfy the spec layout yield
    :class:`~hydroformats.records.MalformedRecord`. Never raises on
    content.
    """
    channels: tuple[XtfChannelInfo, ...] = ()
    for event in _walk(source):
        if isinstance(event, _Gap):
            if event.tag == "HDR":
                yield MalformedRecord(
                    tag="HDR",
                    fields=(f"offset={event.offset}", f"size={event.size}"),
                    error=event.error,
                )
            continue
        if isinstance(event, XtfFileHeader):
            channels = event.channels
            yield event
            continue
        record = _decode(event, channels)
        if record is not None:
            yield record


# --------------------------------------------------------------------------
# survey loading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class XtfCounters:
    """Stream accounting from one :func:`load_survey` pass.

    ``packets`` counts every framed packet, decoded or not.
    ``unknown_header_types`` is (header type, count) pairs in ascending
    type order. ``malformed`` counts known-type packets whose payload
    would not decode (dropped from the series; :func:`read_xtf` shows
    them). ``bytes_skipped`` counts bytes outside any framed packet: an
    unreadable file header, garbage between packets, a truncated tail.
    """

    packets: int
    unknown_header_types: tuple[tuple[int, int], ...]
    malformed: int
    bytes_skipped: int


@dataclass(frozen=True)
class XtfChannelSeries:
    """One sidescan channel's ping series with its metadata: the CHANINFO
    block (None when the channel number has no block), the parent pings,
    and the per-ping channel data aligned with them index for index."""

    channel_number: int
    info: XtfChannelInfo | None
    pings: tuple[XtfSonarPing, ...]
    data: tuple[XtfPingChannel, ...]


@dataclass(frozen=True)
class XtfSurvey:
    """One materialized XTF file, split into its working series.

    ``pings`` are the sidescan sonar pings in file order, each carrying
    its per-channel data; :meth:`channel_series` regroups them per
    channel with the channel metadata. ``bathy`` and ``snippets`` are
    the vendor bathymetry passthrough packets; ``attitude`` the MRU
    series; ``serial`` the raw serial (typically NMEA) lines that carry
    navigation; ``notes`` the annotations. ``header`` is None only when
    the file header was unreadable.
    """

    header: XtfFileHeader | None
    pings: tuple[XtfSonarPing, ...]
    bathy: tuple[XtfRawBathy, ...]
    snippets: tuple[XtfBathySnippet, ...]
    attitude: tuple[XtfAttitude, ...]
    notes: tuple[XtfNotes, ...]
    serial: tuple[XtfRawSerial, ...]
    counters: XtfCounters

    def channel_series(self) -> tuple[XtfChannelSeries, ...]:
        """The pings regrouped per channel number, ascending, pairing
        each channel's data with the file header's CHANINFO metadata."""
        groups: dict[int, tuple[list[XtfSonarPing], list[XtfPingChannel]]] = {}
        for ping in self.pings:
            for channel in ping.channels:
                pings, data = groups.setdefault(channel.channel_number,
                                                ([], []))
                pings.append(ping)
                data.append(channel)
        infos = self.header.channels if self.header is not None else ()
        return tuple(
            XtfChannelSeries(
                channel_number=number,
                info=infos[number] if number < len(infos) else None,
                pings=tuple(pings), data=tuple(data),
            )
            for number, (pings, data) in sorted(groups.items())
        )


def load_survey(source: str | Path | bytes) -> XtfSurvey:
    """Materialize a whole XTF file into series (small files, tests).

    Sample bytes stay raw on each channel record
    (:meth:`~hydroformats.xtf_records.XtfPingChannel.values` decodes on
    demand), so loading does not multiply the file's memory footprint.
    Never raises on content.
    """
    header: XtfFileHeader | None = None
    channels: tuple[XtfChannelInfo, ...] = ()
    pings: list[XtfSonarPing] = []
    bathy: list[XtfRawBathy] = []
    snippets: list[XtfBathySnippet] = []
    attitude: list[XtfAttitude] = []
    notes: list[XtfNotes] = []
    serial: list[XtfRawSerial] = []
    unknown: dict[int, int] = {}
    packets = malformed = skipped = 0
    for event in _walk(source):
        if isinstance(event, _Gap):
            skipped += event.size
            continue
        if isinstance(event, XtfFileHeader):
            header = event
            channels = event.channels
            continue
        packets += 1
        record = _decode(event, channels)
        if record is None:
            unknown[event.header_type] = unknown.get(event.header_type, 0) + 1
        elif isinstance(record, MalformedRecord):
            malformed += 1
        elif isinstance(record, XtfSonarPing):
            pings.append(record)
        elif isinstance(record, XtfBathySnippet):
            snippets.append(record)
        elif isinstance(record, XtfRawBathy):
            bathy.append(record)
        elif isinstance(record, XtfAttitude):
            attitude.append(record)
        elif isinstance(record, XtfNotes):
            notes.append(record)
        elif isinstance(record, XtfRawSerial):
            serial.append(record)
    return XtfSurvey(
        header=header, pings=tuple(pings), bathy=tuple(bathy),
        snippets=tuple(snippets), attitude=tuple(attitude),
        notes=tuple(notes), serial=tuple(serial),
        counters=XtfCounters(
            packets=packets,
            unknown_header_types=tuple(sorted(unknown.items())),
            malformed=malformed, bytes_skipped=skipped,
        ),
    )
