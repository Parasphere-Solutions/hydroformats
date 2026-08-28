"""Cerulean SVLog dialect parser (Surveyor 240-16 packet logging).

An ``.svlog`` file is a raw stream of Cerulean Ping Protocol packets as
captured by SonarView/SonarLink; ``.svlz`` is the same stream gzipped
(both are read transparently, sniffed by the gzip magic). Sessions
auto-split and files concatenate, so truncated tails, garbage between
frames, and multi-member gzip archives are all normal inputs: the scanner
verifies every checksum and resynchronizes on the next sync pair rather
than raising.

Framing (anchor S6 in docs/FORMAT-SOURCES.md): ``'B'``, ``'R'``, u16
payload length, u16 packet id, u8 source device, u8 destination device,
payload, u16 checksum (the 16-bit truncated sum of every preceding byte
in the packet). Multi-byte fields are little-endian; the vendor page
leaves byte order unstated, but the framing is documented as
byte-identical to the Blue Robotics Ping Protocol and the vendor's own
published sample log parses end-to-end little-endian with zero checksum
failures.

Packet layouts are hand-built from the public ICD only:

- https://docs.ceruleansonar.com/c/cerulean-ping-protocol/universal-packet-format.md
- https://docs.ceruleansonar.com/c/surveyor-240-16/application-programming-interface.md
  (per-packet subpages: atof_point_data, yz_point_data, attitude_report,
  water_stats, set_ping_parameters)
- https://docs.ceruleansonar.com/c/cerulean-ping-protocol/general-packet-definitions.md
  (device_information, nmea_wrapper, mavlink_wrapper)
- https://docs.ceruleansonar.com/c/sonarview/log-files (svlog/svlz)

Packet ids without a decoder are skipped tolerantly (counted by
:func:`load_survey`); known ids whose payload cannot satisfy the ICD
layout degrade to :class:`~hydroformats.records.MalformedRecord`, never
exceptions. Solving and georeferencing are out of scope here: this module
only surfaces the raw observables (angle, two-way time of flight, echoed
speed of sound) plus the one piece of format-defined geometry,
:func:`atof_to_yz`.
"""
from __future__ import annotations

import math
import struct
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .records import MalformedRecord, Record

_SYNC = b"BR"
_GZIP_MAGIC = b"\x1f\x8b"
_FRAME_HEAD = struct.Struct("<HHBB")  # after the sync pair
_CHECKSUM = struct.Struct("<H")
_MIN_FRAME = 10  # sync + header + checksum, empty payload

ATOF_POINT_DATA = 3012
YZ_POINT_DATA = 3011
ATTITUDE_REPORT = 504
WATER_STATS = 118
NMEA_WRAPPER = 109
MAVLINK_WRAPPER = 150
DEVICE_INFORMATION = 4
SET_PING_PARAMETERS = 3023

_TAGS = {
    DEVICE_INFORMATION: "DVI",
    NMEA_WRAPPER: "NMEA",
    WATER_STATS: "WTR",
    MAVLINK_WRAPPER: "MAV",
    ATTITUDE_REPORT: "ATT",
    YZ_POINT_DATA: "YZ",
    ATOF_POINT_DATA: "ATOF",
    SET_PING_PARAMETERS: "SPP",
}


# --------------------------------------------------------------------------
# frame scanning
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SvlogFrame:
    """One checksum-verified packet: header fields plus raw payload."""

    offset: int
    packet_id: int
    src: int
    dst: int
    payload: bytes


@dataclass(frozen=True)
class SvlogGap:
    """Bytes between verified frames: garbage, corruption, or truncation.

    ``checksum_failures`` counts the sync candidates inside the gap whose
    checksum did not verify (a truncated final packet is a gap with zero
    failures: its checksum never arrives, so it is never judged).
    """

    offset: int
    size: int
    checksum_failures: int


def _gunzip(data: bytes) -> bytes:
    """Decompress, tolerating truncated archives and concatenated members.

    A gzip stream cut off mid-write is normal for a logger: a truncated
    member yields whatever prefix still decodes, a corrupt one keeps the
    chunks decoded before the damage, and members are walked via
    ``unused_data`` so concatenated ``.svlz`` files read as one stream.
    Raw zlib is used deliberately; ``gzip.GzipFile`` discards
    already-decompressed bytes when truncation surfaces mid-read.
    """
    parts: list[bytes] = []
    remainder = data
    while remainder[:2] == _GZIP_MAGIC:
        decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
        buffer = remainder
        try:
            while buffer and not decompressor.eof:
                parts.append(decompressor.decompress(buffer, 1 << 20))
                buffer = decompressor.unconsumed_tail
        except zlib.error:
            break
        if not decompressor.eof:
            break
        remainder = decompressor.unused_data
    return b"".join(parts)


def _read_bytes(source: str | Path | bytes) -> bytes:
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    if data[:2] == _GZIP_MAGIC:
        return _gunzip(data)
    return data


def iter_frames(source: str | Path | bytes) -> Iterator[SvlogFrame | SvlogGap]:
    """Scan for framed packets; never raises on content.

    Yields :class:`SvlogFrame` for every checksum-verified packet and
    :class:`SvlogGap` for every unverified byte range, in file order.
    After a failed candidate the scan resumes two bytes past its sync, so
    a corrupt length field cannot swallow the valid frames behind it.
    Offsets index the decompressed stream when the input is gzipped.
    """
    data = _read_bytes(source)
    n = len(data)
    position = 0
    gap_start = 0
    failures = 0
    while True:
        sync = data.find(_SYNC, position)
        if sync == -1 or sync + _MIN_FRAME > n:
            break
        length, packet_id, src, dst = _FRAME_HEAD.unpack_from(data, sync + 2)
        end = sync + 8 + length + 2
        if end > n:
            position = sync + 2
            continue
        (declared,) = _CHECKSUM.unpack_from(data, end - 2)
        if declared != sum(data[sync:end - 2]) & 0xFFFF:
            failures += 1
            position = sync + 2
            continue
        if sync > gap_start:
            yield SvlogGap(offset=gap_start, size=sync - gap_start,
                           checksum_failures=failures)
            failures = 0
        yield SvlogFrame(offset=sync, packet_id=packet_id, src=src, dst=dst,
                         payload=data[sync + 8:end - 2])
        position = gap_start = end
    if gap_start < n:
        yield SvlogGap(offset=gap_start, size=n - gap_start,
                       checksum_failures=failures)


# --------------------------------------------------------------------------
# typed records (layouts per the public ICD; anchor S6)
# --------------------------------------------------------------------------


def atof_to_yz(angle: float, tof: float, sos: float) -> tuple[float, float]:
    """Project one ATOF detection into the sonar's swath plane.

    Format-defined math from the vendor's atof_point_data page:
    ``distance = 0.5 * sos * tof`` (the time of flight is two-way), then
    ``y = distance * sin(angle)`` (athwartships, positive to port) and
    ``z = -distance * cos(angle)`` (positive up, so depths are negative).
    Pure geometry in the sonar frame: no attitude, no georeferencing.
    """
    distance = 0.5 * sos * tof
    return distance * math.sin(angle), -distance * math.cos(angle)


@dataclass(frozen=True)
class AtofPointData(Record):
    """ATOF_POINT_DATA (id 3012): one ping's detections as raw observables.

    These are angle plus two-way time of flight with the applied speed of
    sound echoed back, so soundings can be re-reduced later under a
    corrected sound speed. Payload: a 40-byte fixed part (u32
    pwr_up_msec, u64 utc_msec, f32 listening_sec, f32 sos_mps, u32
    ping_number, u32 ping_hz, f32 pulse_sec, u32 flags, u16 num_points,
    u16 reserved) then 16 bytes per point (f32 angle, f32 tof, two
    reserved u32). The reserved u16 between num_points and the point
    array is per the vendor ICD; earlier project notes omitted it, and
    the ICD wins. Reserved words ride along verbatim.
    """

    pwr_up_msec: int
    utc_msec: int
    listening_sec: float
    sos_mps: float
    ping_number: int
    ping_hz: int
    pulse_sec: float
    flags: int
    angles: tuple[float, ...]
    tofs: tuple[float, ...]
    reserved: int
    point_reserved: tuple[int, ...]

    @property
    def num_points(self) -> int:
        return len(self.angles)

    def yz_points(self) -> tuple[tuple[float, float], ...]:
        """Every detection through :func:`atof_to_yz` at the echoed sos."""
        return tuple(
            atof_to_yz(angle, tof, self.sos_mps)
            for angle, tof in zip(self.angles, self.tofs, strict=True)
        )


@dataclass(frozen=True)
class YzPointData(Record):
    """YZ_POINT_DATA (id 3011): one ping's detections pre-projected.

    The device's own projection of the same detections ATOF carries; the
    applied sound speed is baked in, so prefer ATOF for reprocessing.
    Payload: a 100-byte fixed part (u32 timestamp_msec, u32 ping_number,
    f32 sos_mps, f32[3] up_vec, f32[3] mag_vec, u32[10] reserved, f32
    water_degC, f32 water_bar, f32 heave_m, f32 start_m, f32 end_m, u16
    unused, u16 num_points) then one f32 y/z pair per point. Y is
    athwartships meters, positive to port; Z is positive up, so all
    values are negative.
    """

    timestamp_msec: int
    ping_number: int
    sos_mps: float
    up_vec: tuple[float, float, float]
    mag_vec: tuple[float, float, float]
    reserved: tuple[int, ...]
    water_degc: float
    water_bar: float
    heave_m: float
    start_m: float
    end_m: float
    unused: int
    ys: tuple[float, ...]
    zs: tuple[float, ...]

    @property
    def num_points(self) -> int:
        return len(self.ys)


@dataclass(frozen=True)
class AttitudeReport(Record):
    """ATTITUDE_REPORT (id 504): device attitude as a world up-vector.

    Payload (36 bytes): f32[3] up_vec in the device frame (x forward, y
    port, z up), f32[3] mag_vec (reserved for a future magnetic vector,
    so there is no heading here: heading must come from the vehicle),
    u64 utc_msec (1970 epoch, 0 if unavailable), u32 pwr_up_msec.
    Vendor-defined derivations: pitch = asin(x), positive bow up;
    roll = atan2(y, z), positive port side up. The asin argument is
    clamped to [-1, 1] so float noise on a unit vector cannot raise.
    """

    up_vec: tuple[float, float, float]
    mag_vec: tuple[float, float, float]
    utc_msec: int
    pwr_up_msec: int

    @property
    def pitch_radians(self) -> float:
        return math.asin(max(-1.0, min(1.0, self.up_vec[0])))

    @property
    def roll_radians(self) -> float:
        return math.atan2(self.up_vec[1], self.up_vec[2])

    @property
    def pitch_degrees(self) -> float:
        return math.degrees(self.pitch_radians)

    @property
    def roll_degrees(self) -> float:
        return math.degrees(self.roll_radians)


@dataclass(frozen=True)
class WaterStats(Record):
    """WATER_STATS (id 118): f32 temperature (deg C), f32 pressure (bar);
    8 bytes. The vendor sentinel -1000 marks a sensor not installed."""

    temperature_degc: float
    pressure_bar: float


@dataclass(frozen=True)
class NmeaWrapper(Record):
    """NMEA_WRAPPER (id 109): the payload is one NMEA 0183 sentence (or
    NMEA 2000 data) as variable-length text, carried so navigation can be
    interleaved with sonar packets in the log. Decoded latin-1 (never
    raises) with trailing CR/LF/NUL stripped."""

    text: str


@dataclass(frozen=True)
class MavlinkWrapper(Record):
    """MAVLINK_WRAPPER (id 150): the payload is one MAVLink message as
    variable-length JSON text (mavlink2rest style). Decoded latin-1 with
    trailing CR/LF/NUL stripped; parsing the JSON is the caller's job."""

    text: str


@dataclass(frozen=True)
class DeviceInformation(Record):
    """DEVICE_INFORMATION (id 4): u8 device_type, u8 device_revision, u8
    firmware major/minor/patch, u8 reserved; 6 bytes."""

    device_type: int
    device_revision: int
    firmware_major: int
    firmware_minor: int
    firmware_patch: int
    reserved: int

    @property
    def firmware_version(self) -> str:
        return f"{self.firmware_major}.{self.firmware_minor}.{self.firmware_patch}"


@dataclass(frozen=True)
class PingParameters(Record):
    """SET_PING_PARAMETERS (id 3023): the operating setup in force,
    logged alongside the data it produced.

    Payload (36 bytes): i32 start_mm, i32 end_mm (negative means auto
    range), f32 sos_mps, i16 gain_index (-1 auto), i16 msec_per_ping,
    u16 pulse_width_usec (deprecated), u8 diagnostic_injected_signal,
    then five bool bytes (ping_enable, enable_channel_data,
    reserved_for_raw_data, enable_yz_point_data, enable_atof_data),
    i32 target_ping_hz, u16 n_range_steps, u16 reserved, f32
    pulse_len_steps.
    """

    start_mm: int
    end_mm: int
    sos_mps: float
    gain_index: int
    msec_per_ping: int
    pulse_width_usec: int
    diagnostic_injected_signal: int
    ping_enable: bool
    enable_channel_data: bool
    reserved_for_raw_data: bool
    enable_yz_point_data: bool
    enable_atof_data: bool
    target_ping_hz: int
    n_range_steps: int
    reserved: int
    pulse_len_steps: float


# --------------------------------------------------------------------------
# per-id decoders (payload -> Record)
# --------------------------------------------------------------------------

_ATOF_FIXED = struct.Struct("<IQ2f2IfI2H")
_ATOF_POINT = struct.Struct("<2f2I")
_YZ_FIXED = struct.Struct("<2If3f3f10I5f2H")
_YZ_PAIR = struct.Struct("<2f")
_ATTITUDE = struct.Struct("<3f3fQI")
_WATER = struct.Struct("<2f")
_DEVICE_INFO = struct.Struct("<6B")
_PARAMETERS = struct.Struct("<2if2hH6Bi2Hf")


def _decode_atof(payload: bytes) -> Record:
    (pwr_up_msec, utc_msec, listening_sec, sos_mps, ping_number, ping_hz,
     pulse_sec, flags, num_points, reserved) = _ATOF_FIXED.unpack_from(payload, 0)
    needed = _ATOF_FIXED.size + num_points * _ATOF_POINT.size
    if len(payload) < needed:
        raise ValueError(f"{num_points} points need {needed} bytes, got {len(payload)}")
    points = [
        _ATOF_POINT.unpack_from(payload, _ATOF_FIXED.size + i * _ATOF_POINT.size)
        for i in range(num_points)
    ]
    return AtofPointData(
        tag="ATOF", pwr_up_msec=pwr_up_msec, utc_msec=utc_msec,
        listening_sec=listening_sec, sos_mps=sos_mps, ping_number=ping_number,
        ping_hz=ping_hz, pulse_sec=pulse_sec, flags=flags,
        angles=tuple(p[0] for p in points), tofs=tuple(p[1] for p in points),
        reserved=reserved,
        point_reserved=tuple(word for p in points for word in p[2:]),
    )


def _decode_yz(payload: bytes) -> Record:
    values = _YZ_FIXED.unpack_from(payload, 0)
    num_points = values[25]
    needed = _YZ_FIXED.size + num_points * _YZ_PAIR.size
    if len(payload) < needed:
        raise ValueError(f"{num_points} points need {needed} bytes, got {len(payload)}")
    pairs = [
        _YZ_PAIR.unpack_from(payload, _YZ_FIXED.size + i * _YZ_PAIR.size)
        for i in range(num_points)
    ]
    return YzPointData(
        tag="YZ", timestamp_msec=values[0], ping_number=values[1],
        sos_mps=values[2], up_vec=values[3:6], mag_vec=values[6:9],
        reserved=values[9:19], water_degc=values[19], water_bar=values[20],
        heave_m=values[21], start_m=values[22], end_m=values[23],
        unused=values[24],
        ys=tuple(p[0] for p in pairs), zs=tuple(p[1] for p in pairs),
    )


def _decode_attitude(payload: bytes) -> Record:
    values = _ATTITUDE.unpack_from(payload, 0)
    return AttitudeReport(
        tag="ATT", up_vec=values[0:3], mag_vec=values[3:6],
        utc_msec=values[6], pwr_up_msec=values[7],
    )


def _decode_water(payload: bytes) -> Record:
    temperature, pressure = _WATER.unpack_from(payload, 0)
    return WaterStats(tag="WTR", temperature_degc=temperature, pressure_bar=pressure)


def _decode_nmea(payload: bytes) -> Record:
    return NmeaWrapper(tag="NMEA", text=payload.decode("latin-1").rstrip("\x00\r\n"))


def _decode_mavlink(payload: bytes) -> Record:
    return MavlinkWrapper(tag="MAV", text=payload.decode("latin-1").rstrip("\x00\r\n"))


def _decode_device_info(payload: bytes) -> Record:
    (device_type, revision, major, minor, patch,
     reserved) = _DEVICE_INFO.unpack_from(payload, 0)
    return DeviceInformation(
        tag="DVI", device_type=device_type, device_revision=revision,
        firmware_major=major, firmware_minor=minor, firmware_patch=patch,
        reserved=reserved,
    )


def _decode_parameters(payload: bytes) -> Record:
    values = _PARAMETERS.unpack_from(payload, 0)
    return PingParameters(
        tag="SPP", start_mm=values[0], end_mm=values[1], sos_mps=values[2],
        gain_index=values[3], msec_per_ping=values[4], pulse_width_usec=values[5],
        diagnostic_injected_signal=values[6],
        ping_enable=bool(values[7]), enable_channel_data=bool(values[8]),
        reserved_for_raw_data=bool(values[9]),
        enable_yz_point_data=bool(values[10]), enable_atof_data=bool(values[11]),
        target_ping_hz=values[12], n_range_steps=values[13], reserved=values[14],
        pulse_len_steps=values[15],
    )


_DECODERS: dict[int, Callable[[bytes], Record]] = {
    ATOF_POINT_DATA: _decode_atof,
    YZ_POINT_DATA: _decode_yz,
    ATTITUDE_REPORT: _decode_attitude,
    WATER_STATS: _decode_water,
    NMEA_WRAPPER: _decode_nmea,
    MAVLINK_WRAPPER: _decode_mavlink,
    DEVICE_INFORMATION: _decode_device_info,
    SET_PING_PARAMETERS: _decode_parameters,
}


def _decode(frame: SvlogFrame) -> Record | None:
    """Typed record for a known packet id, None for an unknown one."""
    decoder = _DECODERS.get(frame.packet_id)
    if decoder is None:
        return None
    try:
        return decoder(frame.payload)
    except (struct.error, ValueError) as error:
        return MalformedRecord(
            tag=_TAGS[frame.packet_id],
            fields=(
                f"packet_id={frame.packet_id}",
                f"offset={frame.offset}",
                f"payload_size={len(frame.payload)}",
            ),
            error=f"truncated or undecodable payload: {error}",
        )


def read_svlog(source: str | Path | bytes) -> Iterator[Record]:
    """Stream typed records from an SVLog/svlz file (path or bytes).

    Frames with unknown packet ids are skipped (use :func:`iter_frames`
    to see them, or :func:`load_survey` to count them); known ids whose
    payload does not satisfy the ICD layout yield
    :class:`~hydroformats.records.MalformedRecord`. Never raises on
    content.
    """
    for event in iter_frames(source):
        if not isinstance(event, SvlogFrame):
            continue
        record = _decode(event)
        if record is not None:
            yield record


# --------------------------------------------------------------------------
# survey loading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SvlogCounters:
    """Stream accounting from one :func:`load_survey` pass."""

    packets: int
    checksum_failures: int
    unknown_ids: int
    bytes_skipped: int


@dataclass(frozen=True)
class SvlogSurvey:
    """One materialized SVLog capture, split into its working series.

    ``nav`` pairs each wrapper record with its byte offset in the
    (decompressed) stream: the wrappers carry no log-side timestamp, so
    file position is what interleaves them with the ping series.
    ``device_info`` is the first DEVICE_INFORMATION seen, if any.
    """

    pings: tuple[AtofPointData, ...]
    attitude: tuple[AttitudeReport, ...]
    nav: tuple[tuple[int, NmeaWrapper | MavlinkWrapper], ...]
    water_stats: tuple[WaterStats, ...]
    device_info: DeviceInformation | None
    counters: SvlogCounters


def load_survey(source: str | Path | bytes) -> SvlogSurvey:
    """Materialize a whole SVLog file into series (small files, tests).

    ``counters.packets`` counts every checksum-verified frame, including
    unknown ids; ``bytes_skipped`` is every byte that was not part of a
    verified frame (garbage, corrupt frames, a truncated tail).
    """
    pings: list[AtofPointData] = []
    attitude: list[AttitudeReport] = []
    nav: list[tuple[int, NmeaWrapper | MavlinkWrapper]] = []
    water: list[WaterStats] = []
    device_info: DeviceInformation | None = None
    packets = failures = unknown = skipped = 0
    for event in iter_frames(source):
        if isinstance(event, SvlogGap):
            skipped += event.size
            failures += event.checksum_failures
            continue
        packets += 1
        record = _decode(event)
        if record is None:
            unknown += 1
        elif isinstance(record, AtofPointData):
            pings.append(record)
        elif isinstance(record, AttitudeReport):
            attitude.append(record)
        elif isinstance(record, (NmeaWrapper, MavlinkWrapper)):
            nav.append((event.offset, record))
        elif isinstance(record, WaterStats):
            water.append(record)
        elif isinstance(record, DeviceInformation) and device_info is None:
            device_info = record
    return SvlogSurvey(
        pings=tuple(pings), attitude=tuple(attitude), nav=tuple(nav),
        water_stats=tuple(water), device_info=device_info,
        counters=SvlogCounters(
            packets=packets, checksum_failures=failures,
            unknown_ids=unknown, bytes_skipped=skipped,
        ),
    )
