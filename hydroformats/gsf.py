"""Generic Sensor Format (GSF) reader (swath bathymetry interchange).

A GSF file is a sequence of records, each framed by two big-endian 32-bit
words: the size of the data portion, then an identifier word whose bit 31
is a checksum flag, bits 21-12 a registry number and bits 11-0 a data
type number (spec sections 3.6.2, 3.7 and 4.3.1 pin big endian, most
significant byte first, throughout). When the checksum flag is set a
third 32-bit word carries a checksum of the data bytes. Data records are
padded to a multiple of four bytes.

Every layout here is hand-built from the specification document only
(anchor S7 in docs/FORMAT-SOURCES.md):

- Generic Sensor Format Specification, version 03.09, Leidos doc 98-16v,
  26 April 2019.
  https://www3.mbari.org/data/mbsystem/formatdoc/GSF/gsf_spec_03.09.pdf

The reference C library (gsflib, LGPL) was deliberately not consulted;
where the specification is silent the reading chosen is documented in the
relevant docstring and summarized here:

- The declared record size is read as the data portion only; the optional
  checksum word is framing, matching Figure 4-5 where it sits beside the
  size and identifier words. Its "modulo-32" sum is read as a 32-bit
  modular sum of the data bytes. Mismatches are reported on the frame,
  never raised.
- Beam array element width is derived from subrecord size divided by beam
  count (the spec leaves the field-size nibble encoding of the scale
  factor compression byte undefined). Up to three trailing bytes are
  tolerated as padding.
- Attitude time offsets are read as milliseconds: the spec gives no unit,
  milliseconds is the only common unit under which the record's own
  sixty-second ceiling binds against the 2-byte offset field, and the S7
  real sample confirms it (offset spans equal the base-time gaps between
  consecutive records). Raw offsets are preserved so callers can
  re-derive.
- Attitude measurements are decoded interleaved, one (time, pitch, roll,
  heave, heading) group per measurement: the spec table draws parallel
  arrays, but the real data proves interleaving (anchor errata in
  docs/FORMAT-SOURCES.md).
- Summary record depth extremes are read as centimeters, the integer
  depth convention used everywhere else in the format (correctors, SVP,
  single-beam depths); the summary table itself states no unit.

Unlike the SVLog dialect there is no sync marker to resynchronize on, so
an unreadable record header or a size pointing past the end of the file
ends the walk with a :class:`GsfGap` covering the remaining bytes; a
truncated final record degrades the same way, never an exception. Unknown
record and subrecord identifiers are skipped tolerantly and counted by
:func:`load_swath`. Raw observables are preserved: travel time and beam
angle arrays decode alongside depths so soundings can be re-reduced.
"""
from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .records import MalformedRecord, Record

HEADER = 1
SWATH_BATHYMETRY_PING = 2
SOUND_VELOCITY_PROFILE = 3
PROCESSING_PARAMETERS = 4
SENSOR_PARAMETERS = 5
COMMENT = 6
HISTORY = 7
NAVIGATION_ERROR = 8
SWATH_BATHY_SUMMARY = 9
SINGLE_BEAM_SOUNDING = 10
HV_NAVIGATION_ERROR = 11
ATTITUDE = 12

# Ping subrecord identifiers (Appendix A.2).
DEPTH_ARRAY = 1
ACROSS_TRACK_ARRAY = 2
ALONG_TRACK_ARRAY = 3
TRAVEL_TIME_ARRAY = 4
BEAM_ANGLE_ARRAY = 5
QUALITY_FACTOR_ARRAY = 9
NOMINAL_DEPTH_ARRAY = 14
QUALITY_FLAGS_ARRAY = 15
BEAM_FLAGS_ARRAY = 16
VERTICAL_ERROR_ARRAY = 19
HORIZONTAL_ERROR_ARRAY = 20
INTENSITY_SERIES_ARRAY = 21
SCALE_FACTORS = 100
_SENSOR_SPECIFIC_MIN = 102  # sensor-specific subrecords begin here

_TAGS = {
    HEADER: "HDR",
    SWATH_BATHYMETRY_PING: "PING",
    SOUND_VELOCITY_PROFILE: "SVP",
    PROCESSING_PARAMETERS: "PRM",
    COMMENT: "COM",
    HISTORY: "HST",
    SWATH_BATHY_SUMMARY: "SUM",
    ATTITUDE: "ATT",
}

_RECORD_HEAD = struct.Struct(">II")
_CHECKSUM = struct.Struct(">I")
# Ping header, Table 4-3: 56 bytes for GSF v03.01 and higher, 42 bytes
# (ending after SPEED) for files written by earlier versions.
_PING_HEAD_V3 = struct.Struct(">4i2hHhhiH3h2H3ih")
_PING_HEAD_V2 = struct.Struct(">4i2hHhhiH3h2H")
_SCALE_ENTRY = struct.Struct(">BBHIi")
_SVP_FIXED = struct.Struct(">7i")
_ATTITUDE_FIXED = struct.Struct(">2ih")
_COMMENT_FIXED = struct.Struct(">3i")
_TIME_AND_COUNT = struct.Struct(">2ih")

_E7 = 1e-7  # ten-millionths of degrees (spec 3.6.3.2)


def beam_usable(flag: int) -> bool:
    """True when a beam flag's ignore bit (bit 0) is clear (Appendix C.2)."""
    return not flag & 0x01


# --------------------------------------------------------------------------
# record walking
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GsfFrame:
    """One framed record: identifier split per spec 4.3.1.2, raw payload.

    ``checksum_ok`` is True when no checksum is present or when the
    32-bit modular sum of the payload matches the stored word; a
    mismatch is reported here, never raised.
    """

    offset: int
    registry: int
    data_type: int
    payload: bytes
    checksum: int | None = None
    checksum_ok: bool = True

    @property
    def identifier(self) -> int:
        """Registry and data type recombined (checksum flag stripped)."""
        return (self.registry << 12) | self.data_type


@dataclass(frozen=True)
class GsfGap:
    """Bytes that could not be framed: a truncated final record, a size
    pointing past the end of the file, or a non-GSF tail. GSF has no sync
    marker to resynchronize on, so a gap always runs to the end."""

    offset: int
    size: int


def _read_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def iter_records(source: str | Path | bytes) -> Iterator[GsfFrame | GsfGap]:
    """Walk the record chain; never raises on content.

    Yields :class:`GsfFrame` for every record whose framing is intact and
    a single trailing :class:`GsfGap` for anything that is not (there is
    no resynchronization: GSF records carry explicit sizes but no sync
    pattern, so the first broken header ends the walk).
    """
    data = _read_bytes(source)
    n = len(data)
    position = 0
    while position + 8 <= n:
        size, identifier = _RECORD_HEAD.unpack_from(data, position)
        checksum_flag = bool(identifier & 0x80000000)
        body = position + 8 + (4 if checksum_flag else 0)
        if size > n - body:
            break
        checksum: int | None = None
        checksum_ok = True
        if checksum_flag:
            (checksum,) = _CHECKSUM.unpack_from(data, position + 8)
        payload = data[body:body + size]
        if checksum is not None:
            checksum_ok = checksum == sum(payload) & 0xFFFFFFFF
        yield GsfFrame(
            offset=position, registry=(identifier >> 12) & 0x3FF,
            data_type=identifier & 0xFFF, payload=payload,
            checksum=checksum, checksum_ok=checksum_ok,
        )
        position = body + size
    if position < n:
        yield GsfGap(offset=position, size=n - position)


# --------------------------------------------------------------------------
# typed records (layouts per the spec tables; anchor S7)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GsfTimed(Record):
    """Base for records led by a POSIX time (spec 3.6.6): 4-byte seconds
    since the epoch plus 4-byte nanoseconds, both signed, UTC."""

    time_sec: int
    time_nsec: int

    @property
    def time(self) -> float:
        """Seconds since the epoch, UTC."""
        return self.time_sec + self.time_nsec / 1e9


@dataclass(frozen=True)
class GsfHeader(Record):
    """HEADER (id 1): the file's GSF version as text, e.g. ``GSF-v03.09``
    (Table 4-2, 12 bytes). The major version selects the ping header
    length for the rest of the file."""

    version: str

    def _part(self, index: int) -> int | None:
        _, _, tail = self.version.partition("v")
        pieces = tail.split(".")
        if len(pieces) > index and pieces[index].isdigit():
            return int(pieces[index])
        return None

    @property
    def version_major(self) -> int | None:
        return self._part(0)

    @property
    def version_minor(self) -> int | None:
        return self._part(1)


@dataclass(frozen=True)
class GsfScaleFactor:
    """One scale-factor array element (spec 4.3.4.2, Figure 4-7): the
    subrecord id it scales, the compression byte (high nibble field size
    control, low nibble reserved for a future compression algorithm), a
    multiplier and an offset. Engineering value = stored / multiplier -
    offset. The multiplier is read unsigned and the offset signed; the
    spec draws the words without stating signedness, and only a positive
    multiplier can divide."""

    subrecord_id: int
    compression: int
    multiplier: int
    offset: int


@dataclass(frozen=True)
class GsfPing(GsfTimed):
    """SWATH_BATHYMETRY_PING (id 2): fixed ping header plus beam arrays.

    Header scalars decode to engineering units per Table 4-3: longitude
    and latitude from ten-millionths of degrees, correctors and heave
    from centimeters, angles from hundredths of degrees, speed from
    hundredths of knots, height, separation and the GPS tide corrector
    from thousandths of meters (the last three exist only in the 56-byte
    header of GSF v03.01+, so they are None for files whose header record
    declares an older version). ``ping_flags`` is kept as the raw 16-bit
    word; bit 0 set means the whole ping is unusable (Appendix C.1).

    Beam arrays are tuples in swath order, first beam outermost to port,
    decoded through the scale factors in force (this ping's, or the last
    ping's that carried the subrecord). ``beam_flags`` are raw 8-bit
    words (bit 0 set means ignore the beam, Appendix C.2; see
    :func:`beam_usable`). ``quality_factors`` are sensor-dependent units,
    scaled only when the writer supplied a scale entry. ``extra_arrays``
    carries any other decoded standard array as (subrecord id, values)
    pairs. Subrecords that cannot be decoded faithfully (sensor-specific,
    intensity series, unknown ids, arrays whose scale factor is missing
    or invalid) are skipped and listed in ``skipped_subrecords`` as
    (subrecord id, payload size) pairs; ``sensor_id`` is the first
    sensor-specific subrecord id seen, which identifies the sonar
    (spec 4.3.4.27).
    """

    longitude: float
    latitude: float
    num_beams: int
    center_beam: int
    ping_flags: int
    reserved: int
    tide_corrector_m: float
    depth_corrector_m: float
    heading_degrees: float
    pitch_degrees: float
    roll_degrees: float
    heave_m: float
    course_degrees: float
    speed_knots: float
    height_m: float | None = None
    separation_m: float | None = None
    gps_tide_corrector_m: float | None = None
    spare: int | None = None
    scale_factors: tuple[GsfScaleFactor, ...] = ()
    depths: tuple[float, ...] | None = None
    nominal_depths: tuple[float, ...] | None = None
    across_track: tuple[float, ...] | None = None
    along_track: tuple[float, ...] | None = None
    travel_times: tuple[float, ...] | None = None
    beam_angles: tuple[float, ...] | None = None
    beam_flags: tuple[int, ...] | None = None
    quality_factors: tuple[float, ...] | tuple[int, ...] | None = None
    vertical_errors: tuple[float, ...] | None = None
    horizontal_errors: tuple[float, ...] | None = None
    extra_arrays: tuple[tuple[int, tuple[float, ...] | tuple[int, ...]], ...] = ()
    sensor_id: int | None = None
    skipped_subrecords: tuple[tuple[int, int], ...] = ()

    @property
    def usable(self) -> bool:
        """True when the ping flag's ignore bit (bit 0) is clear."""
        return not self.ping_flags & 0x01


@dataclass(frozen=True)
class GsfSvp(Record):
    """SOUND_VELOCITY_PROFILE (id 3): observation and application times,
    position, then depth and sound speed pairs (Table 4-6; 28-byte fixed
    part). Depths decode from centimeters, sound speeds from hundredths
    of meters per second."""

    observed_sec: int
    observed_nsec: int
    applied_sec: int
    applied_nsec: int
    longitude: float
    latitude: float
    depths_m: tuple[float, ...]
    sound_speeds_mps: tuple[float, ...]

    @property
    def num_points(self) -> int:
        return len(self.depths_m)


@dataclass(frozen=True)
class GsfAttitude(Record):
    """ATTITUDE (id 12): full-rate motion series (Table 4-14).

    A base time and a count, then one (time offset, pitch, roll, heave,
    heading) group of 2-byte integers per measurement. **The spec table
    lists the five series as separate arrays, but real GSF data proves
    they are interleaved per measurement**: under the interleaved reading
    the S7 sample's offsets climb monotonically in 20 ms steps and the
    angles match the ping headers, while the parallel reading yields
    garbage (see docs/FORMAT-SOURCES.md, anchor errata). Angles decode
    from hundredths of degrees (spec 3.6.7; the table's stray T types for
    heave and heading are read as the binary integers every other
    measurement uses, heading unsigned per 3.6.7.1) and heave from
    centimeters. The offsets are read as milliseconds: the spec states no
    unit, but milliseconds is the only common unit under which the
    record's sixty-second ceiling binds against a 2-byte field, and the
    S7 sample's offset spans equal the base-time gaps between consecutive
    records exactly. Raw offsets ride along in ``time_offsets``.
    """

    base_sec: int
    base_nsec: int
    time_offsets: tuple[int, ...]
    pitch_degrees: tuple[float, ...]
    roll_degrees: tuple[float, ...]
    heave_m: tuple[float, ...]
    heading_degrees: tuple[float, ...]

    @property
    def times(self) -> tuple[float, ...]:
        """Epoch seconds for every measurement (offsets read as ms)."""
        base = self.base_sec + self.base_nsec / 1e9
        return tuple(base + offset / 1000.0 for offset in self.time_offsets)


@dataclass(frozen=True)
class GsfComment(GsfTimed):
    """COMMENT (id 6): time plus free text (Table 4-10; 12-byte fixed
    part). Decoded latin-1 with trailing NULs stripped, never raises."""

    text: str


@dataclass(frozen=True)
class GsfHistory(GsfTimed):
    """HISTORY (id 7): one processing audit entry (Table 4-11): machine,
    operator, command line and comment as length-prefixed text."""

    machine: str
    operator: str
    command: str
    comment: str


@dataclass(frozen=True)
class GsfProcessingParameters(GsfTimed):
    """PROCESSING_PARAMETERS (id 4): length-prefixed ``KEYWORD=VALUE``
    strings describing the processing state (Table 4-7). ``texts`` is
    verbatim (NUL-stripped); ``parameters`` splits on the first equals
    sign, leaving the value empty when there is none."""

    texts: tuple[str, ...]

    @property
    def parameters(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (key.strip(), value.strip())
            for key, _, value in (text.partition("=") for text in self.texts)
        )


@dataclass(frozen=True)
class GsfSummary(Record):
    """SWATH_BATHY_SUMMARY (id 9): temporal and spatial extremes of the
    file (Table 4-5, 40 bytes; latitude precedes longitude here, unlike
    the ping header). Depth extremes decode from centimeters: the table
    states no unit, and centimeters is the format's integer depth
    convention everywhere a unit is stated."""

    begin_sec: int
    begin_nsec: int
    end_sec: int
    end_nsec: int
    min_latitude: float
    min_longitude: float
    max_latitude: float
    max_longitude: float
    min_depth_m: float
    max_depth_m: float


# --------------------------------------------------------------------------
# ping subrecord machinery
# --------------------------------------------------------------------------

# Standard beam arrays decoded onto named GsfPing fields:
# id -> (field name, signed integers, scale factors required)
_NAMED_ARRAYS = {
    DEPTH_ARRAY: ("depths", False),
    ACROSS_TRACK_ARRAY: ("across_track", True),
    ALONG_TRACK_ARRAY: ("along_track", True),
    TRAVEL_TIME_ARRAY: ("travel_times", False),
    BEAM_ANGLE_ARRAY: ("beam_angles", True),
    NOMINAL_DEPTH_ARRAY: ("nominal_depths", False),
    VERTICAL_ERROR_ARRAY: ("vertical_errors", False),
    HORIZONTAL_ERROR_ARRAY: ("horizontal_errors", False),
}

# Remaining standard scaled arrays, decoded into extra_arrays:
# id -> signed, per the field types of Table 4-3.
_EXTRA_ARRAYS = {
    6: True,    # mean calibrated amplitude, dB
    7: False,   # mean relative amplitude, dB
    8: False,   # echo width, seconds
    10: True,   # receive heave, meters
    11: False,  # depth error, meters (obsolete)
    12: False,  # across track error, meters (obsolete)
    13: False,  # along track error, meters (obsolete)
    17: True,   # signal to noise
    18: False,  # beam angle forward, degrees
    22: False,  # sector number
    23: False,  # detection info
    24: True,   # incident beam adjustment, degrees
    25: False,  # system cleaning
    26: True,   # Doppler correction, seconds
    27: False,  # sonar vertical uncertainty, meters
    28: False,  # sonar horizontal uncertainty, meters
    29: False,  # detection window
    30: False,  # mean absorption coefficient
}

_WIDTH_CODES = {1: "b", 2: "h", 4: "i"}


def _split_subrecords(payload: bytes, start: int) -> tuple[
        list[tuple[int, bytes]], list[tuple[int, int]]]:
    """Walk the subrecord chain (u8 id, u24 size per Figure 4-6).

    Up to three leftover bytes are record padding (spec 4.3.1.4). A
    subrecord whose declared size overruns the record is truncated: it is
    reported as skipped and ends the walk, never raises.
    """
    subrecords: list[tuple[int, bytes]] = []
    skipped: list[tuple[int, int]] = []
    position = start
    end = len(payload)
    while position + 4 <= end:
        sub_id = payload[position]
        size = int.from_bytes(payload[position + 1:position + 4], "big")
        if size > end - position - 4:
            skipped.append((sub_id, end - position - 4))
            break
        subrecords.append((sub_id, payload[position + 4:position + 4 + size]))
        position += 4 + size
    return subrecords, skipped


def _parse_scale_factors(payload: bytes) -> tuple[GsfScaleFactor, ...]:
    (count,) = struct.unpack_from(">i", payload, 0)
    needed = 4 + count * _SCALE_ENTRY.size
    if count < 0 or len(payload) < needed:
        raise ValueError(f"{count} scale factors need {needed} bytes, "
                         f"got {len(payload)}")
    return tuple(
        GsfScaleFactor(subrecord_id=sub_id, compression=compression,
                       multiplier=multiplier, offset=offset)
        for sub_id, compression, _, multiplier, offset in (
            _SCALE_ENTRY.unpack_from(payload, 4 + i * _SCALE_ENTRY.size)
            for i in range(count)
        )
    )


def _raw_beam_values(payload: bytes, num_beams: int,
                     signed: bool) -> tuple[int, ...] | None:
    """Fixed-width integers for every beam, or None when the size does
    not fit any width. The element width is derived from the subrecord
    size (the spec leaves the field-size nibble encoding undefined); up
    to three trailing pad bytes are tolerated."""
    if num_beams <= 0:
        return None
    width = len(payload) // num_beams
    if width not in _WIDTH_CODES or len(payload) - width * num_beams >= 4:
        return None
    code = _WIDTH_CODES[width]
    return struct.unpack_from(
        f">{num_beams}{code if signed else code.upper()}", payload, 0)


def _packed_quality_flags(payload: bytes, num_beams: int) -> tuple[int, ...] | None:
    """QUALITY_FLAGS_ARRAY (id 15, obsolete): two bits per beam, packed
    four to a byte. Read most significant bits first, in keeping with the
    format's byte order; the spec does not state the bit order."""
    if num_beams <= 0 or len(payload) < (num_beams + 3) // 4:
        return None
    return tuple(
        (payload[i // 4] >> (6 - 2 * (i % 4))) & 0x3 for i in range(num_beams)
    )


def _scaled(raw: tuple[int, ...], entry: GsfScaleFactor) -> tuple[float, ...]:
    """Engineering units per spec 4.3.4.2: divide by the multiplier, then
    subtract the offset."""
    return tuple(value / entry.multiplier - entry.offset for value in raw)


def _usable_entry(entry: GsfScaleFactor | None) -> bool:
    """A scale entry that can be applied faithfully: present, a nonzero
    multiplier, and no compression algorithm in the reserved low nibble."""
    return (entry is not None and entry.multiplier != 0
            and not entry.compression & 0x0F)


# --------------------------------------------------------------------------
# per-id decoders (payload -> Record)
# --------------------------------------------------------------------------


class _ReaderState:
    """Carried across records of one file: the scale factors in force
    (spec 4.3.4: they apply until a new instance appears; a new subrecord
    updates the ids it lists) and the ping header length implied by the
    header record's major version."""

    def __init__(self) -> None:
        self.scales: dict[int, GsfScaleFactor] = {}
        self.ping_head = _PING_HEAD_V3

    def note_header(self, header: GsfHeader) -> None:
        major = header.version_major
        if major is not None and major < 3:
            self.ping_head = _PING_HEAD_V2
        else:
            self.ping_head = _PING_HEAD_V3


def _decode_header(payload: bytes, state: _ReaderState) -> Record:
    header = GsfHeader(
        tag="HDR", version=payload.decode("latin-1").rstrip("\x00 "))
    state.note_header(header)
    return header


def _decode_ping(payload: bytes, state: _ReaderState) -> Record:
    head = state.ping_head
    values = head.unpack_from(payload, 0)
    modern = head is _PING_HEAD_V3
    subrecords, skipped = _split_subrecords(payload, head.size)
    for sub_id, body in subrecords:
        if sub_id == SCALE_FACTORS:
            for entry in _parse_scale_factors(body):
                state.scales[entry.subrecord_id] = entry
    num_beams = values[4]
    arrays: dict[str, tuple] = {}
    extras: list[tuple[int, tuple]] = []
    sensor_id: int | None = None
    for sub_id, body in subrecords:
        if sub_id == SCALE_FACTORS:
            continue
        entry = state.scales.get(sub_id)
        if sub_id == BEAM_FLAGS_ARRAY:
            raw = _raw_beam_values(body, num_beams, signed=False)
            if raw is not None:
                arrays["beam_flags"] = raw
                continue
        elif sub_id == QUALITY_FLAGS_ARRAY:
            packed = _packed_quality_flags(body, num_beams)
            if packed is not None:
                extras.append((sub_id, packed))
                continue
        elif sub_id == QUALITY_FACTOR_ARRAY:
            raw = _raw_beam_values(body, num_beams, signed=False)
            if raw is not None and entry is None:
                arrays["quality_factors"] = raw
                continue
            if raw is not None and _usable_entry(entry):
                arrays["quality_factors"] = _scaled(raw, entry)
                continue
        elif sub_id in _NAMED_ARRAYS and _usable_entry(entry):
            name, signed = _NAMED_ARRAYS[sub_id]
            raw = _raw_beam_values(body, num_beams, signed)
            if raw is not None:
                arrays[name] = _scaled(raw, entry)
                continue
        elif sub_id in _EXTRA_ARRAYS and _usable_entry(entry):
            raw = _raw_beam_values(body, num_beams, _EXTRA_ARRAYS[sub_id])
            if raw is not None:
                extras.append((sub_id, _scaled(raw, entry)))
                continue
        if sensor_id is None and sub_id >= _SENSOR_SPECIFIC_MIN:
            sensor_id = sub_id
        skipped.append((sub_id, len(body)))
    return GsfPing(
        tag="PING", time_sec=values[0], time_nsec=values[1],
        longitude=values[2] * _E7, latitude=values[3] * _E7,
        num_beams=num_beams, center_beam=values[5], ping_flags=values[6],
        reserved=values[7], tide_corrector_m=values[8] / 100.0,
        depth_corrector_m=values[9] / 100.0,
        heading_degrees=values[10] / 100.0, pitch_degrees=values[11] / 100.0,
        roll_degrees=values[12] / 100.0, heave_m=values[13] / 100.0,
        course_degrees=values[14] / 100.0, speed_knots=values[15] / 100.0,
        height_m=values[16] / 1000.0 if modern else None,
        separation_m=values[17] / 1000.0 if modern else None,
        gps_tide_corrector_m=values[18] / 1000.0 if modern else None,
        spare=values[19] if modern else None,
        scale_factors=tuple(sorted(state.scales.values(),
                                   key=lambda e: e.subrecord_id)),
        extra_arrays=tuple(extras), sensor_id=sensor_id,
        skipped_subrecords=tuple(skipped), **arrays,
    )


def _decode_svp(payload: bytes, _: _ReaderState) -> Record:
    (obs_sec, obs_nsec, app_sec, app_nsec, longitude, latitude,
     count) = _SVP_FIXED.unpack_from(payload, 0)
    needed = _SVP_FIXED.size + count * 8
    if count < 0 or len(payload) < needed:
        raise ValueError(f"{count} points need {needed} bytes, got {len(payload)}")
    pairs = struct.unpack_from(f">{2 * count}i", payload, _SVP_FIXED.size)
    return GsfSvp(
        tag="SVP", observed_sec=obs_sec, observed_nsec=obs_nsec,
        applied_sec=app_sec, applied_nsec=app_nsec,
        longitude=longitude * _E7, latitude=latitude * _E7,
        depths_m=tuple(d / 100.0 for d in pairs[0::2]),
        sound_speeds_mps=tuple(v / 100.0 for v in pairs[1::2]),
    )


def _decode_attitude(payload: bytes, _: _ReaderState) -> Record:
    base_sec, base_nsec, count = _ATTITUDE_FIXED.unpack_from(payload, 0)
    needed = _ATTITUDE_FIXED.size + count * 10
    if count < 0 or len(payload) < needed:
        raise ValueError(f"{count} measurements need {needed} bytes, "
                         f"got {len(payload)}")
    values = struct.unpack_from(">" + "4hH" * count, payload, _ATTITUDE_FIXED.size)
    rows = [values[5 * i:5 * i + 5] for i in range(count)]
    return GsfAttitude(
        tag="ATT", base_sec=base_sec, base_nsec=base_nsec,
        time_offsets=tuple(row[0] for row in rows),
        pitch_degrees=tuple(row[1] / 100.0 for row in rows),
        roll_degrees=tuple(row[2] / 100.0 for row in rows),
        heave_m=tuple(row[3] / 100.0 for row in rows),
        heading_degrees=tuple(row[4] / 100.0 for row in rows),
    )


def _text(payload: bytes) -> str:
    return payload.decode("latin-1").rstrip("\x00")


def _decode_comment(payload: bytes, _: _ReaderState) -> Record:
    time_sec, time_nsec, length = _COMMENT_FIXED.unpack_from(payload, 0)
    if length < 0 or len(payload) < 12 + length:
        raise ValueError(f"comment of {length} bytes in a {len(payload)} "
                         f"byte payload")
    return GsfComment(tag="COM", time_sec=time_sec, time_nsec=time_nsec,
                      text=_text(payload[12:12 + length]))


def _take_text(payload: bytes, position: int) -> tuple[str, int]:
    (size,) = struct.unpack_from(">h", payload, position)
    end = position + 2 + size
    if size < 0 or len(payload) < end:
        raise ValueError(f"text of {size} bytes at offset {position} overruns "
                         f"a {len(payload)} byte payload")
    return _text(payload[position + 2:end]), end


def _decode_history(payload: bytes, _: _ReaderState) -> Record:
    time_sec, time_nsec = struct.unpack_from(">2i", payload, 0)
    machine, position = _take_text(payload, 8)
    operator, position = _take_text(payload, position)
    command, position = _take_text(payload, position)
    comment, _ = _take_text(payload, position)
    return GsfHistory(tag="HST", time_sec=time_sec, time_nsec=time_nsec,
                      machine=machine, operator=operator, command=command,
                      comment=comment)


def _decode_parameters(payload: bytes, _: _ReaderState) -> Record:
    time_sec, time_nsec, count = _TIME_AND_COUNT.unpack_from(payload, 0)
    texts: list[str] = []
    position = _TIME_AND_COUNT.size
    for _index in range(count):
        text, position = _take_text(payload, position)
        texts.append(text)
    return GsfProcessingParameters(tag="PRM", time_sec=time_sec,
                                   time_nsec=time_nsec, texts=tuple(texts))


def _decode_summary(payload: bytes, _: _ReaderState) -> Record:
    values = struct.unpack_from(">10i", payload, 0)
    return GsfSummary(
        tag="SUM", begin_sec=values[0], begin_nsec=values[1],
        end_sec=values[2], end_nsec=values[3],
        min_latitude=values[4] * _E7, min_longitude=values[5] * _E7,
        max_latitude=values[6] * _E7, max_longitude=values[7] * _E7,
        min_depth_m=values[8] / 100.0, max_depth_m=values[9] / 100.0,
    )


_DECODERS = {
    HEADER: _decode_header,
    SWATH_BATHYMETRY_PING: _decode_ping,
    SOUND_VELOCITY_PROFILE: _decode_svp,
    PROCESSING_PARAMETERS: _decode_parameters,
    COMMENT: _decode_comment,
    HISTORY: _decode_history,
    SWATH_BATHY_SUMMARY: _decode_summary,
    ATTITUDE: _decode_attitude,
}


def _decode(frame: GsfFrame, state: _ReaderState) -> Record | None:
    """Typed record for a known identifier, None for an unknown one.

    Only registry zero carries the standard record set (spec 4.3.1.2.1);
    private registries are skipped tolerantly. So are the standard ids
    without an anchored decoding need here: SENSOR_PARAMETERS, the
    obsolete NAVIGATION_ERROR and SINGLE_BEAM_SOUNDING records, and
    HV_NAVIGATION_ERROR.
    """
    if frame.registry != 0:
        return None
    decoder = _DECODERS.get(frame.data_type)
    if decoder is None:
        return None
    try:
        return decoder(frame.payload, state)
    except (struct.error, ValueError) as error:
        return MalformedRecord(
            tag=_TAGS[frame.data_type],
            fields=(
                f"record_id={frame.data_type}",
                f"offset={frame.offset}",
                f"payload_size={len(frame.payload)}",
            ),
            error=f"truncated or undecodable payload: {error}",
        )


def read_gsf(source: str | Path | bytes) -> Iterator[Record]:
    """Stream typed records from a GSF file (path or bytes), in file order.

    Records with unknown identifiers are skipped (use :func:`iter_records`
    to see them, or :func:`load_swath` to count them); known ids whose
    payload does not satisfy the spec layout yield
    :class:`~hydroformats.records.MalformedRecord`. Never raises on
    content.
    """
    state = _ReaderState()
    for event in iter_records(source):
        if not isinstance(event, GsfFrame):
            continue
        record = _decode(event, state)
        if record is not None:
            yield record


# --------------------------------------------------------------------------
# swath loading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GsfCounters:
    """Stream accounting from one :func:`load_swath` pass.

    ``records`` counts every intact frame, decoded or not.
    ``unknown_record_ids`` and ``unknown_subrecord_ids`` are (id, count)
    pairs in ascending id order; record ids combine registry and data
    type as in :attr:`GsfFrame.identifier`. ``bytes_skipped`` counts only
    bytes outside any intact frame (a truncated tail, non-GSF bytes):
    skipped records and subrecords are framed, so they are counted by id,
    not by byte.
    """

    records: int
    unknown_record_ids: tuple[tuple[int, int], ...]
    unknown_subrecord_ids: tuple[tuple[int, int], ...]
    bytes_skipped: int


@dataclass(frozen=True)
class GsfSwath:
    """One materialized GSF file, split into its working series.

    ``summary`` and ``header`` are the first of their kind seen (a file
    has at most one of each in practice). Malformed records are dropped
    here but still counted in ``counters.records``; use :func:`read_gsf`
    to see them.
    """

    header: GsfHeader | None
    pings: tuple[GsfPing, ...]
    svps: tuple[GsfSvp, ...]
    attitude: tuple[GsfAttitude, ...]
    comments: tuple[GsfComment, ...]
    history: tuple[GsfHistory, ...]
    processing_parameters: tuple[GsfProcessingParameters, ...]
    summary: GsfSummary | None
    counters: GsfCounters


def _sorted_counts(counts: dict[int, int]) -> tuple[tuple[int, int], ...]:
    return tuple(sorted(counts.items()))


def load_swath(source: str | Path | bytes) -> GsfSwath:
    """Materialize a whole GSF file into series (small files, tests).

    Preserves the raw observables the ping records carry: travel times
    and beam angles ride alongside depths so the soundings can be
    re-reduced under a corrected sound speed profile.
    """
    state = _ReaderState()
    header: GsfHeader | None = None
    summary: GsfSummary | None = None
    pings: list[GsfPing] = []
    svps: list[GsfSvp] = []
    attitude: list[GsfAttitude] = []
    comments: list[GsfComment] = []
    history: list[GsfHistory] = []
    parameters: list[GsfProcessingParameters] = []
    unknown_records: dict[int, int] = {}
    unknown_subrecords: dict[int, int] = {}
    records = skipped = 0
    for event in iter_records(source):
        if isinstance(event, GsfGap):
            skipped += event.size
            continue
        records += 1
        record = _decode(event, state)
        if record is None:
            key = event.identifier
            unknown_records[key] = unknown_records.get(key, 0) + 1
        elif isinstance(record, GsfPing):
            pings.append(record)
            for sub_id, _size in record.skipped_subrecords:
                unknown_subrecords[sub_id] = unknown_subrecords.get(sub_id, 0) + 1
        elif isinstance(record, GsfSvp):
            svps.append(record)
        elif isinstance(record, GsfAttitude):
            attitude.append(record)
        elif isinstance(record, GsfComment):
            comments.append(record)
        elif isinstance(record, GsfHistory):
            history.append(record)
        elif isinstance(record, GsfProcessingParameters):
            parameters.append(record)
        elif isinstance(record, GsfHeader) and header is None:
            header = record
        elif isinstance(record, GsfSummary) and summary is None:
            summary = record
    return GsfSwath(
        header=header, pings=tuple(pings), svps=tuple(svps),
        attitude=tuple(attitude), comments=tuple(comments),
        history=tuple(history), processing_parameters=tuple(parameters),
        summary=summary,
        counters=GsfCounters(
            records=records,
            unknown_record_ids=_sorted_counts(unknown_records),
            unknown_subrecord_ids=_sorted_counts(unknown_subrecords),
            bytes_skipped=skipped,
        ),
    )
