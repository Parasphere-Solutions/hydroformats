"""Blueprint Subsea Oculus reader (.oculus ViewPoint logs, raw streams).

The Oculus family (M370s, M750d, M1200d, M3000d) are multibeam imaging
sonars: acoustic cameras that resolve each ping into a fan of hundreds
of beams and record the echo strength every beam heard at every range
step. One ping is a two-dimensional image, one row per range line
(nearest first), one column per beam, plus a per-ping bearing table
giving each beam's direction in hundredths of a degree. The sonar
streams these as SimplePingResult network messages; Blueprint's
ViewPoint software records the stream into ``.oculus`` log files.

The log container is a 48-byte file header (magic 0x11223344, the text
"Oculus", a version word, an encryption word, and the recording start
time as a double of Unix seconds) followed by items: each a header
(magic 0xAABBCCDD, its own declared size, a type word, a version word,
a double timestamp, a compression word, original and payload sizes)
and its payload. Sonar pings are item type 10; the payload is one
Oculus network message, itself led by a 16-byte header (magic 0x4F53,
source and destination device ids, a message id, a message version and
a payload size). All multi-byte fields are little endian.

Every layout is anchored to verified-permissive sources and to real
recorded bytes (anchor S13 in docs/FORMAT-SOURCES.md): the BSD-3
liboculus wrapper (University of Washington) with its recorded test
captures, the BSD-3 files of ENSTA Bretagne's oculus_driver (the
Recorder container structs and python bindings; the repository's
GPL-headed files were not consulted), the MIT ESP3 MATLAB reader, and
the Apache-2.0 oculus-python package. Blueprint's own Oculus.h is GPL
licensed and was deliberately not consulted; no public ICD exists.
Validation data: a CC0 ViewPoint survey (Parnum et al. 2024) and the
BSD liboculus captures.

Readings the sources leave open are documented in the relevant
docstring and summarized here:

- A message version word of 2 selects the version 2 layout; anything
  else decodes as version 1. The real version 1 capture carries
  msgVersion 0, so the layout cannot be keyed on "version equals 1"
  (one consulted source requires 1 or 2 and would refuse the real
  bytes; see the anchor errata).
- Item payloads begin at the item header's own declared size, and the
  first item at the file header's. Real files declare 48 and 40 (the
  natural-alignment struct sizes); one consulted source computes the
  item header as 36 bytes, which the declared size overrules.
- The bytes between the bearing table and the declared image offset
  are unanchored filler (nonzero in real captures): the image is
  always sliced at ``image_offset``, never assumed to follow the
  bearings.
- The version 1 ping start word is carried verbatim (see the record
  docstring and the anchor errata).
- Compressed item payloads (compression word 1, a Qt qCompress/zlib
  wrapper per the S13 sources) are skipped and counted, not decoded:
  no capture with compression is in hand to validate against.
- Encrypted logs (a nonzero file-header encryption word) refuse
  loudly: the header decodes, then a MalformedRecord ends the walk.
- ViewPoint V2 writes a different, SQLite-based log that Blueprint
  states is not backward compatible; such files are recognized by the
  SQLite magic and refused loudly by name, never guessed at.

Unknown item types and non-ping message ids are skipped tolerantly and
counted by :func:`load_imaging`; garbage between items resynchronizes
on the next item magic; truncation degrades to
:class:`~hydroformats.records.MalformedRecord`, never exceptions.
"""
from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .oculus_records import OculusFileHeader, OculusPing, bytes_per_sample
from .records import MalformedRecord, Record

__all__ = [
    "FILE_MAGIC",
    "ITEM_MAGIC",
    "OCULUS_ID",
    "ITEM_TYPE_NAMES",
    "MESSAGE_SIMPLE_FIRE",
    "MESSAGE_PING_RESULT",
    "MESSAGE_SIMPLE_PING_RESULT",
    "MESSAGE_USER_CONFIG",
    "MESSAGE_DUMMY",
    "SONAR_ITEM",
    "OculusCounters",
    "OculusFileHeader",
    "OculusGap",
    "OculusImaging",
    "OculusItem",
    "OculusPing",
    "bytes_per_sample",
    "iter_items",
    "load_imaging",
    "read_oculus",
    "read_oculus_raw",
]

FILE_MAGIC = 0x11223344
ITEM_MAGIC = 0xAABBCCDD
OCULUS_ID = 0x4F53  # the bytes "SO" on the wire

# Message ids (MIT ESP3 reader; 0x23 empirically confirmed).
MESSAGE_SIMPLE_FIRE = 0x15
MESSAGE_PING_RESULT = 0x22
MESSAGE_SIMPLE_PING_RESULT = 0x23
MESSAGE_USER_CONFIG = 0x55
MESSAGE_DUMMY = 0xFF

SONAR_ITEM = 10  # rt_oculusSonar

# Item type names per the S13 Recorder enum. Only type 10 decodes here;
# the rest are recognized for counting. 1010 is an ENSTA recorder
# extension (a seconds/nanoseconds stamp), absent from ViewPoint files.
ITEM_TYPE_NAMES = {
    1: "settings", 2: "serialPort", 10: "oculusSonar", 11: "blueviewSonar",
    12: "rawVideo", 13: "h264Video", 14: "apBattery", 15: "apMissionProgress",
    16: "nortekDVL", 17: "apNavData", 18: "apDvlData", 19: "apAhrsData",
    20: "apSonarHeader", 21: "rawSonarImage", 22: "ahrsMtData2",
    23: "apVehicleInfo", 24: "apMarker", 25: "apGeoImageHeader",
    26: "apGeoImageData", 30: "sbgData", 500: "ocViewInfo",
    1010: "oculusSonarStamp",
}

_SQLITE_MAGIC = b"SQLite format 3\x00"

_FILE_HEADER = struct.Struct("<2I16s2H4xqd")   # 48 bytes
_ITEM_FIXED = struct.Struct("<2I2H4xdH2x2I")   # 36 named bytes of the header
_ITEM_SYNC = b"\xdd\xcc\xbb\xaa"               # item magic, little endian

_MSG_HEADER = struct.Struct("<5HIH")           # 16 bytes
_FIRE_TAIL = struct.Struct("<5B4d")            # 37 bytes at offset 16
_V1_TAIL = struct.Struct("<2I4dIBd2H3I")       # 69 bytes at offset 53
_V2_FIRE_EXT = struct.Struct("<9I")            # 36 bytes at offset 53
_V2_TAIL = struct.Struct("<2I8dBd2H7I")        # 113 bytes at offset 89

V1_PING_STRUCT_SIZE = 16 + _FIRE_TAIL.size + _V1_TAIL.size          # 122
V2_PING_STRUCT_SIZE = 16 + _FIRE_TAIL.size + _V2_FIRE_EXT.size + _V2_TAIL.size


# --------------------------------------------------------------------------
# item walking
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OculusItem:
    """One framed log item: the header's fields plus the raw payload.

    ``time`` is the recording PC's clock, seconds since the Unix
    epoch. ``compression`` is 0 for a verbatim payload (1 names a
    Qt-compressed payload per the S13 sources; such payloads ride
    along undecoded). ``original_size`` is the payload size before any
    compression. ``size_header`` is the header's own declared size (40
    in real files); the payload starts that far past ``offset``.
    """

    offset: int
    size_header: int
    item_type: int
    version: int
    time: float
    compression: int
    original_size: int
    payload: bytes


@dataclass(frozen=True)
class OculusGap:
    """Bytes outside any well-framed structure: garbage between items,
    an item whose declared size overruns the file, or a bad or missing
    file header. The scan resumes at the next item magic."""

    offset: int
    size: int
    error: str


def _read_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def _text(raw: bytes) -> str:
    """Text up to the first NUL, latin-1 (never raises), stripped."""
    return raw.split(b"\x00", 1)[0].decode("latin-1").strip()


def _parse_file_header(data: bytes) -> OculusFileHeader | OculusGap:
    if data[:len(_SQLITE_MAGIC)] == _SQLITE_MAGIC:
        return OculusGap(
            offset=0, size=len(data),
            error="SQLite database: a ViewPoint V2 log, a different format "
                  "this reader deliberately does not guess at",
        )
    if len(data) < _FILE_HEADER.size:
        return OculusGap(offset=0, size=len(data),
                         error=f"no room for a 48-byte file header in "
                               f"{len(data)} bytes")
    (magic, size_header, source, version, encryption, key,
     time) = _FILE_HEADER.unpack_from(data, 0)
    if magic != FILE_MAGIC:
        return OculusGap(offset=0, size=len(data),
                         error=f"file magic 0x{magic:08X}, "
                               f"expected 0x{FILE_MAGIC:08X}")
    if size_header < _FILE_HEADER.size:
        return OculusGap(offset=0, size=len(data),
                         error=f"declared header size {size_header} below "
                               f"the 48-byte layout")
    return OculusFileHeader(
        tag="HDR", magic=magic, size_header=size_header,
        source_text=_text(source), version=version, encryption=encryption,
        key=key, time=time, header_bytes=data[:_FILE_HEADER.size],
    )


def _try_item(data: bytes, offset: int) -> OculusItem | None:
    """One item at ``offset``, or None when the bytes there do not
    frame (bad magic, an undersized declared header, or a payload that
    overruns the file)."""
    (magic, size_header, item_type, version, time, compression, original,
     payload_size) = _ITEM_FIXED.unpack_from(data, offset)
    if magic != ITEM_MAGIC or size_header < _ITEM_FIXED.size:
        return None
    body = offset + size_header
    if body + payload_size > len(data):
        return None
    return OculusItem(
        offset=offset, size_header=size_header, item_type=item_type,
        version=version, time=time, compression=compression,
        original_size=original, payload=data[body:body + payload_size],
    )


def iter_items(source: str | Path | bytes) -> Iterator[
        OculusFileHeader | OculusItem | OculusGap]:
    """Walk a .oculus log: the file header, then every item, in file
    order; never raises on content.

    Yields :class:`OculusGap` for every byte range that does not frame
    (including a bad file header), resynchronizing on the next item
    magic, so one corrupt length cannot swallow the valid items behind
    it. An encrypted log yields its header and one gap covering the
    undecryptable remainder.
    """
    data = _read_bytes(source)
    n = len(data)
    header = _parse_file_header(data)
    yield header
    if isinstance(header, OculusGap):
        return
    if header.encryption:
        yield OculusGap(
            offset=header.size_header, size=n - header.size_header,
            error=f"encrypted log (encryption word "
                  f"{header.encryption}): nothing here decrypts",
        )
        return
    position = header.size_header
    gap_start = position
    while position + _ITEM_FIXED.size <= n:
        frame = _try_item(data, position)
        if frame is None:
            sync = data.find(_ITEM_SYNC, position + 1)
            position = sync if sync != -1 else n
            continue
        if position > gap_start:
            yield OculusGap(offset=gap_start, size=position - gap_start,
                            error="unframed bytes before the next item")
        yield frame
        position = gap_start = (frame.offset + frame.size_header
                                + len(frame.payload))
    if gap_start < n:
        yield OculusGap(offset=gap_start, size=n - gap_start,
                        error="unframed or truncated bytes at end of file")


# --------------------------------------------------------------------------
# ping decoding
# --------------------------------------------------------------------------


def _malformed(context: tuple[str, ...], error: str) -> MalformedRecord:
    return MalformedRecord(tag="PING", fields=context, error=error)


def _decode_ping(payload: bytes, log_time: float | None,
                 context: tuple[str, ...]) -> Record | None:
    """Typed record for one message payload, None for a non-ping
    message id, MalformedRecord when the bytes do not satisfy the
    layout."""
    if len(payload) < _MSG_HEADER.size:
        return _malformed(context, f"no room for a 16-byte message header "
                                   f"in {len(payload)} bytes")
    (oculus_id, src, dst, msg_id, msg_version, _,
     _) = _MSG_HEADER.unpack_from(payload, 0)
    if oculus_id != OCULUS_ID:
        return _malformed(context, f"message magic 0x{oculus_id:04X}, "
                                   f"expected 0x{OCULUS_ID:04X}")
    if msg_id != MESSAGE_SIMPLE_PING_RESULT:
        return None
    try:
        return _decode_simple_ping(payload, log_time, src, dst, msg_version)
    except (struct.error, ValueError) as error:
        return _malformed(context, f"truncated or undecodable ping: {error}")


def _decode_simple_ping(payload: bytes, log_time: float | None, src: int,
                        dst: int, msg_version: int) -> Record:
    fire = _FIRE_TAIL.unpack_from(payload, 16)
    if msg_version == 2:
        ext = _V2_FIRE_EXT.unpack_from(payload, 53)
        tail = _V2_TAIL.unpack_from(payload, 89)
        (ping_id, status, frequency, temperature, pressure, heading, pitch,
         roll, sos_used, start_time, data_size, resolution, n_ranges,
         n_beams, _, _, _, _, image_offset, image_size, message_size) = tail
        struct_size = V2_PING_STRUCT_SIZE
        versioned: dict = {
            "ext_flags": ext[0], "fire_reserved": tuple(ext[1:]),
            "heading_deg": heading, "pitch_deg": pitch, "roll_deg": roll,
            "ping_start_time_s": start_time, "ping_start_word": None,
        }
    else:
        tail = _V1_TAIL.unpack_from(payload, 53)
        (ping_id, status, frequency, temperature, pressure, sos_used,
         start_word, data_size, resolution, n_ranges, n_beams, image_offset,
         image_size, message_size) = tail
        struct_size = V1_PING_STRUCT_SIZE
        versioned = {
            "ext_flags": None, "fire_reserved": None, "heading_deg": None,
            "pitch_deg": None, "roll_deg": None, "ping_start_time_s": None,
            "ping_start_word": start_word,
        }
    sample_size = bytes_per_sample(data_size)
    if sample_size is None:
        raise ValueError(f"unknown data size word {data_size}")
    bearings_end = struct_size + 2 * n_beams
    if bearings_end > image_offset:
        raise ValueError(f"bearing table through {bearings_end} overlaps "
                         f"image offset {image_offset}")
    if image_offset + image_size > len(payload):
        raise ValueError(f"image through {image_offset + image_size} "
                         f"overruns the {len(payload)}-byte payload")
    bearings = struct.unpack_from(f"<{n_beams}h", payload, struct_size)
    image = payload[image_offset:image_offset + image_size]
    gains, samples = _split_gain_rows(fire[4], image, n_ranges, n_beams,
                                      sample_size)
    return OculusPing(
        tag="PING", src_device_id=src, dst_device_id=dst,
        message_version=msg_version, log_time=log_time, ping_id=ping_id,
        status=status, master_mode=fire[0], ping_rate_raw=fire[1],
        network_speed_raw=fire[2], gamma_correction=fire[3], flags=fire[4],
        range_setting=fire[5], gain_percent=fire[6],
        speed_of_sound_mps=fire[7], salinity_ppt=fire[8],
        frequency_hz=frequency, temperature_c=temperature,
        pressure_bar=pressure, speed_of_sound_used_mps=sos_used,
        data_size=data_size, range_resolution_m=resolution,
        n_ranges=n_ranges, n_beams=n_beams, image_offset=image_offset,
        image_size=image_size, message_size=message_size, bearings_raw=bearings,
        gains=gains, samples=samples, header_bytes=payload[:struct_size],
        **versioned,
    )


def _split_gain_rows(flags: int, image: bytes, n_ranges: int, n_beams: int,
                     sample_size: int) -> tuple[tuple[int, ...] | None, bytes]:
    """Split per-row gain words from the image when flags bit 2 says the
    sonar sent them (each row then starts with a 4-byte gain; S13).
    The declared image size must equal the row lattice exactly either
    way; a mismatch is a refusal, never a guess."""
    width = n_beams * sample_size
    if not flags & 0x04:
        if len(image) != n_ranges * width:
            raise ValueError(f"image size {len(image)} is not "
                             f"{n_ranges} rows of {width} bytes")
        return None, image
    stride = width + 4
    if len(image) != n_ranges * stride:
        raise ValueError(f"image size {len(image)} is not {n_ranges} "
                         f"gain-prefixed rows of {stride} bytes")
    gains = tuple(
        int.from_bytes(image[row * stride:row * stride + 4], "little")
        for row in range(n_ranges)
    )
    samples = b"".join(
        image[row * stride + 4:(row + 1) * stride] for row in range(n_ranges)
    )
    return gains, samples


# --------------------------------------------------------------------------
# streaming and loading
# --------------------------------------------------------------------------


def read_oculus(source: str | Path | bytes) -> Iterator[Record]:
    """Stream typed records from a .oculus log (path or bytes), in file
    order: the :class:`OculusFileHeader`, then one :class:`OculusPing`
    per sonar ping. Bytes that cannot be framed and payloads that do
    not satisfy the layout yield
    :class:`~hydroformats.records.MalformedRecord`; items of other
    types, non-ping message ids and compressed payloads are skipped
    (use :func:`iter_items` to see them, or :func:`load_imaging` to
    count them). Never raises on content.
    """
    for event in iter_items(source):
        if isinstance(event, OculusGap):
            yield MalformedRecord(
                tag="HDR" if event.offset == 0 else "ITEM",
                fields=(f"offset={event.offset}", f"size={event.size}"),
                error=event.error,
            )
        elif isinstance(event, OculusFileHeader):
            yield event
        elif event.item_type == SONAR_ITEM and not event.compression:
            record = _decode_ping(
                event.payload, event.time,
                (f"offset={event.offset}",
                 f"payload_size={len(event.payload)}"))
            if record is not None:
                yield record


def read_oculus_raw(source: str | Path | bytes) -> Iterator[Record]:
    """Stream typed records from a bare Oculus message stream: the raw
    network capture format (no log container), a concatenation of
    messages each led by the 16-byte header whose declared payload
    size frames the walk. Yields one :class:`OculusPing` per simple
    ping result; other message ids are skipped; bytes that do not
    frame yield :class:`~hydroformats.records.MalformedRecord` and the
    scan resumes at the next 0x4F53 marker. Never raises on content.
    """
    data = _read_bytes(source)
    n = len(data)
    position = 0
    gap_start = 0
    while position + _MSG_HEADER.size <= n:
        oculus_id, size = struct.unpack_from("<H8xI", data, position)
        end = position + _MSG_HEADER.size + size
        if oculus_id != OCULUS_ID or end > n:
            sync = data.find(b"SO", position + 1)
            position = sync if sync != -1 else n
            continue
        if position > gap_start:
            yield MalformedRecord(
                tag="MSG",
                fields=(f"offset={gap_start}",
                        f"size={position - gap_start}"),
                error="unframed bytes before message magic",
            )
        record = _decode_ping(data[position:end], None,
                              (f"offset={position}",))
        if record is not None:
            yield record
        position = gap_start = end
    if gap_start < n:
        yield MalformedRecord(
            tag="MSG",
            fields=(f"offset={gap_start}", f"size={n - gap_start}"),
            error="unframed bytes at end of stream",
        )


@dataclass(frozen=True)
class OculusCounters:
    """Stream accounting from one :func:`load_imaging` pass.

    ``items`` counts every framed item; ``pings`` every decoded ping.
    ``skipped_item_types`` counts items of types this module does not
    decode, ``unknown_message_ids`` sonar items whose message id is
    not the simple ping result, both as (value, count) pairs in
    ascending order. ``compressed_items`` counts sonar items skipped
    for a nonzero compression word. ``malformed`` counts records
    refused for layout violations; ``bytes_skipped`` counts only bytes
    outside any intact frame.
    """

    items: int
    pings: int
    skipped_item_types: tuple[tuple[int, int], ...]
    unknown_message_ids: tuple[tuple[int, int], ...]
    compressed_items: int
    malformed: int
    bytes_skipped: int


@dataclass(frozen=True)
class OculusImaging:
    """One materialized .oculus log: the file header, every ping in
    file order, and the stream counters. ``file_header`` is None when
    the input is not a V1 .oculus log at all; the malformed record is
    dropped here but counted (use :func:`read_oculus` to see it)."""

    file_header: OculusFileHeader | None
    pings: tuple[OculusPing, ...]
    counters: OculusCounters


def load_imaging(source: str | Path | bytes) -> OculusImaging:
    """Materialize a whole .oculus log (small files, tests).

    Every ping keeps its raw samples, bearing table and settings, so
    downstream imaging never has to reopen the file. Exported at the
    package level as ``load_oculus`` (the DDF reader holds the
    package-level ``load_imaging`` name).
    """
    file_header: OculusFileHeader | None = None
    pings: list[OculusPing] = []
    skipped: dict[int, int] = {}
    unknown_ids: dict[int, int] = {}
    items = compressed = malformed = bytes_skipped = 0
    for event in iter_items(source):
        if isinstance(event, OculusGap):
            malformed += 1
            bytes_skipped += event.size
        elif isinstance(event, OculusFileHeader):
            file_header = event
        elif event.item_type != SONAR_ITEM:
            items += 1
            skipped[event.item_type] = skipped.get(event.item_type, 0) + 1
        elif event.compression:
            items += 1
            compressed += 1
        else:
            items += 1
            record = _decode_ping(
                event.payload, event.time,
                (f"offset={event.offset}",
                 f"payload_size={len(event.payload)}"))
            if record is None:
                msg_id = _MSG_HEADER.unpack_from(event.payload, 0)[3]
                unknown_ids[msg_id] = unknown_ids.get(msg_id, 0) + 1
            elif isinstance(record, OculusPing):
                pings.append(record)
            else:
                malformed += 1
    return OculusImaging(
        file_header=file_header, pings=tuple(pings),
        counters=OculusCounters(
            items=items, pings=len(pings),
            skipped_item_types=tuple(sorted(skipped.items())),
            unknown_message_ids=tuple(sorted(unknown_ids.items())),
            compressed_items=compressed, malformed=malformed,
            bytes_skipped=bytes_skipped,
        ),
    )
