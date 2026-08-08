"""HS2X binary dialect parser (HYPACK® HYSWEEP® 64-bit edit format).

An HS2X file is one chain of doubly-linked TLV frames::

    [payload size: u16][record type: u16][payload]          # first frame
    [previous size: u16][payload size: u16][type: u16][payload]  # rest

The first frame is the type-26 file header ("DATAGRAM VERSION ..."). Every
later frame carries the previous frame's payload size, which lets the chain
be verified as it is walked (``Hs2xFrame.link_ok``). Files end after the
last payload, optionally followed by a 2-byte echo of its size.

There is no public byte-level specification of HS2X; layouts here are
anchored empirically against a paired HSX log of the same session (source
S5 in docs/FORMAT-SOURCES.md). Record types without an anchored layout are
surfaced as :class:`~hydroformats.records.Hs2xOpaque` — nothing is guessed.
Frames whose payload cannot satisfy the anchored layout degrade to
:class:`~hydroformats.records.MalformedRecord`, never exceptions.
"""
from __future__ import annotations

import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .records import (
    EndOfHeader,
    Hs2xAttitude,
    Hs2xFileHeader,
    Hs2xHeading,
    Hs2xOpaque,
    Hs2xPing,
    Hs2xPosition,
    Hs2xSidescanData,
    Hs2xSidescanHeader,
    Hs2xSounding,
    Hs2xTide,
    Hs2xTimeMark,
    MalformedRecord,
    Record,
)

MAGIC = b"DATAGRAM VERSION"

_FILE_HEADER_TYPE = 26
# Record types that begin the data region (navigation, pings, soundings,
# sidescan); everything before the first of these is header/configuration.
_DATA_TYPES = frozenset({60, 61, 62, 63, 67, 68, 69, 70, 72})

_TAGS = {
    26: "HS2X",
    60: "TID",
    61: "TMK",
    62: "GYR",
    63: "HCP",
    67: "POS",
    68: "PING",
    69: "SND",
    70: "SSH",
    72: "SSD",
}


@dataclass(frozen=True)
class Hs2xFrame:
    """One framed record: header fields, payload, and chain integrity."""

    offset: int
    prev_size: int
    size: int
    record_type: int
    payload: bytes
    link_ok: bool


def _read_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def iter_frames(source: str | Path | bytes) -> Iterator[Hs2xFrame]:
    """Walk the TLV chain; never raises on content.

    The final frame may carry a payload shorter than its declared size
    (truncated file); the record layer decides what to do with it. Up to
    five residual bytes after the last frame are treated as the customary
    trailing size echo and ignored.
    """
    data = _read_bytes(source)
    if len(data) < 4:
        return
    size, record_type = struct.unpack_from("<HH", data, 0)
    yield Hs2xFrame(
        offset=0, prev_size=0, size=size, record_type=record_type,
        payload=data[4:4 + size], link_ok=True,
    )
    position = 4 + size
    expected_prev = size
    while position + 6 <= len(data):
        prev_size, size, record_type = struct.unpack_from("<3H", data, position)
        yield Hs2xFrame(
            offset=position, prev_size=prev_size, size=size,
            record_type=record_type, payload=data[position + 6:position + 6 + size],
            link_ok=prev_size == expected_prev,
        )
        position += 6 + size
        expected_prev = size


# --------------------------------------------------------------------------
# per-type decoders (payload -> Record); offsets per source S5
# --------------------------------------------------------------------------


def _decode_file_header(payload: bytes) -> Record:
    if len(payload) < 4:
        raise ValueError("file header payload shorter than 4 bytes")
    (word,) = struct.unpack_from("<I", payload, len(payload) - 4)
    strings = [part.decode("latin-1") for part in payload[:-4].split(b"\x00") if part]
    text = strings[0] if strings else ""
    build_date = strings[1] if len(strings) > 1 else ""
    version: int | None = None
    _, _, last = text.rpartition(" ")
    if last.isdigit():
        version = int(last)
    return Hs2xFileHeader(
        tag="HS2X", text=text, build_date=build_date, version=version,
        unassigned=(word,),
    )


def _decode_time_mark(payload: bytes) -> Record:
    time_ms, w1, w2 = struct.unpack_from("<3i", payload, 0)
    return Hs2xTimeMark(tag="TMK", time_ms=time_ms, unassigned=(w1, w2))


def _decode_heading(payload: bytes) -> Record:
    time_ms, device, w6, heading = struct.unpack_from("<i2Hi", payload, 0)
    return Hs2xHeading(
        tag="GYR", time_ms=time_ms, device=device,
        heading_millideg=heading, unassigned=(w6,),
    )


def _decode_attitude(payload: bytes) -> Record:
    time_ms, device, w6, w8, w12, roll, w20, pitch, w28 = struct.unpack_from(
        "<i2H6i", payload, 0
    )
    return Hs2xAttitude(
        tag="HCP", time_ms=time_ms, device=device,
        roll_millideg=roll, pitch_millideg=pitch,
        unassigned=(w6, w8, w12, w20, w28),
    )


def _decode_position(payload: bytes) -> Record:
    (time_ms, w4, easting, northing, easting2, northing2, w24, w28,
     h32, h34, h36, h38) = struct.unpack_from("<2i4i2i4H", payload, 0)
    latitude, longitude, height, utc = struct.unpack_from("<4d", payload, 40)
    return Hs2xPosition(
        tag="POS", time_ms=time_ms, easting_cm=easting, northing_cm=northing,
        latitude_packed=latitude, longitude_packed=longitude,
        ellipsoid_height=height, utc_seconds=utc,
        unassigned=(w4, easting2, northing2, w24, w28, h32, h34, h36, h38),
    )


def _decode_tide(payload: bytes) -> Record:
    time_ms, device, w6, w8, w12, tide, w18 = struct.unpack_from("<i2H2i2H", payload, 0)
    return Hs2xTide(
        tag="TID", time_ms=time_ms, device=device, tide_cm=tide,
        unassigned=(w6, w8, w12, w18),
    )


def _decode_ping(payload: bytes) -> Record:
    (time_ms, w4, device, sonar_type, beam_count, w14, sound_velocity,
     ping_number, easting, northing, w32, heading, w40, w44, roll, pitch,
     w56, w60, w64, w66) = struct.unpack_from("<iI4H2i2i2i2i2i2i2H", payload, 0)
    return Hs2xPing(
        tag="PING", time_ms=time_ms, device=device, sonar_type=sonar_type,
        beam_count=beam_count, sound_velocity_cm_s=sound_velocity,
        ping_number=ping_number, easting_cm=easting, northing_cm=northing,
        heading_millideg=heading, roll_millideg=roll, pitch_millideg=pitch,
        unassigned=(w4, w14, w32, w40, w44, w56, w60, w64, w66),
        tail=payload[68:],
    )


def _decode_sounding(payload: bytes) -> Record:
    values = struct.unpack_from("<7i2h2i2h2i", payload, 0)
    return Hs2xSounding(
        tag="SND", easting_cm=values[0], northing_cm=values[1],
        elevation_cm=values[2], beam_angle_cdeg=values[3],
        unassigned=values[4:],
    )


def _decode_sidescan_header(payload: bytes) -> Record:
    (time_ms, device, port, starboard, w10, sound_velocity, ping_number,
     w20, w24, w28, w32, easting, northing, heading) = struct.unpack_from(
        "<i4H9i", payload, 0
    )
    return Hs2xSidescanHeader(
        tag="SSH", time_ms=time_ms, device=device, port_samples=port,
        starboard_samples=starboard, sound_velocity_cm_s=sound_velocity,
        ping_number=ping_number, easting_cm=easting, northing_cm=northing,
        heading_millideg=heading, unassigned=(w10, w20, w24, w28, w32),
    )


def _decode_sidescan_data(payload: bytes) -> Record:
    return Hs2xSidescanData(tag="SSD", samples=payload)


_DECODERS: dict[int, Callable[[bytes], Record]] = {
    _FILE_HEADER_TYPE: _decode_file_header,
    60: _decode_tide,
    61: _decode_time_mark,
    62: _decode_heading,
    63: _decode_attitude,
    67: _decode_position,
    68: _decode_ping,
    69: _decode_sounding,
    70: _decode_sidescan_header,
    72: _decode_sidescan_data,
}


def _decode(frame: Hs2xFrame) -> Record:
    decoder = _DECODERS.get(frame.record_type)
    if decoder is None:
        return Hs2xOpaque(
            tag=f"T{frame.record_type}", record_type=frame.record_type,
            payload=frame.payload,
        )
    try:
        return decoder(frame.payload)
    except (struct.error, ValueError) as error:
        return MalformedRecord(
            tag=_TAGS.get(frame.record_type, f"T{frame.record_type}"),
            fields=(
                f"type={frame.record_type}",
                f"offset={frame.offset}",
                f"declared_size={frame.size}",
            ),
            error=f"truncated or undecodable payload: {error}",
        )


def parse_hs2x(source: str | Path | bytes) -> Iterator[Record]:
    """Stream typed records from an HS2X file (path or bytes).

    A synthetic :class:`~hydroformats.records.EndOfHeader` is emitted
    before the first data-region record (types 60–72), mirroring the
    ``EOH`` boundary of the text dialects so the session layer treats all
    three uniformly. The marker is synthesized — HS2X has no EOH record.
    """
    emitted_eoh = False
    for frame in iter_frames(source):
        if not emitted_eoh and frame.record_type in _DATA_TYPES:
            yield EndOfHeader(tag="EOH")
            emitted_eoh = True
        yield _decode(frame)
