"""EdgeTech JSF reader (side scan and bathymetric side scan logging).

A JSF file is EdgeTech's native recording format, written by the
Discover and JStar topsides: a stream of messages, each led by a
16-byte header carrying a start marker (0x1601), a protocol version, a
message type, the subsystem and channel that produced the data, and the
byte size of the payload to follow. All multi-byte fields are little
endian (ICD section 1.2). Sonar data arrives one message per ping per
channel: a dual-frequency side scan such as the EdgeTech 6205 writes
four type 80 messages per ping (port and starboard at each of two
frequencies, told apart by subsystem and channel), and its bathymetric
side writes type 3000 messages the same way. Navigation, attitude and
sensor readings ride along as their own message types.

Every layout here is hand-built from the format owner's own interface
control document, the only source consulted (anchor S9 in
docs/FORMAT-SOURCES.md):

- *JSF File and Message Descriptions*, EdgeTech document 0023492
  Rev. R, 2025-12-22.
  https://www.edgetech.com/wp-content/uploads/2023/04/0023492_Rev_R.pdf

Readings the document leaves open are documented in the relevant
docstring and summarized here:

- The type 3000 angle scale factor is read as a 4-byte float: the ICD
  table prints its size as UINT32, but its unit is degrees and
  Equation 2-5 multiplies it directly onto a signed 16-bit count, which
  no whole number of degrees could scale; every neighboring scale and
  accuracy field in the same table is a float.
- The type 3002 validity bits are read in field order (bit 0 pressure
  through bit 5 depth): the ICD defers to the 3001 description without
  listing bits, and field order is the rule both fully enumerated
  validity tables follow.
- The type 80 sample interval fraction byte is carried verbatim,
  never interpreted: the ICD names it without stating its encoding.

The header's start marker makes resynchronization possible: garbage
between messages, a corrupt size, or a truncated tail degrade to
:class:`JsfGap` ranges and the scan resumes at the next marker, never
raising. Message types without an anchored decoding need here are
skipped tolerantly and counted by type in :func:`load_survey`; known
types whose payload does not satisfy the ICD layout degrade to
:class:`~hydroformats.records.MalformedRecord`. Raw observables are
preserved throughout: side scan traces keep their stored integers next
to the block floating point scale, and bathymetric samples keep their
time delay and angle counts next to the scale factors, so everything
can be re-reduced downstream.
"""
from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .jsf_records import (
    JsfAltitude,
    JsfAttitude,
    JsfBathyPing,
    JsfBathyPressure,
    JsfNmea,
    JsfPitchRoll,
    JsfPosition,
    JsfPressureReading,
    JsfSonarTrace,
    JsfSystemInfo,
    frequency_band,
    side_name,
    sounding_usable,
)
from .records import MalformedRecord, Record

__all__ = [
    "SONAR_DATA",
    "SYSTEM_INFORMATION",
    "NMEA_STRING",
    "PITCH_ROLL",
    "PRESSURE_SENSOR",
    "BATHYMETRIC_DATA",
    "ATTITUDE",
    "BATHY_PRESSURE",
    "ALTITUDE",
    "POSITION",
    "JsfAltitude",
    "JsfAttitude",
    "JsfBathyPing",
    "JsfBathyPressure",
    "JsfChannelSeries",
    "JsfCounters",
    "JsfFrame",
    "JsfGap",
    "JsfNmea",
    "JsfPitchRoll",
    "JsfPosition",
    "JsfPressureReading",
    "JsfSonarTrace",
    "JsfSurvey",
    "JsfSystemInfo",
    "frequency_band",
    "iter_messages",
    "load_survey",
    "read_jsf",
    "side_name",
    "sounding_usable",
]

SONAR_DATA = 80
SYSTEM_INFORMATION = 182
NMEA_STRING = 2002
PITCH_ROLL = 2020
PRESSURE_SENSOR = 2060
BATHYMETRIC_DATA = 3000
ATTITUDE = 3001
BATHY_PRESSURE = 3002
ALTITUDE = 3003
POSITION = 3004

_TAGS = {
    SONAR_DATA: "SON",
    SYSTEM_INFORMATION: "SYS",
    NMEA_STRING: "NMEA",
    PITCH_ROLL: "MRU",
    PRESSURE_SENSOR: "PSNS",
    BATHYMETRIC_DATA: "BATH",
    ATTITUDE: "ATT",
    BATHY_PRESSURE: "BPRS",
    ALTITUDE: "ALT",
    POSITION: "POS",
}

_SYNC = b"\x01\x16"  # start marker 0x1601, little endian
_HEADER = struct.Struct("<HBBHBBBBHi")


# --------------------------------------------------------------------------
# message walking
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class JsfFrame:
    """One framed message: the 16-byte header's fields plus the raw
    payload (ICD Table 2-1). ``subsystem`` and ``channel`` identify the
    data source; on a multi-frequency side scan the subsystem also
    names the frequency (see :func:`~hydroformats.jsf_records.frequency_band`).
    """

    offset: int
    protocol_version: int
    session: int
    message_type: int
    command: int
    subsystem: int
    channel: int
    sequence: int
    payload: bytes


@dataclass(frozen=True)
class JsfGap:
    """Bytes outside any well-framed message: garbage between messages,
    a header whose declared size overruns the file, or a truncated
    final message. The scan resumes at the next 0x1601 marker."""

    offset: int
    size: int


def _read_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def iter_messages(source: str | Path | bytes) -> Iterator[JsfFrame | JsfGap]:
    """Walk the message stream; never raises on content.

    Yields :class:`JsfFrame` for every message whose header parses with
    a sane size and :class:`JsfGap` for every byte range that does not
    frame, in file order. A start marker whose declared size overruns
    the file is treated as part of the surrounding garbage and the scan
    resumes two bytes past it, so a corrupt length cannot swallow the
    valid messages behind it.
    """
    data = _read_bytes(source)
    n = len(data)
    position = 0
    gap_start = 0
    while True:
        sync = data.find(_SYNC, position)
        if sync == -1 or sync + _HEADER.size > n:
            break
        (_, protocol, session, message_type, command, subsystem, channel,
         sequence, _, size) = _HEADER.unpack_from(data, sync)
        if size < 0 or size > n - sync - _HEADER.size:
            position = sync + 2
            continue
        if sync > gap_start:
            yield JsfGap(offset=gap_start, size=sync - gap_start)
        body = sync + _HEADER.size
        yield JsfFrame(
            offset=sync, protocol_version=protocol, session=session,
            message_type=message_type, command=command, subsystem=subsystem,
            channel=channel, sequence=sequence,
            payload=data[body:body + size],
        )
        position = gap_start = body + size
    if gap_start < n:
        yield JsfGap(offset=gap_start, size=n - gap_start)


# --------------------------------------------------------------------------
# per-type decoders (frame -> Record; layouts per the ICD tables, anchor S9)
# --------------------------------------------------------------------------

# The 240-byte type 80 header, split along the ICD's own blocks.
_SON_FORMAT = struct.Struct("<iII2h3H3hhHHh2h2h")     # 0-43, Table 2-2
_SON_NAV = struct.Struct("<2f12hf2ih")                # 44-89, Table 2-3
_SON_PULSE = struct.Struct("<24sHIHhh3H2i2Hi2f")      # 90-155, Table 2-4
_SON_CPU = struct.Struct("<6h")                       # 156-167, Table 2-5
_SON_WEIGHT = struct.Struct("<2h")                    # 168-171, Table 2-6
_SON_ORIENT = struct.Struct("<H3h")                   # 172-179, Table 2-7
_SON_TRIGGER = struct.Struct("<2hH")                  # 180-185, Table 2-8
_SON_FIX = struct.Struct("<7h")                       # 186-199, Table 2-9
_SON_MISC = struct.Struct("<IH2h6siHhhhfiHH")         # 200-239, Table 2-10
_SONAR_HEADER_SIZE = 240

_BATHY_HEADER = struct.Struct("<3IH4BHf4fI3ffII4B2f")  # Table 2-28, 80 bytes
_BATHY_SAMPLE = struct.Struct("<HhBBBB")               # Table 2-29, 8 bytes
_ATTITUDE = struct.Struct("<3I5f")                     # Table 2-30, 32 bytes
_BATHY_PRESSURE = struct.Struct("<3I6f")               # Table 2-31, 36 bytes
_ALTITUDE = struct.Struct("<3I3f")                     # Table 2-32, 24 bytes
_POSITION = struct.Struct("<2I2H4d3f")                 # Table 2-33, 56 bytes
_NMEA_FIXED = struct.Struct("<2iB3B")                  # Table 2-19, 12 bytes
_PITCH_ROLL = struct.Struct("<2i4B9hHhHih2x")          # Table 2-20, 44 bytes
_PRESSURE = struct.Struct("<2i4B3iI3i")                # Table 2-21, 40 bytes
_SYSTEM_INFO = struct.Struct("<6i")                    # Table 2-17, 24 bytes

_ANGLE_PER_COUNT = 180.0 / 32768.0  # pitch/roll convention, Tables 2-7, 2-20

# Integers per sample by data format word (Table 2-2, bytes 34-35):
# envelope (0) and pre-matched-filter (2) data are one short per
# sample, analytic real/imaginary data (1, 9) two. Values above 255 are
# EdgeTech proprietary formats, left undecoded.
_SHORTS_PER_SAMPLE = {0: 1, 2: 1, 1: 2, 9: 2}


def _text(raw: bytes) -> str:
    return raw.decode("latin-1").rstrip("\x00 ")


def _extend_20bit(low16: int, msb4: int) -> int:
    """A 16-bit field extended to 20 bits by its MSB nibble (Table 2-2,
    bytes 16-17): the nibble becomes bits 16-19."""
    return (msb4 & 0xF) << 16 | low16


def _decode_sonar(frame: JsfFrame) -> Record:
    payload = frame.payload
    if len(payload) < _SONAR_HEADER_SIZE:
        raise ValueError(f"type 80 header needs {_SONAR_HEADER_SIZE} bytes, "
                         f"got {len(payload)}")
    (time_sec, starting_depth, ping_number, _, _, msb, lsb1, lsb2, _, _, _,
     id_code, validity, _, data_format, aft_cm, starboard_cm, _,
     _) = _SON_FORMAT.unpack_from(payload, 0)
    (kp, heave_m, *rest) = _SON_NAV.unpack_from(payload, 44)
    gap_filler_m, longitude_raw, latitude_raw, coord_units = rest[12:]
    (annotation, samples16, interval_ns, gain, level, _, start_dahz, end_dahz,
     sweep_ms, pressure_mpsi, depth_mm, sample_freq, pulse_id, altitude_mm,
     sound_speed, mixer_hz) = _SON_PULSE.unpack_from(payload, 90)
    year, day, hour, minute, second, time_basis = _SON_CPU.unpack_from(payload, 156)
    weighting_n, pulses = _SON_WEIGHT.unpack_from(payload, 168)
    heading_cdeg, pitch_raw, roll_raw, _ = _SON_ORIENT.unpack_from(payload, 172)
    _, trigger, mark16 = _SON_TRIGGER.unpack_from(payload, 180)
    (fix_hour, fix_minute, fix_second, course_whole, speed_dknots, fix_day,
     fix_year) = _SON_FIX.unpack_from(payload, 186)
    (msec_today, max_adc, _, _, software, spherical, packet_number, decim_x100,
     _, water_temp_dc, layback, _, cable_out_dm,
     _) = _SON_MISC.unpack_from(payload, 200)

    samples = _extend_20bit(samples16, msb >> 8)
    per = _SHORTS_PER_SAMPLE.get(data_format)
    trace: tuple[int, ...] | None = None
    if per is not None:
        # The declared count bounds the decode; a short data section
        # decodes as far as it goes (the record's `complete` property
        # compares). Bytes beyond the declared count are never samples.
        count = min(samples * per, (len(payload) - _SONAR_HEADER_SIZE) // 2)
        trace = struct.unpack_from(f"<{count}h", payload, _SONAR_HEADER_SIZE)
    return JsfSonarTrace(
        tag="SON", subsystem=frame.subsystem, channel=frame.channel,
        protocol_version=frame.protocol_version,
        time_sec=time_sec, milliseconds_today=msec_today,
        starting_depth_samples=starting_depth, ping_number=ping_number,
        id_code=id_code, validity=validity, data_format=data_format,
        samples=samples,
        sample_interval_ns=interval_ns,
        sample_interval_fraction_raw=lsb1 & 0xFF,
        sample_frequency_hz=sample_freq,
        start_frequency_hz=_extend_20bit(start_dahz, msb) * 10,
        end_frequency_hz=_extend_20bit(end_dahz, msb >> 4) * 10,
        sweep_length_ms=sweep_ms + (lsb2 >> 4 & 0x3FF) / 1000.0,
        gain_factor=gain, transmit_level_percent=level,
        pulse_identifier=pulse_id, pulses_in_water=pulses,
        weighting_factor=weighting_n, trace=trace,
        coordinate_units=coord_units, longitude_raw=longitude_raw,
        latitude_raw=latitude_raw, kilometers_of_pipe=kp, heave_m=heave_m,
        gap_filler_offset_m=gap_filler_m, annotation=_text(annotation),
        pressure_psi=pressure_mpsi / 1000.0, depth_m=depth_mm / 1000.0,
        altitude_m=altitude_mm / 1000.0, sound_speed_mps=sound_speed,
        mixer_hz=mixer_hz, cpu_time=(year, day, hour, minute, second),
        time_basis=time_basis, heading_degrees=heading_cdeg / 100.0,
        pitch_degrees=pitch_raw * _ANGLE_PER_COUNT,
        roll_degrees=roll_raw * _ANGLE_PER_COUNT,
        trigger_source=trigger, mark_number=_extend_20bit(mark16, msb >> 12),
        fix_time=(fix_year, fix_day, fix_hour, fix_minute, fix_second),
        course_degrees=course_whole + (lsb1 >> 8) / 100.0,
        speed_knots=speed_dknots / 10.0 + (lsb2 & 0xF) / 100.0,
        max_adc=max_adc, software_version=_text(software),
        spherical_correction_raw=spherical, packet_number=packet_number,
        adc_decimation=decim_x100 / 100.0,
        water_temperature_c=water_temp_dc / 10.0, layback_m=layback,
        cable_out_m=cable_out_dm / 10.0,
        antenna_to_tow_aft_m=aft_cm / 100.0,
        antenna_to_tow_starboard_m=starboard_cm / 100.0,
    )


def _decode_bathy(frame: JsfFrame) -> Record:
    payload = frame.payload
    (time_sec, time_nsec, ping_number, num_samples, channel, algorithm,
     num_pulses, pulse_phase, pulse_length, tx_amplitude, chirp_start,
     chirp_end, mixer_hz, sample_rate, offset_ns, delay_uncertainty,
     time_scale, time_accuracy, angle_scale, _, first_bottom_ns, revision,
     binning, tvg, _, span, bin_size) = _BATHY_HEADER.unpack_from(payload, 0)
    arrays: dict[str, tuple[int, ...] | None] = dict.fromkeys(
        ("time_delays", "angles", "amplitudes", "angle_uncertainties",
         "flags", "snr_db", "qualities"))
    if revision >= 4:
        # Only the revision 4+ sample layout is defined by the ICD
        # (Table 2-29). The declared count bounds the decode; a short
        # block decodes as far as it goes (`complete` compares).
        count = min(num_samples,
                    (len(payload) - _BATHY_HEADER.size) // _BATHY_SAMPLE.size)
        rows = [
            _BATHY_SAMPLE.unpack_from(
                payload, _BATHY_HEADER.size + i * _BATHY_SAMPLE.size)
            for i in range(count)
        ]
        arrays = {
            "time_delays": tuple(row[0] for row in rows),
            "angles": tuple(row[1] for row in rows),
            "amplitudes": tuple(row[2] for row in rows),
            "angle_uncertainties": tuple(row[3] for row in rows),
            "flags": tuple(row[4] for row in rows),
            "snr_db": tuple(row[5] & 0x1F for row in rows),
            "qualities": tuple(row[5] >> 5 for row in rows),
        }
    return JsfBathyPing(
        tag="BATH", subsystem=frame.subsystem, channel=channel,
        time_sec=time_sec, time_nsec=time_nsec, ping_number=ping_number,
        num_samples=num_samples, algorithm_type=algorithm,
        num_pulses=num_pulses, pulse_phase=pulse_phase,
        pulse_length_usec=pulse_length,
        transmit_pulse_amplitude=tx_amplitude, chirp_start_hz=chirp_start,
        chirp_end_hz=chirp_end, mixer_hz=mixer_hz, sample_rate_hz=sample_rate,
        offset_to_first_sample_ns=offset_ns,
        time_delay_uncertainty_sec=delay_uncertainty,
        time_scale_factor_sec=time_scale,
        time_scale_accuracy_percent=time_accuracy,
        angle_scale_factor_degrees=angle_scale,
        time_to_first_bottom_ns=first_bottom_ns, format_revision=revision,
        binning_flag=binning, tvg_db_per_100m=tvg, span=span,
        bin_size=bin_size, **arrays,
    )


def _decode_attitude(frame: JsfFrame) -> Record:
    (time_sec, time_nsec, valid, heading, heave, pitch, roll,
     yaw) = _ATTITUDE.unpack_from(frame.payload, 0)
    return JsfAttitude(
        tag="ATT", time_sec=time_sec, time_nsec=time_nsec, valid_flags=valid,
        heading_degrees=heading, heave_m=heave, pitch_degrees=pitch,
        roll_degrees=roll, yaw_degrees=yaw,
    )


def _decode_bathy_pressure(frame: JsfFrame) -> Record:
    (time_sec, time_nsec, valid, pressure, temperature, salinity,
     conductivity, sound_speed, depth) = _BATHY_PRESSURE.unpack_from(
        frame.payload, 0)
    return JsfBathyPressure(
        tag="BPRS", time_sec=time_sec, time_nsec=time_nsec, valid_flags=valid,
        pressure_psi=pressure, water_temperature_c=temperature,
        salinity_ppm=salinity, conductivity=conductivity,
        sound_speed_mps=sound_speed, depth_m=depth,
    )


def _decode_altitude(frame: JsfFrame) -> Record:
    (time_sec, time_nsec, valid, altitude, speed,
     heading) = _ALTITUDE.unpack_from(frame.payload, 0)
    return JsfAltitude(
        tag="ALT", time_sec=time_sec, time_nsec=time_nsec, valid_flags=valid,
        altitude_m=altitude, speed_knots=speed, heading_degrees=heading,
    )


def _decode_position(frame: JsfFrame) -> Record:
    (time_sec, time_nsec, valid, utm_zone, easting, northing, latitude,
     longitude, speed, heading, antenna) = _POSITION.unpack_from(
        frame.payload, 0)
    return JsfPosition(
        tag="POS", time_sec=time_sec, time_nsec=time_nsec, valid_flags=valid,
        utm_zone=utm_zone, easting_m=easting, northing_m=northing,
        latitude_degrees=latitude, longitude_degrees=longitude,
        speed_knots=speed, heading_degrees=heading, antenna_height_m=antenna,
    )


def _decode_nmea(frame: JsfFrame) -> Record:
    time_sec, msec, source, _, _, _ = _NMEA_FIXED.unpack_from(frame.payload, 0)
    text = frame.payload[_NMEA_FIXED.size:].decode("latin-1")
    return JsfNmea(tag="NMEA", time_sec=time_sec, milliseconds=msec,
                   source=source, text=text.rstrip("\x00\r\n"))


def _decode_pitch_roll(frame: JsfFrame) -> Record:
    (time_sec, msec, _, _, _, _, ax, ay, az, rx, ry, rz, pitch_raw, roll_raw,
     temp_dc, device_info, heave_mm, heading_cdeg, valid,
     yaw_cdeg) = _PITCH_ROLL.unpack_from(frame.payload, 0)
    return JsfPitchRoll(
        tag="MRU", time_sec=time_sec, milliseconds=msec,
        acceleration_g=tuple(v * 30.0 / 32768.0 for v in (ax, ay, az)),
        rate_dps=tuple(v * 750.0 / 32768.0 for v in (rx, ry, rz)),
        pitch_degrees=pitch_raw * _ANGLE_PER_COUNT,
        roll_degrees=roll_raw * _ANGLE_PER_COUNT,
        temperature_c=temp_dc / 10.0, device_info=device_info,
        heave_m=heave_mm / 1000.0, heading_degrees=heading_cdeg / 100.0,
        valid_flags=valid, yaw_degrees=yaw_cdeg / 100.0,
    )


def _decode_pressure(frame: JsfFrame) -> Record:
    (time_sec, msec, _, _, _, _, pressure_mpsi, temp_mc, salinity, valid,
     conductivity, sound_mmps, depth_m) = _PRESSURE.unpack_from(
        frame.payload, 0)
    return JsfPressureReading(
        tag="PSNS", time_sec=time_sec, milliseconds=msec,
        pressure_psi=pressure_mpsi / 1000.0, temperature_c=temp_mc / 1000.0,
        salinity_ppm=salinity, valid_flags=valid,
        conductivity_usiemens_per_cm=conductivity,
        sound_speed_mps=sound_mmps / 1000.0, depth_m=depth_m,
    )


def _decode_system_info(frame: JsfFrame) -> Record:
    (system_type, low_rate_io, version, subsystems, serial_devices,
     tow_serial) = _SYSTEM_INFO.unpack_from(frame.payload, 0)
    return JsfSystemInfo(
        tag="SYS", system_type=system_type, low_rate_io=low_rate_io,
        software_version=version, num_subsystems=subsystems,
        num_serial_devices=serial_devices, tow_vehicle_serial=tow_serial,
    )


_DECODERS = {
    SONAR_DATA: _decode_sonar,
    SYSTEM_INFORMATION: _decode_system_info,
    NMEA_STRING: _decode_nmea,
    PITCH_ROLL: _decode_pitch_roll,
    PRESSURE_SENSOR: _decode_pressure,
    BATHYMETRIC_DATA: _decode_bathy,
    ATTITUDE: _decode_attitude,
    BATHY_PRESSURE: _decode_bathy_pressure,
    ALTITUDE: _decode_altitude,
    POSITION: _decode_position,
}


def _decode(frame: JsfFrame) -> Record | None:
    """Typed record for a known message type, None for an unknown one."""
    decoder = _DECODERS.get(frame.message_type)
    if decoder is None:
        return None
    try:
        return decoder(frame)
    except (struct.error, ValueError) as error:
        return MalformedRecord(
            tag=_TAGS[frame.message_type],
            fields=(
                f"message_type={frame.message_type}",
                f"offset={frame.offset}",
                f"payload_size={len(frame.payload)}",
            ),
            error=f"truncated or undecodable payload: {error}",
        )


def read_jsf(source: str | Path | bytes) -> Iterator[Record]:
    """Stream typed records from a JSF file (path or bytes), in file
    order.

    Messages with unknown types are skipped (use :func:`iter_messages`
    to see them, or :func:`load_survey` to count them); known types
    whose payload does not satisfy the ICD layout yield
    :class:`~hydroformats.records.MalformedRecord`. Never raises on
    content.
    """
    for event in iter_messages(source):
        if not isinstance(event, JsfFrame):
            continue
        record = _decode(event)
        if record is not None:
            yield record


# --------------------------------------------------------------------------
# survey loading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class JsfChannelSeries:
    """One side scan channel's ping series: everything one fan recorded.

    A dual-frequency system yields four of these (two frequencies times
    port and starboard); a single-frequency system two. ``pings`` are
    in file order.
    """

    subsystem: int
    channel: int
    frequency_band: str | None
    side: str | None
    pings: tuple[JsfSonarTrace, ...]


@dataclass(frozen=True)
class JsfCounters:
    """Stream accounting from one :func:`load_survey` pass.

    ``messages`` counts every framed message, decoded or not.
    ``unknown_message_types`` are (type, count) pairs in ascending type
    order. ``bytes_skipped`` counts only bytes outside any intact frame
    (garbage, corrupt headers, a truncated tail).
    """

    messages: int
    unknown_message_types: tuple[tuple[int, int], ...]
    bytes_skipped: int


@dataclass(frozen=True)
class JsfSurvey:
    """One materialized JSF file, split into its working series.

    ``sidescan`` groups the type 80 traces per (subsystem, channel) in
    ascending order, so a dual-frequency file reads as four fans;
    ``bathy`` keeps the type 3000 pings flat in file order (each names
    its own side and frequency). ``system_info`` is the first type 182
    seen. Malformed records are dropped here but still counted in
    ``counters.messages``; use :func:`read_jsf` to see them.
    """

    system_info: JsfSystemInfo | None
    sidescan: tuple[JsfChannelSeries, ...]
    bathy: tuple[JsfBathyPing, ...]
    attitude: tuple[JsfAttitude, ...]
    pitch_rolls: tuple[JsfPitchRoll, ...]
    nmea: tuple[JsfNmea, ...]
    positions: tuple[JsfPosition, ...]
    bathy_pressure: tuple[JsfBathyPressure, ...]
    pressure_readings: tuple[JsfPressureReading, ...]
    altitude: tuple[JsfAltitude, ...]
    counters: JsfCounters


def load_survey(source: str | Path | bytes) -> JsfSurvey:
    """Materialize a whole JSF file into series (small files, tests).

    Preserves the raw observables everywhere: side scan traces keep
    their stored integers and weighting factor, bathymetric pings keep
    their time delay and angle counts with both scale factors, so the
    survey can be re-reduced under corrected sound speed or gains.
    """
    system_info: JsfSystemInfo | None = None
    traces: dict[tuple[int, int], list[JsfSonarTrace]] = {}
    bathy: list[JsfBathyPing] = []
    attitude: list[JsfAttitude] = []
    pitch_rolls: list[JsfPitchRoll] = []
    nmea: list[JsfNmea] = []
    positions: list[JsfPosition] = []
    bathy_pressure: list[JsfBathyPressure] = []
    pressure_readings: list[JsfPressureReading] = []
    altitude: list[JsfAltitude] = []
    unknown: dict[int, int] = {}
    messages = skipped = 0
    for event in iter_messages(source):
        if isinstance(event, JsfGap):
            skipped += event.size
            continue
        messages += 1
        record = _decode(event)
        if record is None:
            key = event.message_type
            unknown[key] = unknown.get(key, 0) + 1
        elif isinstance(record, JsfSonarTrace):
            traces.setdefault((record.subsystem, record.channel),
                              []).append(record)
        elif isinstance(record, JsfBathyPing):
            bathy.append(record)
        elif isinstance(record, JsfAttitude):
            attitude.append(record)
        elif isinstance(record, JsfPitchRoll):
            pitch_rolls.append(record)
        elif isinstance(record, JsfNmea):
            nmea.append(record)
        elif isinstance(record, JsfPosition):
            positions.append(record)
        elif isinstance(record, JsfBathyPressure):
            bathy_pressure.append(record)
        elif isinstance(record, JsfPressureReading):
            pressure_readings.append(record)
        elif isinstance(record, JsfAltitude):
            altitude.append(record)
        elif isinstance(record, JsfSystemInfo) and system_info is None:
            system_info = record
    return JsfSurvey(
        system_info=system_info,
        sidescan=tuple(
            JsfChannelSeries(
                subsystem=subsystem, channel=channel,
                frequency_band=frequency_band(subsystem),
                side=side_name(subsystem, channel), pings=tuple(pings),
            )
            for (subsystem, channel), pings in sorted(traces.items())
        ),
        bathy=tuple(bathy), attitude=tuple(attitude),
        pitch_rolls=tuple(pitch_rolls), nmea=tuple(nmea),
        positions=tuple(positions), bathy_pressure=tuple(bathy_pressure),
        pressure_readings=tuple(pressure_readings), altitude=tuple(altitude),
        counters=JsfCounters(
            messages=messages,
            unknown_message_types=tuple(sorted(unknown.items())),
            bytes_skipped=skipped,
        ),
    )
