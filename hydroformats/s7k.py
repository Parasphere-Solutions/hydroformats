"""Teledyne RESON s7k reader (SeaBat 7k series multibeam logging).

An .s7k file is the native recording of the 7k sonar protocol: the
format the SeaBat 7-P processors log themselves and the format
third-party acquisition software (Teledyne PDS, QINSy, HYPACK) records
from them, which makes it a major interchange format in the survey
world. The file is a plain sequence of records, each wrapped in a
64-byte Data Record Frame (DRF) carrying a protocol version, an offset
to the embedded data, the sync pattern 0x0000FFFF, the record size, a
UTC time tag, the record type number, the producing device and its
enumerator, a checksum-validity flag and a trailing 32-bit byte-sum
checksum. All fields are little endian (DFD section 2.4).

Every layout here is hand-built from the format owner's own data
format definition, the only source consulted (anchor S12 in
docs/FORMAT-SOURCES.md):

- *7k Data Format*, Teledyne RESON Data Format Definition,
  Version 3.10, April 3, 2019.
  https://www3.mbari.org/data/mbsystem/formatdoc/Teledyne7k/7k_DFD_3.10_package/DFD_7k_Version_3.10.pdf

Readings the document leaves open are documented in the relevant
docstring and summarized here:

- The trailing checksum word is read as always present: the DRF's size
  field is defined "to the end of the checksum field" unconditionally
  (Table 5), so the word is framing even when the flags mark its value
  invalid. It is verified only when flags bit 0 is set: Table 5's own
  bit enumeration names bit 0, while the checksum field's prose says
  "bit 1"; bit 0 is the reading the enumeration supports and real
  logged data verifies (see the S12 validation notes).
- Frames whose header offset field is below 60 are treated as garbage:
  the version 5 DRF (the only protocol version in use per the DFD's
  version concordance) needs its full 64-byte fixed part before the
  Record Type Header can begin.
- Records are decoded through the record size, as the DFD instructs
  (its backwards-compatibility rule appends new fields at the end):
  longer payloads than the Version 3.10 layout ride along undecoded,
  and shorter vintages surface the older layout with the absent
  trailing fields as None (7027 detection fields gated by the declared
  block size, the 7006 filter gates, the 7004 transmit delays, the
  7610 temperature and pressure, the 7503 tail).
- The zero-length snippet window convention (begin sample greater than
  end sample means no data for that beam), which the DFD states for
  record 7058, is applied to record 7028's identical descriptors too.

The sync pattern makes resynchronization possible: garbage between
records, a corrupt size, or a truncated tail degrade to
:class:`S7kGap` ranges and the scan resumes at the next pattern, never
raising. Checksum mismatches are reported on the frame and counted,
never raised. Record types without an anchored decoding need here
(water column payloads aside, whose 7018/7042 headers are decoded and
whose sample matrices are skipped) are skipped tolerantly and counted
by type in :func:`load_swath`; known types whose payload does not
satisfy the DFD layout degrade to
:class:`~hydroformats.records.MalformedRecord`. Raw observables are
preserved throughout: detections keep their sample numbers and receive
angles next to the sampling rate and the applied sound velocity, and
snippets keep their stored intensity integers, so everything can be
re-reduced downstream.
"""
from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .records import MalformedRecord, Record
from .s7k_records import (
    S7kBathymetry,
    S7kBeamformedHeader,
    S7kBeamGeometry,
    S7kCompressedWaterColumnHeader,
    S7kCtd,
    S7kGeodesy,
    S7kHeading,
    S7kPosition,
    S7kRawDetections,
    S7kRemoteSonarSettings,
    S7kRollPitchHeave,
    S7kSnippetBackscatter,
    S7kSnippets,
    S7kSonarSettings,
    S7kSoundVelocity,
)

__all__ = [
    "POSITION",
    "CTD",
    "GEODESY",
    "ROLL_PITCH_HEAVE",
    "HEADING",
    "SONAR_SETTINGS",
    "BEAM_GEOMETRY",
    "BATHYMETRY",
    "BEAMFORMED_DATA",
    "RAW_DETECTION_DATA",
    "SNIPPET_DATA",
    "COMPRESSED_WATER_COLUMN",
    "SNIPPET_BACKSCATTER",
    "REMOTE_SONAR_SETTINGS",
    "SOUND_VELOCITY",
    "S7kBathymetry",
    "S7kBeamformedHeader",
    "S7kBeamGeometry",
    "S7kCompressedWaterColumnHeader",
    "S7kCounters",
    "S7kCtd",
    "S7kFrame",
    "S7kGap",
    "S7kGeodesy",
    "S7kHeading",
    "S7kPing",
    "S7kPosition",
    "S7kRawDetections",
    "S7kRemoteSonarSettings",
    "S7kRollPitchHeave",
    "S7kSnippetBackscatter",
    "S7kSnippets",
    "S7kSonarSettings",
    "S7kSoundVelocity",
    "S7kSwath",
    "iter_records",
    "load_swath",
    "read_s7k",
]

POSITION = 1003
CTD = 1010
GEODESY = 1011
ROLL_PITCH_HEAVE = 1012
HEADING = 1013
SONAR_SETTINGS = 7000
BEAM_GEOMETRY = 7004
BATHYMETRY = 7006
BEAMFORMED_DATA = 7018
RAW_DETECTION_DATA = 7027
SNIPPET_DATA = 7028
COMPRESSED_WATER_COLUMN = 7042
SNIPPET_BACKSCATTER = 7058
REMOTE_SONAR_SETTINGS = 7503
SOUND_VELOCITY = 7610

_TAGS = {
    POSITION: "POS",
    CTD: "CTD",
    GEODESY: "GEO",
    ROLL_PITCH_HEAVE: "MRU",
    HEADING: "HDG",
    SONAR_SETTINGS: "SET",
    BEAM_GEOMETRY: "BEAM",
    BATHYMETRY: "BATHY",
    BEAMFORMED_DATA: "WC",
    RAW_DETECTION_DATA: "DET",
    SNIPPET_DATA: "SNIP",
    COMPRESSED_WATER_COLUMN: "WCC",
    SNIPPET_BACKSCATTER: "SBS",
    REMOTE_SONAR_SETTINGS: "RSET",
    SOUND_VELOCITY: "SV",
}

_SYNC = b"\xff\xff\x00\x00"  # 0x0000FFFF, at byte 4 of every record
# The version 5 DRF, Table 5: u16 protocol, u16 offset, u32 sync,
# u32 size, u32 optional data offset, u32 optional data identifier,
# 7KTIME (u16 year, u16 day, f32 seconds, u8 hours, u8 minutes),
# u16 record version, u32 record type, u32 device identifier,
# u16 reserved, u16 system enumerator, u32 reserved, u16 flags,
# u16 reserved, u32 reserved, u32 fragmented total, u32 fragment.
_DRF = struct.Struct("<HHIIIIHHfBBHIIHHIHHIII")
_MIN_RTH_OFFSET = 60          # the fixed DRF fields end 64 bytes in
_MIN_RECORD = _DRF.size + 4   # empty data section plus checksum word


# --------------------------------------------------------------------------
# record walking
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class S7kFrame:
    """One framed record: the Data Record Frame's fields plus the raw
    data section (Table 5). ``payload`` is the Record Type Header and
    Record Data; ``optional_data`` is the trailing optional section
    (empty bytes when the record carries none) with its user-defined
    identifier. ``checksum_ok`` is True when the frame's flags mark the
    checksum invalid (bit 0 clear) or when the 32-bit byte sum of the
    record matches the stored word; a mismatch is reported here, never
    raised."""

    offset: int
    protocol_version: int
    record_type: int
    device_identifier: int
    system_enumerator: int
    year: int
    day: int
    seconds: float
    hours: int
    minutes: int
    flags: int
    payload: bytes
    optional_data: bytes
    optional_data_identifier: int
    checksum: int
    checksum_ok: bool


@dataclass(frozen=True)
class S7kGap:
    """Bytes outside any well-framed record: garbage between records, a
    header whose declared size overruns the file, or a truncated final
    record. The scan resumes at the next 0x0000FFFF sync pattern."""

    offset: int
    size: int


def _read_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def iter_records(source: str | Path | bytes) -> Iterator[S7kFrame | S7kGap]:
    """Walk the record stream; never raises on content.

    Yields :class:`S7kFrame` for every record whose Data Record Frame
    parses with a sane size and offset, and :class:`S7kGap` for every
    byte range that does not frame, in file order. A record starts four
    bytes before its sync pattern (the protocol version and offset
    fields precede it); a pattern whose frame cannot be validated is
    treated as part of the surrounding garbage and the scan resumes
    just past it, so a corrupt length cannot swallow the valid records
    behind it.
    """
    data = _read_bytes(source)
    n = len(data)
    position = 0
    gap_start = 0
    while True:
        sync = data.find(_SYNC, position)
        if sync == -1:
            break
        start = sync - 4
        if start < gap_start or start + _MIN_RECORD > n:
            position = sync + 4
            continue
        (protocol, rth_offset, _, size, od_offset, od_id, year, day, seconds,
         hours, minutes, _, record_type, device, _, enumerator, _, flags, _,
         _, _, _) = _DRF.unpack_from(data, start)
        if (rth_offset < _MIN_RTH_OFFSET or size < 4 + rth_offset + 4
                or start + size > n):
            position = sync + 4
            continue
        if start > gap_start:
            yield S7kGap(offset=gap_start, size=start - gap_start)
        data_end = start + size - 4
        (checksum,) = struct.unpack_from("<I", data, data_end)
        checksum_ok = True
        if flags & 0x0001:
            checksum_ok = checksum == sum(data[start:data_end]) & 0xFFFFFFFF
        body = start + 4 + rth_offset
        payload_end = data_end
        optional = b""
        if 4 + rth_offset <= od_offset <= size - 4:
            payload_end = start + od_offset
            optional = data[payload_end:data_end]
        yield S7kFrame(
            offset=start, protocol_version=protocol, record_type=record_type,
            device_identifier=device, system_enumerator=enumerator,
            year=year, day=day, seconds=seconds, hours=hours, minutes=minutes,
            flags=flags, payload=data[body:payload_end],
            optional_data=optional, optional_data_identifier=od_id,
            checksum=checksum, checksum_ok=checksum_ok,
        )
        position = gap_start = start + size
    if gap_start < n:
        yield S7kGap(offset=gap_start, size=n - gap_start)


# --------------------------------------------------------------------------
# per-type decoders (frame -> Record; layouts per the DFD tables, anchor S12)
# --------------------------------------------------------------------------

_POSITION_RTH = struct.Struct("<IfdddBBBB")           # Table 14, 36 bytes
# plus the trailing satellite count, the one field Table 14 marks
# optional (real PDS-logged files omit it)
_CTD_RTH = struct.Struct("<f6BH2dfI")                 # Table 24, 36 bytes
_GEODESY_RTH = struct.Struct(                         # Table 26, 320 bytes
    "<32s2d16x32sIB7d35x32s2B5di50x")
_MOTION_RTH = struct.Struct("<3f")                    # Table 27
_HEADING_RTH = struct.Struct("<f")                    # Table 28
_SETTINGS_RTH = struct.Struct(                        # Table 39, 156 bytes
    "<QIH4f2IfHH5f2I5fIf3IfI8fH")
_BEAM_GEOMETRY_RTH = struct.Struct("<QI")             # Table 44
_BATHYMETRY_RTH = struct.Struct("<QIHIBBf")           # Table 46, 24 bytes
_BEAMFORMED_RTH = struct.Struct("<QIHHI")             # Table 63 leading fields
_RAW_DETECTION_RTH = struct.Struct("<QIHIIBIfff")     # Table 71, 39 + 60 res.
_RAW_DETECTION_BASE = struct.Struct("<HffII")         # Table 72 leading block
_SNIPPET_RTH = struct.Struct("<QIHHBBI")              # Table 74, 22 + 24 res.
_SNIPPET_DESCRIPTOR = struct.Struct("<HIII")          # Tables 75 and 101
_WATER_COLUMN_RTH = struct.Struct("<QIHHIIIIffI")     # Table 82, 44 bytes
_BACKSCATTER_RTH = struct.Struct("<QIHHBIf")          # Table 100, 25 + 24 res.
_REMOTE_CORE = struct.Struct(                         # Table 113 through the
    "<QI4f2IfHH5f2I5fIf3IfI7f")                       # spreading loss field

# The 7503 fields past the 7000-equivalent core, in the DFD's append
# order (Table 113): field name (None for a reserved skip) and format.
_REMOTE_TAIL: tuple[tuple[str | None, str], ...] = (
    ("vernier_operation_mode", "<B"),
    ("automatic_filter_window", "<B"),
    ("tx_offset_x_m", "<f"),
    ("tx_offset_y_m", "<f"),
    ("tx_offset_z_m", "<f"),
    ("head_tilt_x_rad", "<f"),
    ("head_tilt_y_rad", "<f"),
    ("head_tilt_z_rad", "<f"),
    ("ping_state", "<I"),
    ("beam_spacing_mode", "<H"),
    ("sonar_source_mode", "<H"),
    ("adaptive_gate_min_depth_m", "<f"),
    ("adaptive_gate_max_depth_m", "<f"),
    ("trigger_out_width_sec", "<d"),
    ("trigger_out_offset_sec", "<d"),
    ("projector_81xx_selection", "<H"),
    (None, "<8s"),                       # reserved u32 * 2
    ("alternate_gain_db", "<f"),
    ("vernier_filter", "<B"),
    (None, "<B"),                        # reserved u8
    ("custom_beams", "<H"),
    ("coverage_angle_rad", "<f"),
    ("coverage_mode", "<B"),
    ("quality_filter_flags", "<B"),
    ("rx_steering_angle_rad", "<f"),
    ("flexmode_coverage_rad", "<f"),
    ("flexmode_steering_rad", "<f"),
    ("constant_spacing_m", "<f"),
    ("beam_mode_selection", "<H"),
    ("depth_gate_tilt_rad", "<f"),
    ("applied_frequency_hz", "<f"),
    ("element_number", "<I"),
)


def _common(frame: S7kFrame, tag: str) -> dict:
    """The DRF context every s7k record carries."""
    return {
        "tag": tag, "device_identifier": frame.device_identifier,
        "system_enumerator": frame.system_enumerator, "year": frame.year,
        "day": frame.day, "seconds": frame.seconds, "hours": frame.hours,
        "minutes": frame.minutes,
    }


def _text(raw: bytes) -> str:
    return raw.decode("latin-1").rstrip("\x00 ")


def _floats(payload: bytes, offset: int, count: int) -> tuple[float, ...]:
    return struct.unpack_from(f"<{count}f", payload, offset)


def _need(payload: bytes, size: int, what: str) -> None:
    if len(payload) < size:
        raise ValueError(f"{what} needs {size} bytes, got {len(payload)}")


def _decode_position(frame: S7kFrame) -> Record:
    (datum, latency, lat_or_northing, lon_or_easting, height, position_type,
     utm_zone, quality, method) = _POSITION_RTH.unpack_from(frame.payload, 0)
    satellites = None
    if len(frame.payload) > _POSITION_RTH.size:
        satellites = frame.payload[_POSITION_RTH.size]
    return S7kPosition(
        **_common(frame, "POS"), datum_identifier=datum, latency_sec=latency,
        latitude_or_northing=lat_or_northing,
        longitude_or_easting=lon_or_easting, height_m=height,
        position_type=position_type, utm_zone=utm_zone, quality_flag=quality,
        positioning_method=method, number_of_satellites=satellites,
    )


def _decode_ctd(frame: S7kFrame) -> Record:
    payload = frame.payload
    (frequency, source, algorithm, conductivity_flag, pressure_flag,
     position_flag, validity, _, latitude, longitude, rate,
     count) = _CTD_RTH.unpack_from(payload, 0)
    _need(payload, _CTD_RTH.size + 20 * count, f"1010 with {count} samples")
    rows = struct.unpack_from(f"<{5 * count}f", payload, _CTD_RTH.size)
    return S7kCtd(
        **_common(frame, "CTD"), frequency_hz=frequency,
        sound_velocity_source=source, sound_velocity_algorithm=algorithm,
        conductivity_flag=conductivity_flag, pressure_flag=pressure_flag,
        position_flag=position_flag, sample_validity=validity,
        latitude_rad=latitude, longitude_rad=longitude, sample_rate_hz=rate,
        conductivity_salinity=rows[0::5], temperature_c=rows[1::5],
        pressure_depth=rows[2::5], sound_velocity_mps=rows[3::5],
        absorption_db_per_km=rows[4::5],
    )


def _decode_geodesy(frame: S7kFrame) -> Record:
    (spheroid, semi_major, flattening, datum, method, parameters, dx, dy, dz,
     rx, ry, rz, scale, grid, distance_units, angular_units, origin, meridian,
     easting, northing, central_scale, custom) = _GEODESY_RTH.unpack_from(
        frame.payload, 0)
    return S7kGeodesy(
        **_common(frame, "GEO"), spheroid=_text(spheroid),
        semi_major_axis_m=semi_major, inverse_flattening=flattening,
        datum=_text(datum), calculation_method=method,
        number_of_parameters=parameters, dx_m=dx, dy_m=dy, dz_m=dz,
        rx_rad=rx, ry_rad=ry, rz_rad=rz, scale=scale, grid_name=_text(grid),
        grid_distance_units=distance_units, grid_angular_units=angular_units,
        latitude_of_origin=origin, central_meridian=meridian,
        false_easting_m=easting, false_northing_m=northing,
        central_scale_factor=central_scale, custom_identifier=custom,
    )


def _decode_roll_pitch_heave(frame: S7kFrame) -> Record:
    roll, pitch, heave = _MOTION_RTH.unpack_from(frame.payload, 0)
    return S7kRollPitchHeave(**_common(frame, "MRU"), roll_rad=roll,
                             pitch_rad=pitch, heave_m=heave)


def _decode_heading(frame: S7kFrame) -> Record:
    (heading,) = _HEADING_RTH.unpack_from(frame.payload, 0)
    return S7kHeading(**_common(frame, "HDG"), heading_rad=heading)


def _decode_sonar_settings(frame: S7kFrame) -> Record:
    (sonar_id, ping, multiping, frequency, rate, bandwidth, pulse_width,
     pulse_type, envelope, envelope_parameter, pulse_mode, _, max_ping_rate,
     ping_period, range_selection, power, gain, control_flags, projector,
     steer_v, steer_h, width_v, width_h, focal, projector_weighting,
     projector_weighting_parameter, transmit_flags, hydrophone,
     receive_weighting, receive_weighting_parameter, receive_flags,
     beam_width, min_range, max_range, min_depth, max_depth, absorption,
     sound_velocity, spreading, _) = _SETTINGS_RTH.unpack_from(
        frame.payload, 0)
    return S7kSonarSettings(
        **_common(frame, "SET"), sonar_id=sonar_id, ping_number=ping,
        multiping_sequence=multiping, frequency_hz=frequency,
        sample_rate_hz=rate, receiver_bandwidth_hz=bandwidth,
        tx_pulse_width_sec=pulse_width, tx_pulse_type=pulse_type,
        tx_pulse_envelope=envelope,
        tx_pulse_envelope_parameter=envelope_parameter,
        tx_pulse_mode=pulse_mode, max_ping_rate_hz=max_ping_rate,
        ping_period_sec=ping_period, range_selection_m=range_selection,
        power_selection_db=power, gain_selection_db=gain,
        control_flags=control_flags, projector_identifier=projector,
        projector_steering_vertical_rad=steer_v,
        projector_steering_horizontal_rad=steer_h,
        projector_beam_width_vertical_rad=width_v,
        projector_beam_width_horizontal_rad=width_h,
        projector_focal_point_m=focal,
        projector_weighting_window=projector_weighting,
        projector_weighting_parameter=projector_weighting_parameter,
        transmit_flags=transmit_flags, hydrophone_identifier=hydrophone,
        receive_weighting_window=receive_weighting,
        receive_weighting_parameter=receive_weighting_parameter,
        receive_flags=receive_flags, receive_beam_width_rad=beam_width,
        min_range_m=min_range, max_range_m=max_range, min_depth_m=min_depth,
        max_depth_m=max_depth, absorption_db_per_km=absorption,
        sound_velocity_mps=sound_velocity, spreading_loss_db=spreading,
    )


def _decode_beam_geometry(frame: S7kFrame) -> Record:
    payload = frame.payload
    sonar_id, count = _BEAM_GEOMETRY_RTH.unpack_from(payload, 0)
    base = _BEAM_GEOMETRY_RTH.size
    _need(payload, base + 16 * count, f"7004 with {count} beams")
    with_delays = len(payload) >= base + 20 * count
    return S7kBeamGeometry(
        **_common(frame, "BEAM"), sonar_id=sonar_id,
        vertical_angles_rad=_floats(payload, base, count),
        horizontal_angles_rad=_floats(payload, base + 4 * count, count),
        beam_width_y_rad=_floats(payload, base + 8 * count, count),
        beam_width_x_rad=_floats(payload, base + 12 * count, count),
        tx_delays=_floats(payload, base + 16 * count, count)
        if with_delays else None,
    )


def _decode_bathymetry(frame: S7kFrame) -> Record:
    payload = frame.payload
    (sonar_id, ping, multiping, count, flags, manual,
     sound_velocity) = _BATHYMETRY_RTH.unpack_from(payload, 0)
    base = _BATHYMETRY_RTH.size
    _need(payload, base + 9 * count, f"7006 with {count} beams")
    with_gates = len(payload) >= base + 17 * count
    return S7kBathymetry(
        **_common(frame, "BATHY"), sonar_id=sonar_id, ping_number=ping,
        multiping_sequence=multiping, flags=flags,
        sound_velocity_manual=manual, sound_velocity_mps=sound_velocity,
        travel_times_sec=_floats(payload, base, count),
        qualities=tuple(payload[base + 4 * count:base + 5 * count]),
        intensities=_floats(payload, base + 5 * count, count),
        min_filter_sec=_floats(payload, base + 9 * count, count)
        if with_gates else None,
        max_filter_sec=_floats(payload, base + 13 * count, count)
        if with_gates else None,
    )


def _decode_raw_detections(frame: S7kFrame) -> Record:
    payload = frame.payload
    (sonar_id, ping, multiping, count, block, algorithm, flags, rate,
     tx_angle, roll) = _RAW_DETECTION_RTH.unpack_from(payload, 0)
    base = _RAW_DETECTION_RTH.size + 60  # u32 * 15 reserved (Table 71)
    if block < _RAW_DETECTION_BASE.size:
        raise ValueError(f"7027 detection block of {block} bytes cannot hold "
                         f"the {_RAW_DETECTION_BASE.size} byte base fields")
    _need(payload, base + count * block, f"7027 with {count} x {block} bytes")
    # The declared block size says which of the appended per-detection
    # fields this vintage of the record carries (up to the four Version
    # 3.10 defines); anything past those is a newer field, skipped.
    extras = min((block - _RAW_DETECTION_BASE.size) // 4, 4)
    row_struct = struct.Struct("<HffII" + "f" * extras)
    rows = [row_struct.unpack_from(payload, base + i * block)
            for i in range(count)]

    def extra(index: int) -> tuple[float, ...] | None:
        if index >= extras:
            return None
        return tuple(row[5 + index] for row in rows)

    return S7kRawDetections(
        **_common(frame, "DET"), sonar_id=sonar_id, ping_number=ping,
        multiping_sequence=multiping, detection_algorithm=algorithm,
        flags=flags, sampling_rate_hz=rate, tx_angle_rad=tx_angle,
        applied_roll_rad=roll, detection_size=block,
        beam_numbers=tuple(row[0] for row in rows),
        detection_points=tuple(row[1] for row in rows),
        rx_angles_rad=tuple(row[2] for row in rows),
        detection_flags=tuple(row[3] for row in rows),
        qualities=tuple(row[4] for row in rows),
        uncertainties=extra(0), intensities=extra(1),
        min_limits=extra(2), max_limits=extra(3),
    )


def _snippet_windows(payload: bytes, base: int, count: int,
                     what: str) -> list[tuple[int, int, int, int]]:
    """The per-detection descriptor blocks of records 7028 and 7058:
    beam number, first sample, detection sample, last sample."""
    _need(payload, base + count * _SNIPPET_DESCRIPTOR.size,
          f"{what} with {count} descriptors")
    return [
        _SNIPPET_DESCRIPTOR.unpack_from(
            payload, base + i * _SNIPPET_DESCRIPTOR.size)
        for i in range(count)
    ]


def _decode_snippets(frame: S7kFrame) -> Record:
    payload = frame.payload
    (sonar_id, ping, multiping, count, error_flag, control_flags,
     flags) = _SNIPPET_RTH.unpack_from(payload, 0)
    base = _SNIPPET_RTH.size + 24  # u32 * 6 reserved (Table 74)
    common = dict(
        **_common(frame, "SNIP"), sonar_id=sonar_id, ping_number=ping,
        multiping_sequence=multiping, error_flag=error_flag,
        control_flags=control_flags, flags=flags,
    )
    if error_flag:
        # A set error flag means the record carries no data (Table 74).
        return S7kSnippets(**common, beam_numbers=(), snippet_starts=(),
                           detection_samples=(), snippet_ends=(), snippets=())
    rows = _snippet_windows(payload, base, count, "7028")
    code = "I" if flags & 1 else "H"
    width = 4 if flags & 1 else 2
    position = base + count * _SNIPPET_DESCRIPTOR.size
    snippets = []
    for _, start, _, end in rows:
        length = max(0, end - start + 1)
        _need(payload, position + length * width, "7028 snippet samples")
        snippets.append(struct.unpack_from(f"<{length}{code}", payload,
                                           position))
        position += length * width
    return S7kSnippets(
        **common,
        beam_numbers=tuple(row[0] for row in rows),
        snippet_starts=tuple(row[1] for row in rows),
        detection_samples=tuple(row[2] for row in rows),
        snippet_ends=tuple(row[3] for row in rows),
        snippets=tuple(snippets),
    )


def _decode_snippet_backscatter(frame: S7kFrame) -> Record:
    payload = frame.payload
    (sonar_id, ping, multiping, count, error_flag, control_flags,
     absorption) = _BACKSCATTER_RTH.unpack_from(payload, 0)
    base = _BACKSCATTER_RTH.size + 24  # u32 * 6 reserved (Table 100)
    rows = _snippet_windows(payload, base, count, "7058")
    lengths = [max(0, end - begin + 1) for _, begin, _, end in rows]
    position = base + count * _SNIPPET_DESCRIPTOR.size
    backscatter = []
    for length in lengths:
        _need(payload, position + length * 4, "7058 backscatter samples")
        backscatter.append(_floats(payload, position, length))
        position += length * 4
    footprints = None
    if control_flags & 0x40:  # footprint areas included (Table 100 bit 6)
        series = []
        for length in lengths:
            _need(payload, position + length * 4, "7058 footprint areas")
            series.append(_floats(payload, position, length))
            position += length * 4
        footprints = tuple(series)
    return S7kSnippetBackscatter(
        **_common(frame, "SBS"), sonar_id=sonar_id, ping_number=ping,
        multiping_sequence=multiping, error_flag=error_flag,
        control_flags=control_flags, absorption_db_per_km=absorption,
        beam_numbers=tuple(row[0] for row in rows),
        begin_samples=tuple(row[1] for row in rows),
        detection_samples=tuple(row[2] for row in rows),
        end_samples=tuple(row[3] for row in rows),
        backscatter_db=tuple(backscatter), footprints_m2=footprints,
    )


def _decode_sound_velocity(frame: S7kFrame) -> Record:
    payload = frame.payload
    (sound_velocity,) = struct.unpack_from("<f", payload, 0)
    temperature = pressure = None
    if len(payload) >= 8:
        (temperature,) = struct.unpack_from("<f", payload, 4)
    if len(payload) >= 12:
        (pressure,) = struct.unpack_from("<f", payload, 8)
    return S7kSoundVelocity(**_common(frame, "SV"),
                            sound_velocity_mps=sound_velocity,
                            temperature_k=temperature, pressure_pa=pressure)


def _decode_beamformed(frame: S7kFrame) -> Record:
    sonar_id, ping, multiping, beams, samples = _BEAMFORMED_RTH.unpack_from(
        frame.payload, 0)
    return S7kBeamformedHeader(
        **_common(frame, "WC"), sonar_id=sonar_id, ping_number=ping,
        multiping_sequence=multiping, beams=beams, samples=samples,
    )


def _decode_compressed_water_column(frame: S7kFrame) -> Record:
    (sonar_id, ping, multiping, beams, samples, compressed, flags, first,
     rate, factor, _) = _WATER_COLUMN_RTH.unpack_from(frame.payload, 0)
    return S7kCompressedWaterColumnHeader(
        **_common(frame, "WCC"), sonar_id=sonar_id, ping_number=ping,
        multiping_sequence=multiping, beams=beams, samples=samples,
        compressed_samples=compressed, flags=flags, first_sample=first,
        sample_rate_hz=rate, compression_factor=factor,
    )


def _decode_remote_settings(frame: S7kFrame) -> Record:
    payload = frame.payload
    (sonar_id, ping, frequency, rate, bandwidth, pulse_width, pulse_type,
     envelope, envelope_parameter, pulse_mode, _, max_ping_rate, ping_period,
     range_selection, power, gain, control_flags, projector, steer_v, steer_h,
     width_v, width_h, focal, projector_weighting,
     projector_weighting_parameter, transmit_flags, hydrophone,
     receive_weighting, receive_weighting_parameter, receive_flags, min_range,
     max_range, min_depth, max_depth, absorption, sound_velocity,
     spreading) = _REMOTE_CORE.unpack_from(payload, 0)
    tail: dict[str, int | float] = {}
    position = _REMOTE_CORE.size
    for name, fmt in _REMOTE_TAIL:
        size = struct.calcsize(fmt)
        if len(payload) < position + size:
            break
        if name is not None:
            (tail[name],) = struct.unpack_from(fmt, payload, position)
        position += size
    return S7kRemoteSonarSettings(
        **_common(frame, "RSET"), sonar_id=sonar_id, ping_number=ping,
        frequency_hz=frequency, sample_rate_hz=rate,
        receiver_bandwidth_hz=bandwidth, tx_pulse_width_sec=pulse_width,
        tx_pulse_type=pulse_type, tx_pulse_envelope=envelope,
        tx_pulse_envelope_parameter=envelope_parameter,
        tx_pulse_mode=pulse_mode, max_ping_rate_hz=max_ping_rate,
        ping_period_sec=ping_period, range_selection_m=range_selection,
        power_selection_db=power, gain_selection_db=gain,
        control_flags=control_flags, projector_identifier=projector,
        projector_steering_vertical_rad=steer_v,
        projector_steering_horizontal_rad=steer_h,
        projector_beam_width_vertical_rad=width_v,
        projector_beam_width_horizontal_rad=width_h,
        projector_focal_point_m=focal,
        projector_weighting_window=projector_weighting,
        projector_weighting_parameter=projector_weighting_parameter,
        transmit_flags=transmit_flags, hydrophone_identifier=hydrophone,
        receive_weighting_window=receive_weighting,
        receive_weighting_parameter=receive_weighting_parameter,
        receive_flags=receive_flags, min_range_m=min_range,
        max_range_m=max_range, min_depth_m=min_depth, max_depth_m=max_depth,
        absorption_db_per_km=absorption, sound_velocity_mps=sound_velocity,
        spreading_loss_db=spreading, **tail,
    )


_DECODERS = {
    POSITION: _decode_position,
    CTD: _decode_ctd,
    GEODESY: _decode_geodesy,
    ROLL_PITCH_HEAVE: _decode_roll_pitch_heave,
    HEADING: _decode_heading,
    SONAR_SETTINGS: _decode_sonar_settings,
    BEAM_GEOMETRY: _decode_beam_geometry,
    BATHYMETRY: _decode_bathymetry,
    BEAMFORMED_DATA: _decode_beamformed,
    RAW_DETECTION_DATA: _decode_raw_detections,
    SNIPPET_DATA: _decode_snippets,
    COMPRESSED_WATER_COLUMN: _decode_compressed_water_column,
    SNIPPET_BACKSCATTER: _decode_snippet_backscatter,
    REMOTE_SONAR_SETTINGS: _decode_remote_settings,
    SOUND_VELOCITY: _decode_sound_velocity,
}


def _decode(frame: S7kFrame) -> Record | None:
    """Typed record for a known record type, None for an unknown one."""
    decoder = _DECODERS.get(frame.record_type)
    if decoder is None:
        return None
    try:
        return decoder(frame)
    except (struct.error, ValueError) as error:
        return MalformedRecord(
            tag=_TAGS[frame.record_type],
            fields=(
                f"record_type={frame.record_type}",
                f"offset={frame.offset}",
                f"payload_size={len(frame.payload)}",
            ),
            error=f"truncated or undecodable payload: {error}",
        )


def read_s7k(source: str | Path | bytes) -> Iterator[Record]:
    """Stream typed records from an s7k file (path or bytes), in file
    order.

    Records with unknown types are skipped (use :func:`iter_records` to
    see them, or :func:`load_swath` to count them); known types whose
    payload does not satisfy the DFD layout yield
    :class:`~hydroformats.records.MalformedRecord`. Never raises on
    content.
    """
    for event in iter_records(source):
        if not isinstance(event, S7kFrame):
            continue
        record = _decode(event)
        if record is not None:
            yield record


# --------------------------------------------------------------------------
# swath loading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class S7kPing:
    """One ping's records, matched on the producing device, its
    enumerator (twin heads log independent ping sequences), the ping
    number and the multi-ping sequence. Each slot holds the first
    record of its kind seen for the ping, or None: ``raw_detections``
    (7027) carries the raw observables, ``bathymetry`` the deprecated
    7006 results, ``snippets`` (7028) the raw intensity windows and
    ``backscatter`` (7058) their calibrated form, and ``settings``
    (7000) the sonar state that shaped the ping."""

    device_identifier: int
    system_enumerator: int
    ping_number: int
    multiping_sequence: int
    settings: S7kSonarSettings | None = None
    raw_detections: S7kRawDetections | None = None
    bathymetry: S7kBathymetry | None = None
    snippets: S7kSnippets | None = None
    backscatter: S7kSnippetBackscatter | None = None


@dataclass(frozen=True)
class S7kCounters:
    """Stream accounting from one :func:`load_swath` pass.

    ``records`` counts every intact frame, decoded or not.
    ``unknown_record_types`` are (type, count) pairs in ascending type
    order. ``checksum_failures`` counts frames whose flags promised a
    valid checksum but whose byte sum disagreed (reported, never
    dropped). ``bytes_skipped`` counts only bytes outside any intact
    frame (garbage, corrupt headers, a truncated tail).
    """

    records: int
    unknown_record_types: tuple[tuple[int, int], ...]
    checksum_failures: int
    bytes_skipped: int


@dataclass(frozen=True)
class S7kSwath:
    """One materialized s7k file, split into its working series.

    ``pings`` matches each ping's 7027/7006/7028/7058/7000 records
    together, ordered by first appearance in the file; everything else
    is a file-order series. ``water_column`` holds the decoded 7018 and
    7042 headers (their sample payloads are skipped, not stored).
    Malformed records are dropped here but still counted in
    ``counters.records``; use :func:`read_s7k` to see them.
    """

    pings: tuple[S7kPing, ...]
    positions: tuple[S7kPosition, ...]
    roll_pitch_heaves: tuple[S7kRollPitchHeave, ...]
    headings: tuple[S7kHeading, ...]
    ctds: tuple[S7kCtd, ...]
    geodesies: tuple[S7kGeodesy, ...]
    sound_velocities: tuple[S7kSoundVelocity, ...]
    beam_geometries: tuple[S7kBeamGeometry, ...]
    remote_settings: tuple[S7kRemoteSonarSettings, ...]
    water_column: tuple[S7kBeamformedHeader | S7kCompressedWaterColumnHeader,
                        ...]
    counters: S7kCounters


_PING_SLOTS = {
    S7kSonarSettings: "settings",
    S7kRawDetections: "raw_detections",
    S7kBathymetry: "bathymetry",
    S7kSnippets: "snippets",
    S7kSnippetBackscatter: "backscatter",
}

_SERIES_SLOTS = {
    S7kPosition: "positions",
    S7kRollPitchHeave: "roll_pitch_heaves",
    S7kHeading: "headings",
    S7kCtd: "ctds",
    S7kGeodesy: "geodesies",
    S7kSoundVelocity: "sound_velocities",
    S7kBeamGeometry: "beam_geometries",
    S7kRemoteSonarSettings: "remote_settings",
    S7kBeamformedHeader: "water_column",
    S7kCompressedWaterColumnHeader: "water_column",
}


def load_swath(source: str | Path | bytes) -> S7kSwath:
    """Materialize a whole s7k file into series (exported at the
    package level as :func:`hydroformats.load_s7k`).

    Preserves the raw observables everywhere: detections keep their
    sample numbers and receive angles next to the sampling rate,
    settings and surface sound velocity series, and snippets keep
    their stored intensity integers, so the swath can be re-reduced
    under a corrected sound velocity profile downstream.
    """
    ping_order: list[tuple[int, int, int, int]] = []
    ping_parts: dict[tuple[int, int, int, int], dict] = {}
    series: dict[str, list] = {name: [] for name in
                               ("positions", "roll_pitch_heaves", "headings",
                                "ctds", "geodesies", "sound_velocities",
                                "beam_geometries", "remote_settings",
                                "water_column")}
    unknown: dict[int, int] = {}
    records = checksum_failures = skipped = 0
    for event in iter_records(source):
        if isinstance(event, S7kGap):
            skipped += event.size
            continue
        records += 1
        if not event.checksum_ok:
            checksum_failures += 1
        record = _decode(event)
        if record is None:
            key = event.record_type
            unknown[key] = unknown.get(key, 0) + 1
            continue
        slot = _PING_SLOTS.get(type(record))
        if slot is not None:
            key = (record.device_identifier, record.system_enumerator,
                   record.ping_number,
                   getattr(record, "multiping_sequence", 0))
            if key not in ping_parts:
                ping_parts[key] = {}
                ping_order.append(key)
            ping_parts[key].setdefault(slot, record)
            continue
        name = _SERIES_SLOTS.get(type(record))
        if name is not None:
            series[name].append(record)
    return S7kSwath(
        pings=tuple(
            S7kPing(device_identifier=key[0], system_enumerator=key[1],
                    ping_number=key[2], multiping_sequence=key[3],
                    **ping_parts[key])
            for key in ping_order
        ),
        counters=S7kCounters(
            records=records,
            unknown_record_types=tuple(sorted(unknown.items())),
            checksum_failures=checksum_failures,
            bytes_skipped=skipped,
        ),
        **{name: tuple(values) for name, values in series.items()},
    )
