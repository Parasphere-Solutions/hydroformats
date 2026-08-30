"""EdgeTech JSF dialect: message walking, decoding, survey loading.

Fixtures are synthetic bytes assembled in-test from the EdgeTech JSF
interface control document tables (see hydroformats/jsf.py for the
citation); all values are fictional. The real-sample integration test at
the bottom runs only when JSF_SAMPLE points at a real JSF file.
"""
import math
import os
import struct
import time

import pytest

from hydroformats.jsf import (
    JsfAltitude,
    JsfAttitude,
    JsfBathyPing,
    JsfBathyPressure,
    JsfFrame,
    JsfGap,
    JsfNmea,
    JsfPitchRoll,
    JsfPosition,
    JsfPressureReading,
    JsfSonarTrace,
    JsfSystemInfo,
    frequency_band,
    iter_messages,
    load_survey,
    read_jsf,
    side_name,
    sounding_usable,
)
from hydroformats.records import MalformedRecord

# ---------------------------------------------------------------------------
# byte builders (assembled by hand from the ICD tables, not via the parser)
# ---------------------------------------------------------------------------


def header(message_type: int, size: int, *, protocol: int = 0x0D,
           session: int = 0, command: int = 2, subsystem: int = 0,
           channel: int = 0, sequence: int = 0) -> bytes:
    """The 16-byte message header of Table 2-1, little endian: u16 start
    marker 0x1601, u8 protocol version, u8 session, u16 message type, u8
    command type, u8 subsystem, u8 channel, u8 sequence, u16 reserved,
    i32 size of the following message."""
    return struct.pack("<HBBHBBBBHi", 0x1601, protocol, session,
                       message_type, command, subsystem, channel,
                       sequence, 0, size)


def message(message_type: int, payload: bytes, **kw) -> bytes:
    return header(message_type, len(payload), **kw) + payload


def sonar_header(
    time_sec: int = 1_772_100_000,
    starting_depth: int = 40,
    ping_number: int = 1234,
    msb: int = 0,
    lsb1: int = 25 << 8,          # course fraction 0.25 degrees
    lsb2: int = (500 << 4) | 7,   # sweep 500 us, speed fraction 0.07 kt
    id_code: int = 1,
    validity: int = 0x4009,
    data_format: int = 0,
    aft_cm: int = 150,
    starboard_cm: int = -25,
    kp: float = 12.5,
    heave_m: float = 0.35,
    gap_filler_m: float = 0.0,
    longitude: int = -38_107_380,
    latitude: int = 26_760_720,
    coord_units: int = 2,
    annotation: bytes = b"LINE 7 BRIDGE A",
    samples: int = 8,
    interval_ns: int = 8000,
    adc_gain: int = 2,
    transmit_level: int = 85,
    start_dahz: int = 12_000,
    end_dahz: int = 12_600,
    sweep_ms: int = 10,
    pressure_mpsi: int = 14_700,
    depth_mm: int = 3_500,
    sample_freq: int = 25_000,
    pulse_id: int = 7,
    altitude_mm: int = 8_200,
    sound_speed: float = 1481.5,
    mixer_hz: float = 123_000.0,
    year: int = 2026,
    day: int = 229,
    hour: int = 14,
    minute: int = 5,
    second: int = 30,
    weighting_n: int = 2,
    pulses: int = 1,
    heading_cdeg: int = 9_125,
    pitch_raw: int = -1024,
    roll_raw: int = 2048,
    trigger: int = 1,
    mark: int = 0,
    fix_hour: int = 14,
    fix_minute: int = 5,
    fix_second: int = 29,
    course: int = 91,
    speed_dknots: int = 45,
    fix_day: int = 229,
    fix_year: int = 2026,
    msec_today: int = 50_730_123,
    max_adc: int = 1023,
    software: bytes = b"4.02",
    spherical: int = -1,
    packet_number: int = 1,
    adc_decim: int = 100,
    water_temp_dc: int = 185,
    layback: float = 42.5,
    cable_out_dm: int = 125,
) -> bytes:
    """The 240-byte sonar data header of Tables 2-2 through 2-10."""
    parts = (
        struct.pack("<iII", time_sec, starting_depth, ping_number),   # 0-11
        struct.pack("<2h", 0, 0),                                     # 12-15
        struct.pack("<3H", msb, lsb1, lsb2),                          # 16-21
        struct.pack("<3h", 0, 0, 0),                                  # 22-27
        struct.pack("<hHHh", id_code, validity, 0, data_format),      # 28-35
        struct.pack("<2h", aft_cm, starboard_cm),                     # 36-39
        struct.pack("<2h", 0, 0),                                     # 40-43
        struct.pack("<2f", kp, heave_m),                              # 44-51
        struct.pack("<12h", *([0] * 12)),                             # 52-75
        struct.pack("<f", gap_filler_m),                              # 76-79
        struct.pack("<2ih", longitude, latitude, coord_units),        # 80-89
        annotation.ljust(24, b"\x00"),                                # 90-113
        struct.pack("<HIHhh", samples, interval_ns, adc_gain,
                    transmit_level, 0),                               # 114-125
        struct.pack("<3H", start_dahz, end_dahz, sweep_ms),           # 126-131
        struct.pack("<2i", pressure_mpsi, depth_mm),                  # 132-139
        struct.pack("<2H", sample_freq, pulse_id),                    # 140-143
        struct.pack("<i2f", altitude_mm, sound_speed, mixer_hz),      # 144-155
        struct.pack("<6h", year, day, hour, minute, second, 3),       # 156-167
        struct.pack("<2h", weighting_n, pulses),                      # 168-171
        struct.pack("<H3h", heading_cdeg, pitch_raw, roll_raw, 0),    # 172-179
        struct.pack("<2hH", 0, trigger, mark),                        # 180-185
        struct.pack("<7h", fix_hour, fix_minute, fix_second, course,
                    speed_dknots, fix_day, fix_year),                 # 186-199
        struct.pack("<IH2h", msec_today, max_adc, 0, 0),              # 200-209
        software.ljust(6, b"\x00"),                                   # 210-215
        struct.pack("<iHh", spherical, packet_number, adc_decim),     # 216-223
        struct.pack("<2h", 0, water_temp_dc),                         # 224-227
        struct.pack("<fi", layback, 0),                               # 228-235
        struct.pack("<2H", cable_out_dm, 0),                          # 236-239
    )
    return b"".join(parts)


DEFAULT_TRACE = (100, 200, 400, 800, 1600, 3200, 12345, 32000)


def sonar_message(header_bytes: bytes | None = None,
                  trace: tuple[int, ...] = DEFAULT_TRACE,
                  subsystem: int = 20, channel: int = 0, **frame_kw) -> bytes:
    body = header_bytes if header_bytes is not None else sonar_header()
    payload = body + struct.pack(f"<{len(trace)}h", *trace)
    return message(80, payload, subsystem=subsystem, channel=channel,
                   **frame_kw)


def bathy_header(
    time_sec: int = 1_772_100_000,
    time_nsec: int = 250_000_000,
    ping_number: int = 987,
    num_samples: int = 3,
    channel: int = 1,
    algorithm: int = 2,
    num_pulses: int = 1,
    pulse_phase: int = 0,
    pulse_length_usec: int = 2_000,
    tx_amplitude: float = 0.8,
    chirp_start_hz: float = 520_000.0,
    chirp_end_hz: float = 580_000.0,
    mixer_hz: float = 550_000.0,
    sample_rate_hz: float = 34_722.0,
    offset_first_ns: int = 250_000,
    delay_uncertainty_sec: float = 2e-5,
    time_scale_sec: float = 1e-5,
    time_accuracy_pct: float = 1.5,
    angle_scale_deg: float = 0.01,
    time_first_bottom_ns: int = 9_000_000,
    revision: int = 5,
    binning: int = 1,
    tvg: int = 30,
    span: float = 40.0,
    bin_size: float = 0.05,
) -> bytes:
    """The 80-byte bathymetric header of Table 2-28."""
    return struct.pack(
        "<3IH4BHf4fI3ffII4B2f",
        time_sec, time_nsec, ping_number, num_samples,
        channel, algorithm, num_pulses, pulse_phase,
        pulse_length_usec, tx_amplitude,
        chirp_start_hz, chirp_end_hz, mixer_hz, sample_rate_hz,
        offset_first_ns,
        delay_uncertainty_sec, time_scale_sec, time_accuracy_pct,
        angle_scale_deg, 0, time_first_bottom_ns,
        revision, binning, tvg, 0,
        span, bin_size,
    )


def bathy_sample(time_delay: int, angle: int, amplitude: int = 51,
                 angle_uncertainty: int = 12, flag: int = 0,
                 snr: int = 18, quality: int = 6) -> bytes:
    """One 8-byte sample set of Table 2-29: u16 time delay, i16 angle,
    then amplitude, angle uncertainty, flag, and SNR (bits 0-4) packed
    with quality (bits 5-7)."""
    return struct.pack("<HhBBBB", time_delay, angle, amplitude,
                       angle_uncertainty, flag, snr | (quality << 5))


DEFAULT_BATHY_SAMPLES = (
    bathy_sample(20_000, 3000),
    bathy_sample(21_500, -450, amplitude=64, flag=0x20, snr=3, quality=0),
    bathy_sample(24_000, 9990, amplitude=255, angle_uncertainty=255,
                 snr=31, quality=7),
)


def bathy_message(header_bytes: bytes | None = None,
                  samples: tuple[bytes, ...] = DEFAULT_BATHY_SAMPLES,
                  subsystem: int = 41, channel: int = 1) -> bytes:
    body = header_bytes if header_bytes is not None else bathy_header()
    return message(3000, body + b"".join(samples), subsystem=subsystem,
                   channel=channel)


def attitude_payload(valid: int = 0b01111, heading: float = 91.25,
                     heave: float = 0.4, pitch: float = -2.5,
                     roll: float = 5.75, yaw: float = 0.0) -> bytes:
    return struct.pack("<3I5f", 1_772_100_000, 500_000_000, valid,
                       heading, heave, pitch, roll, yaw)


def pressure_3002_payload(valid: int = 0b110011) -> bytes:
    return struct.pack("<3I6f", 1_772_100_001, 0, valid,
                       14.7, 18.5, 31_500.0, 0.0, 1481.5, 3.5)


def altitude_payload(valid: int = 0b111) -> bytes:
    return struct.pack("<3I3f", 1_772_100_002, 750_000_000, valid,
                       8.2, 4.6, 91.0)


def position_payload(valid: int = 0b11111000) -> bytes:
    return struct.pack("<2I2H4d3f", 1_772_100_003, 0, valid, 0,
                       0.0, 0.0, 44.6012, -63.5123, 4.6, 91.0, -21.5)


def nmea_payload(text: bytes = b"$GPGGA,140530,4436.07,N*42\r\n",
                 source: int = 2) -> bytes:
    return struct.pack("<2i4B", 1_772_100_004, 250, source, 0, 0, 0) + text


def pitch_roll_payload() -> bytes:
    return struct.pack(
        "<2i4B9hHhHih2x",
        1_772_100_005, 500, 0, 0, 0, 0,
        1638, -819, 3277,        # accelerations
        328, -164, 66,           # rate gyros
        -1024, 2048,             # pitch, roll
        185,                     # temperature, 1/10 C
        0,                       # device info
        -350,                    # heave mm, positive down
        9125,                    # heading, 1/100 degree
        0b1111111111011,         # validity
        450,                     # yaw, 1/100 degree
    )


def pressure_2060_payload(valid: int = 0b111111) -> bytes:
    return struct.pack(
        "<2i4B3iI3i36x",
        1_772_100_006, 750, 0, 0, 0, 0,
        14_700, 18_500, 31_500, valid, 52_000, 1_481_500, 4,
    )


def system_info_payload(extra: bytes = b"") -> bytes:
    return struct.pack("<6i", 6205, 0, 40_206, 4, 2, 61_234) + extra


def stream(*messages: bytes) -> bytes:
    return b"".join(messages)


# ---------------------------------------------------------------------------
# message walking
# ---------------------------------------------------------------------------


def test_header_bytes_are_little_endian_and_marker_pinned():
    built = header(80, 4, protocol=0x0D, subsystem=20, channel=1)
    assert built == bytes((
        0x01, 0x16,              # start marker 0x1601, little endian
        0x0D, 0x00,              # protocol version, session
        0x50, 0x00,              # message type 80
        0x02, 20, 1, 0,          # command, subsystem, channel, sequence
        0x00, 0x00,              # reserved
        0x04, 0x00, 0x00, 0x00,  # size, little-endian i32
    ))


def test_walker_yields_frames_in_file_order():
    data = stream(message(2002, nmea_payload()), sonar_message())
    frames = list(iter_messages(data))
    assert [f.message_type for f in frames] == [2002, 80]
    assert all(isinstance(f, JsfFrame) for f in frames)
    assert frames[0].offset == 0
    assert frames[1].offset == 16 + len(nmea_payload())
    assert frames[1].subsystem == 20
    assert frames[1].channel == 0
    assert frames[1].protocol_version == 0x0D
    assert len(frames[1].payload) == 240 + 2 * len(DEFAULT_TRACE)


def test_walker_resynchronizes_after_garbage():
    good = message(2002, nmea_payload())
    data = b"\xde\xad\xbe\xef" + good
    events = list(iter_messages(data))
    assert isinstance(events[0], JsfGap)
    assert events[0].offset == 0
    assert events[0].size == 4
    assert isinstance(events[1], JsfFrame)
    assert events[1].offset == 4


def test_walker_degrades_on_truncated_final_message():
    good = message(2002, nmea_payload())
    cut = sonar_message()[:-11]
    events = list(iter_messages(stream(good, cut)))
    assert isinstance(events[0], JsfFrame)
    assert isinstance(events[-1], JsfGap)
    assert events[-1].offset == len(good)
    assert events[-1].size == len(cut)


def test_walker_treats_insane_declared_size_as_garbage():
    bad = header(80, 0x7FFFFFFF) + b"\x00" * 32
    events = list(iter_messages(bad))
    assert all(isinstance(e, JsfGap) for e in events)


def test_walker_rejects_non_jsf_bytes():
    assert list(iter_messages(b"")) == []
    events = list(iter_messages(b"not a jsf file"))
    assert all(isinstance(e, JsfGap) for e in events)


def test_walker_skips_sync_marker_inside_garbage():
    # A stray 0x1601 marker whose declared size overruns the file must
    # not swallow the valid message behind it.
    decoy = header(80, 500)[:16]
    good = message(2002, nmea_payload())
    events = list(iter_messages(decoy + good))
    frames = [e for e in events if isinstance(e, JsfFrame)]
    assert [f.message_type for f in frames] == [2002]
    assert frames[0].offset == 16


# ---------------------------------------------------------------------------
# channel and frequency identification
# ---------------------------------------------------------------------------


def test_subsystem_frequency_bands():
    assert frequency_band(0) is None          # sub-bottom
    assert frequency_band(20) == "low"
    assert frequency_band(21) == "high"
    assert frequency_band(22) == "very high"
    assert frequency_band(40) == "low"        # bathymetric
    assert frequency_band(41) == "high"
    assert frequency_band(42) == "very high"
    assert frequency_band(70) == "low"        # motion tolerant bathymetric
    assert frequency_band(71) == "high"
    assert frequency_band(72) == "very high"
    assert frequency_band(100) is None        # serial passthrough
    assert frequency_band(120) is None        # gap filler


def test_side_names():
    assert side_name(20, 0) == "port"
    assert side_name(41, 1) == "starboard"
    assert side_name(100, 3) is None          # serial: logical port number
    assert side_name(0, 0) is None            # sub-bottom: single channel


# ---------------------------------------------------------------------------
# sonar data message (type 80)
# ---------------------------------------------------------------------------


def _records(data: bytes):
    return list(read_jsf(data))


def test_sonar_trace_roundtrip():
    (rec,) = _records(sonar_message(subsystem=20, channel=1))
    assert isinstance(rec, JsfSonarTrace)
    assert rec.subsystem == 20
    assert rec.channel == 1
    assert rec.frequency_band == "low"
    assert rec.side == "starboard"
    assert rec.protocol_version == 0x0D
    assert rec.time_sec == 1_772_100_000
    assert rec.milliseconds_today == 50_730_123
    assert rec.time == pytest.approx(1_772_100_000.123)
    assert rec.starting_depth_samples == 40
    assert rec.ping_number == 1234
    assert rec.validity == 0x4009
    assert rec.data_format == 0
    assert rec.samples == 8
    assert rec.sample_interval_ns == 8000
    assert rec.sample_interval_fraction_raw == 0
    assert rec.sample_frequency_hz == 25_000
    assert rec.start_frequency_hz == 120_000
    assert rec.end_frequency_hz == 126_000
    assert rec.sweep_length_ms == pytest.approx(10.5)
    assert rec.gain_factor == 2
    assert rec.transmit_level_percent == 85
    assert rec.pulse_identifier == 7
    assert rec.pulses_in_water == 1
    assert rec.annotation == "LINE 7 BRIDGE A"
    assert rec.software_version == "4.02"
    assert rec.packet_number == 1
    assert rec.mark_number == 0
    assert rec.trigger_source == 1
    assert rec.max_adc == 1023
    assert rec.adc_decimation == pytest.approx(1.0)
    assert rec.spherical_correction_raw == -1
    assert rec.trace == DEFAULT_TRACE
    assert rec.complete is True


def test_sonar_trace_navigation_and_attitude_fields():
    (rec,) = _records(sonar_message())
    assert rec.coordinate_units == 2
    assert rec.longitude_raw == -38_107_380
    assert rec.latitude_raw == 26_760_720
    assert rec.longitude_degrees == pytest.approx(-63.5123)
    assert rec.latitude_degrees == pytest.approx(44.6012)
    assert rec.x_m is None
    assert rec.y_m is None
    assert rec.heave_m == pytest.approx(0.35)          # positive down
    assert rec.depth_m == pytest.approx(3.5)           # positive down
    assert rec.altitude_m == pytest.approx(8.2)        # positive up off seabed
    assert rec.pressure_psi == pytest.approx(14.7)
    assert rec.sound_speed_mps == pytest.approx(1481.5)
    assert rec.heading_degrees == pytest.approx(91.25)
    assert rec.pitch_degrees == pytest.approx(-1024 * 180.0 / 32768.0)
    assert rec.roll_degrees == pytest.approx(2048 * 180.0 / 32768.0)
    assert rec.course_degrees == pytest.approx(91.25)
    assert rec.speed_knots == pytest.approx(4.57)
    assert rec.water_temperature_c == pytest.approx(18.5)
    assert rec.layback_m == pytest.approx(42.5)
    assert rec.cable_out_m == pytest.approx(12.5)
    assert rec.kilometers_of_pipe == pytest.approx(12.5)
    assert rec.antenna_to_tow_aft_m == pytest.approx(1.5)
    assert rec.antenna_to_tow_starboard_m == pytest.approx(-0.25)
    assert rec.fix_time == (2026, 229, 14, 5, 29)
    assert rec.cpu_time == (2026, 229, 14, 5, 30)


def test_sonar_trace_xy_grid_units():
    for units, scale in ((1, 1000.0), (3, 10.0), (4, 100.0)):
        head = sonar_header(coord_units=units, longitude=123_456,
                            latitude=-78_900)
        (rec,) = _records(sonar_message(head))
        assert rec.longitude_degrees is None
        assert rec.latitude_degrees is None
        assert rec.x_m == pytest.approx(123_456 / scale)
        assert rec.y_m == pytest.approx(-78_900 / scale)


def test_weighting_factor_scaling_hand_computed():
    # Equation 2-2-1: scaled = stored * 2^-N. With N = 2 every stored
    # integer is divided by 4.
    (rec,) = _records(sonar_message(sonar_header(weighting_n=2)))
    assert rec.weighting_factor == 2
    assert rec.scaled() == pytest.approx(
        (25.0, 50.0, 100.0, 200.0, 400.0, 800.0, 3086.25, 8000.0))


def test_weighting_factor_negative_n_scales_up():
    # N = -5 makes 2^-N = 32: small stored integers restore to large
    # physical values.
    head = sonar_header(weighting_n=-5, samples=3)
    (rec,) = _records(sonar_message(head, trace=(3, -2, 100)))
    assert rec.scaled() == pytest.approx((96.0, -64.0, 3200.0))


def test_weighting_factor_zero_is_identity():
    head = sonar_header(weighting_n=0, samples=2)
    (rec,) = _records(sonar_message(head, trace=(7, -7)))
    assert rec.scaled() == pytest.approx((7.0, -7.0))


def test_msb_extension_to_20_bits():
    # Bits 8-11 of the MSB word extend the 16-bit sample count; bits 0-3
    # and 4-7 extend the transmit frequencies (Table 2-2). Declared
    # samples 2 + MSB 1 means 65538 samples; only two are present, so
    # the trace decodes short and the record reports incomplete.
    msb = (1 << 8) | (2 << 0) | (3 << 4)
    head = sonar_header(msb=msb, samples=2, start_dahz=28_928,
                        end_dahz=34_464)
    (rec,) = _records(sonar_message(head, trace=(5, 6)))
    assert rec.samples == 65_538
    assert rec.start_frequency_hz == ((2 << 16) | 28_928) * 10  # 1,600,000
    assert rec.end_frequency_hz == ((3 << 16) | 34_464) * 10    # 2,310,720
    assert rec.trace == (5, 6)
    assert rec.complete is False


def test_mark_number_msb_extension():
    head = sonar_header(msb=5 << 12, mark=17)
    (rec,) = _records(sonar_message(head))
    assert rec.mark_number == (5 << 16) | 17


def test_analytic_data_format_carries_two_shorts_per_sample():
    head = sonar_header(data_format=1, samples=3)
    trace = (10, -1, 20, -2, 30, -3)  # real, imaginary pairs
    (rec,) = _records(sonar_message(head, trace=trace))
    assert rec.data_format == 1
    assert rec.shorts_per_sample == 2
    assert rec.trace == trace
    assert rec.complete is True


def test_unknown_data_format_keeps_header_drops_trace():
    head = sonar_header(data_format=256)
    (rec,) = _records(sonar_message(head))
    assert isinstance(rec, JsfSonarTrace)
    assert rec.trace is None
    assert rec.scaled() is None
    assert rec.complete is False


def test_short_trace_is_tolerated_not_fatal():
    head = sonar_header(samples=8)
    (rec,) = _records(sonar_message(head, trace=(1, 2, 3)))
    assert rec.trace == (1, 2, 3)
    assert rec.samples == 8
    assert rec.complete is False


def test_extra_trace_bytes_are_ignored_not_decoded():
    # The declared sample count bounds the trace: trailing bytes beyond
    # it (alignment padding, future extras) never masquerade as samples.
    head = sonar_header(samples=2)
    (rec,) = _records(sonar_message(head, trace=(1, 2, 3)))
    assert rec.trace == (1, 2)
    assert rec.complete is True


def test_short_sonar_header_is_malformed_not_fatal():
    (rec,) = _records(message(80, sonar_header()[:100], subsystem=20))
    assert isinstance(rec, MalformedRecord)
    assert rec.tag == "SON"


# ---------------------------------------------------------------------------
# bathymetric data message (type 3000)
# ---------------------------------------------------------------------------


def test_bathy_header_roundtrip():
    (rec,) = _records(bathy_message())
    assert isinstance(rec, JsfBathyPing)
    assert rec.subsystem == 41
    assert rec.frequency_band == "high"
    assert rec.channel == 1
    assert rec.side == "starboard"
    assert rec.time_sec == 1_772_100_000
    assert rec.time_nsec == 250_000_000
    assert rec.time == pytest.approx(1_772_100_000.25)
    assert rec.ping_number == 987
    assert rec.num_samples == 3
    assert rec.algorithm_type == 2
    assert rec.num_pulses == 1
    assert rec.pulse_phase == 0
    assert rec.pulse_length_usec == 2_000
    assert rec.transmit_pulse_amplitude == pytest.approx(0.8)
    assert rec.chirp_start_hz == pytest.approx(520_000.0)
    assert rec.chirp_end_hz == pytest.approx(580_000.0)
    assert rec.mixer_hz == pytest.approx(550_000.0)
    assert rec.sample_rate_hz == pytest.approx(34_722.0)
    assert rec.offset_to_first_sample_ns == 250_000
    assert rec.time_delay_uncertainty_sec == pytest.approx(2e-5)
    assert rec.time_scale_factor_sec == pytest.approx(1e-5)
    assert rec.time_scale_accuracy_percent == pytest.approx(1.5)
    assert rec.angle_scale_factor_degrees == pytest.approx(0.01)
    assert rec.time_to_first_bottom_ns == 9_000_000
    assert rec.format_revision == 5
    assert rec.binning_flag == 1
    assert rec.tvg_db_per_100m == 30
    assert rec.span == pytest.approx(40.0)
    assert rec.bin_size == pytest.approx(0.05)


def test_bathy_samples_decode_raw_and_scaled():
    (rec,) = _records(bathy_message())
    assert rec.time_delays == (20_000, 21_500, 24_000)
    assert rec.angles == (3000, -450, 9990)
    assert rec.amplitudes == (51, 64, 255)
    # 0.5 dB increments, 0 to 127.5 dB (ICD 2.5.1.4.5)
    assert rec.amplitudes_db == pytest.approx((25.5, 32.0, 127.5))
    # 0.02 degree increments at the 2-sigma level, clamped at 5.1
    # degrees (ICD 2.5.1.4.6)
    assert rec.angle_uncertainties_degrees == pytest.approx(
        (0.24, 0.24, 5.1))
    assert rec.flags == (0, 0x20, 0)
    assert rec.snr_db == (18, 3, 31)
    assert rec.qualities == (6, 0, 7)


def test_bathy_null_bin_flag_marks_unusable():
    # Bit 5 marks a null bin (ICD 2.5.1.4.7): a false sounding parked at
    # the sonar head unless excluded.
    (rec,) = _records(bathy_message())
    assert [sounding_usable(flag) for flag in rec.flags] == \
        [True, False, True]
    assert sounding_usable(0x01) is False  # outlier removal flag


def test_bathy_echo_times_and_angles_hand_computed():
    # Equation 2-2: echo time = offset/1e9 + delay * scale. Equation
    # 2-5: angle from nadir = (-1)^(channel+1) * angle * scale, so raw
    # angle 3000 at scale 0.01 on the starboard channel is +30 degrees.
    (rec,) = _records(bathy_message())
    assert rec.echo_times_sec == pytest.approx(
        (0.20025, 0.21525, 0.24025))
    assert rec.angles_from_nadir_degrees == pytest.approx(
        (30.0, -4.5, 99.9))


def test_bathy_port_channel_flips_angle_sign():
    head = bathy_header(channel=0)
    (rec,) = _records(bathy_message(head, subsystem=40, channel=0))
    assert rec.side == "port"
    assert rec.frequency_band == "low"
    assert rec.angles_from_nadir_degrees == pytest.approx(
        (-30.0, 4.5, -99.9))


def test_bathy_soundings_hand_computed():
    # Equations 2-3, 2-7, 2-8 at 1500 m/s: slant = 750 * 0.20025 =
    # 150.1875 m; x = slant * sin(30), z = slant * cos(30).
    (rec,) = _records(bathy_message())
    soundings = rec.soundings_xz_m(1500.0)
    slant = 750.0 * 0.20025
    assert soundings[0][0] == pytest.approx(slant * math.sin(math.radians(30)))
    assert soundings[0][1] == pytest.approx(slant * math.cos(math.radians(30)))
    assert rec.slant_ranges_m(1500.0)[0] == pytest.approx(slant)


def test_bathy_nadir_depth_hand_computed():
    # Equation 2-6: depth below sounder = sos/2 * time to first bottom.
    (rec,) = _records(bathy_message())
    assert rec.nadir_depth_m(1500.0) == pytest.approx(750.0 * 0.009)


def test_bathy_old_revision_keeps_header_drops_samples():
    head = bathy_header(revision=2)
    (rec,) = _records(bathy_message(head))
    assert isinstance(rec, JsfBathyPing)
    assert rec.format_revision == 2
    assert rec.time_delays is None
    assert rec.angles is None
    assert rec.complete is False


def test_bathy_short_sample_block_is_tolerated():
    (rec,) = _records(bathy_message(samples=DEFAULT_BATHY_SAMPLES[:2]))
    assert rec.num_samples == 3
    assert len(rec.time_delays) == 2
    assert rec.complete is False


def test_bathy_extra_sample_bytes_are_ignored_not_decoded():
    head = bathy_header(num_samples=2)
    (rec,) = _records(bathy_message(head))
    assert len(rec.time_delays) == 2
    assert rec.complete is True


def test_bathy_short_header_is_malformed_not_fatal():
    (rec,) = _records(message(3000, bathy_header()[:40], subsystem=40))
    assert isinstance(rec, MalformedRecord)
    assert rec.tag == "BATH"


# ---------------------------------------------------------------------------
# navigation, attitude, and sensor messages
# ---------------------------------------------------------------------------


def test_attitude_roundtrip():
    (rec,) = _records(message(3001, attitude_payload()))
    assert isinstance(rec, JsfAttitude)
    assert rec.time == pytest.approx(1_772_100_000.5)
    assert rec.valid_flags == 0b01111
    assert rec.heading_valid is True
    assert rec.heave_valid is True
    assert rec.pitch_valid is True
    assert rec.roll_valid is True
    assert rec.yaw_valid is False
    assert rec.heading_degrees == pytest.approx(91.25)
    assert rec.heave_m == pytest.approx(0.4)       # positive down
    assert rec.pitch_degrees == pytest.approx(-2.5)
    assert rec.roll_degrees == pytest.approx(5.75)
    assert rec.yaw_degrees == pytest.approx(0.0)


def test_bathy_pressure_roundtrip():
    (rec,) = _records(message(3002, pressure_3002_payload()))
    assert isinstance(rec, JsfBathyPressure)
    assert rec.time_sec == 1_772_100_001
    assert rec.valid_flags == 0b110011
    assert rec.pressure_psi == pytest.approx(14.7)
    assert rec.water_temperature_c == pytest.approx(18.5)
    assert rec.salinity_ppm == pytest.approx(31_500.0)
    assert rec.sound_speed_mps == pytest.approx(1481.5)
    assert rec.depth_m == pytest.approx(3.5)       # positive down


def test_altitude_roundtrip():
    (rec,) = _records(message(3003, altitude_payload()))
    assert isinstance(rec, JsfAltitude)
    assert rec.time == pytest.approx(1_772_100_002.75)
    assert rec.altitude_m == pytest.approx(8.2)    # positive up off seabed
    assert rec.speed_knots == pytest.approx(4.6)
    assert rec.heading_degrees == pytest.approx(91.0)


def test_position_roundtrip():
    (rec,) = _records(message(3004, position_payload()))
    assert isinstance(rec, JsfPosition)
    assert rec.time_sec == 1_772_100_003
    assert rec.valid_flags == 0b11111000
    assert rec.latitude_degrees == pytest.approx(44.6012)   # north positive
    assert rec.longitude_degrees == pytest.approx(-63.5123)  # east positive
    assert rec.speed_knots == pytest.approx(4.6)
    assert rec.heading_degrees == pytest.approx(91.0)
    assert rec.antenna_height_m == pytest.approx(-21.5)     # positive up
    assert rec.utm_zone == 0
    assert rec.easting_m == pytest.approx(0.0)
    assert rec.northing_m == pytest.approx(0.0)


def test_nmea_roundtrip():
    (rec,) = _records(message(2002, nmea_payload()))
    assert isinstance(rec, JsfNmea)
    assert rec.time == pytest.approx(1_772_100_004.25)
    assert rec.source == 2
    assert rec.text == "$GPGGA,140530,4436.07,N*42"


def test_pitch_roll_roundtrip():
    (rec,) = _records(message(2020, pitch_roll_payload()))
    assert isinstance(rec, JsfPitchRoll)
    assert rec.time == pytest.approx(1_772_100_005.5)
    # Table 2-20 scalings: accelerations by 30/32768 G, rates by
    # 750/32768 degrees per second, angles by 180/32768 degrees.
    assert rec.acceleration_g == pytest.approx(
        (1638 * 30.0 / 32768.0, -819 * 30.0 / 32768.0,
         3277 * 30.0 / 32768.0))
    assert rec.rate_dps == pytest.approx(
        (328 * 750.0 / 32768.0, -164 * 750.0 / 32768.0,
         66 * 750.0 / 32768.0))
    assert rec.pitch_degrees == pytest.approx(-1024 * 180.0 / 32768.0)
    assert rec.roll_degrees == pytest.approx(2048 * 180.0 / 32768.0)
    assert rec.temperature_c == pytest.approx(18.5)
    assert rec.heave_m == pytest.approx(-0.35)     # positive down
    assert rec.heading_degrees == pytest.approx(91.25)
    assert rec.yaw_degrees == pytest.approx(4.5)
    assert rec.valid_flags == 0b1111111111011


def test_pressure_reading_roundtrip():
    (rec,) = _records(message(2060, pressure_2060_payload()))
    assert isinstance(rec, JsfPressureReading)
    assert rec.time == pytest.approx(1_772_100_006.75)
    assert rec.pressure_psi == pytest.approx(14.7)
    assert rec.temperature_c == pytest.approx(18.5)
    assert rec.salinity_ppm == 31_500
    assert rec.conductivity_usiemens_per_cm == 52_000
    assert rec.sound_speed_mps == pytest.approx(1481.5)
    assert rec.depth_m == 4                        # whole meters, positive down
    assert rec.valid_flags == 0b111111


def test_system_info_roundtrip():
    (rec,) = _records(message(182, system_info_payload()))
    assert isinstance(rec, JsfSystemInfo)
    assert rec.system_type == 6205
    assert rec.low_rate_io == 0
    assert rec.software_version == 40_206
    assert rec.num_subsystems == 4
    assert rec.num_serial_devices == 2
    assert rec.tow_vehicle_serial == 61_234


def test_system_info_tolerates_reserved_tail():
    (rec,) = _records(message(182, system_info_payload(b"\x00" * 100)))
    assert isinstance(rec, JsfSystemInfo)
    assert rec.system_type == 6205


def test_short_known_payload_is_malformed_not_fatal():
    (rec,) = _records(message(3001, attitude_payload()[:10]))
    assert isinstance(rec, MalformedRecord)
    assert rec.tag == "ATT"


def test_unknown_message_type_is_skipped_by_read_jsf():
    data = stream(message(2071, b"\x00" * 32), message(182, system_info_payload()))
    records = _records(data)
    assert len(records) == 1
    assert isinstance(records[0], JsfSystemInfo)


def test_read_jsf_yields_records_in_file_order():
    data = stream(
        message(182, system_info_payload()),
        message(2002, nmea_payload()),
        sonar_message(subsystem=20, channel=0),
        sonar_message(subsystem=20, channel=1),
        bathy_message(),
        message(3001, attitude_payload()),
    )
    kinds = [type(r).__name__ for r in _records(data)]
    assert kinds == ["JsfSystemInfo", "JsfNmea", "JsfSonarTrace",
                     "JsfSonarTrace", "JsfBathyPing", "JsfAttitude"]


def test_docstring_layout_sizes():
    """The fixed-part byte counts quoted in the record docstrings."""
    assert len(header(80, 0)) == 16
    assert len(sonar_header()) == 240
    assert len(bathy_header()) == 80
    assert len(bathy_sample(0, 0)) == 8
    assert len(attitude_payload()) == 32
    assert len(pressure_3002_payload()) == 36
    assert len(altitude_payload()) == 24
    assert len(position_payload()) == 56
    assert len(pitch_roll_payload()) == 44
    assert len(pressure_2060_payload()) == 76
    assert len(system_info_payload()) == 24


# ---------------------------------------------------------------------------
# load_survey
# ---------------------------------------------------------------------------


def _survey_stream() -> bytes:
    return stream(
        message(182, system_info_payload()),
        message(2002, nmea_payload()),
        sonar_message(subsystem=20, channel=0),
        sonar_message(subsystem=20, channel=1),
        sonar_message(subsystem=21, channel=0),
        sonar_message(subsystem=21, channel=1),
        sonar_message(sonar_header(ping_number=1235), subsystem=20, channel=0),
        bathy_message(bathy_header(channel=0), subsystem=40, channel=0),
        bathy_message(),
        message(3001, attitude_payload()),
        message(3002, pressure_3002_payload()),
        message(3003, altitude_payload()),
        message(3004, position_payload()),
        message(2020, pitch_roll_payload()),
        message(2060, pressure_2060_payload()),
        message(2071, b"\x00" * 32),
        message(2071, b"\x00" * 32),
        message(9999, b"\xaa\xbb"),
    )


def test_load_survey_bundles_series_and_counters():
    survey = load_survey(_survey_stream())
    assert survey.system_info is not None
    assert survey.system_info.system_type == 6205
    assert [(s.subsystem, s.channel) for s in survey.sidescan] == \
        [(20, 0), (20, 1), (21, 0), (21, 1)]
    assert [len(s.pings) for s in survey.sidescan] == [2, 1, 1, 1]
    assert survey.sidescan[0].frequency_band == "low"
    assert survey.sidescan[0].side == "port"
    assert survey.sidescan[3].frequency_band == "high"
    assert survey.sidescan[3].side == "starboard"
    assert [p.ping_number for p in survey.sidescan[0].pings] == [1234, 1235]
    assert [(b.subsystem, b.channel) for b in survey.bathy] == \
        [(40, 0), (41, 1)]
    assert len(survey.attitude) == 1
    assert len(survey.pitch_rolls) == 1
    assert len(survey.nmea) == 1
    assert len(survey.positions) == 1
    assert len(survey.bathy_pressure) == 1
    assert len(survey.pressure_readings) == 1
    assert len(survey.altitude) == 1
    assert survey.counters.messages == 18
    assert dict(survey.counters.unknown_message_types) == {2071: 2, 9999: 1}
    assert survey.counters.bytes_skipped == 0


def test_load_survey_counts_truncated_tail_bytes():
    data = stream(_survey_stream(), sonar_message()[:-13])
    survey = load_survey(data)
    assert survey.counters.bytes_skipped == len(sonar_message()) - 13


def test_load_survey_never_raises_on_garbage():
    survey = load_survey(b"\x07\x03" * 40)
    assert survey.sidescan == ()
    assert survey.counters.messages == 0
    assert survey.counters.bytes_skipped == 80


# ---------------------------------------------------------------------------
# real sample validation
# ---------------------------------------------------------------------------

_SAMPLE = os.environ.get("JSF_SAMPLE", "")


@pytest.mark.skipif(not (_SAMPLE and os.path.exists(_SAMPLE)),
                    reason="JSF_SAMPLE not set or file missing")
def test_real_sample_statistics():
    """Statistics measured 2026-08-30 from galv2017_line07.000.jsf
    (EdgeTech SB-512i chirp sub-bottom, 3200-XS topside, offshore
    Galveston TX, May 2017; 100,005,102 bytes; source URL and license in
    docs/FORMAT-SOURCES.md anchor S9).

    Every byte frames (nothing skipped, nothing malformed): 8,421 sonar
    data messages on the sub-bottom subsystem, 3,368 NMEA strings, and
    1,347 type 2040 messages (undocumented in ICD Rev R, skipped and
    counted). Consecutive ping numbers at a 5 Hz ping rate over the
    28-minute line; every ping is protocol 8, matched-filtered analytic
    data (format 1) of 2,893 samples, complete against the declared
    count. The 46,080 ns sample interval and the 21,701 Hz sample
    frequency words reproduce each other, pinning both decodes; the
    0.7 to 12 kHz chirp matches the SB-512i. The CPU calendar block
    equals the seconds-since-1970 word on every ping (the recorder's
    clock was left unset at 2003, faithfully decoded on both fields)
    while the NMEA fix riders carry the true 2017 survey date, agreeing
    with the interleaved GPRMC sentences to the second: the two time
    tracks pin the CPU and navigation block layouts independently. The
    per-ping weighting factors span 3 to 7 with raw trace maxima
    hugging the 16-bit ceiling, block floating point behaving exactly
    as Equation 2-2-1 describes.
    """
    survey = load_survey(_SAMPLE)
    counters = survey.counters
    assert counters.messages == 13_136
    assert counters.bytes_skipped == 0
    assert counters.unknown_message_types == ((2040, 1347),)
    assert not [r for r in read_jsf(_SAMPLE) if isinstance(r, MalformedRecord)]

    assert survey.system_info is None
    assert survey.bathy == ()
    assert survey.attitude == ()
    assert survey.positions == ()
    assert len(survey.nmea) == 3368

    (series,) = survey.sidescan
    assert (series.subsystem, series.channel) == (0, 0)  # sub-bottom
    assert series.frequency_band is None
    assert series.side is None
    pings = series.pings
    assert len(pings) == 8421

    assert {p.protocol_version for p in pings} == {8}
    assert {p.data_format for p in pings} == {1}  # analytic pairs
    assert {p.samples for p in pings} == {2893}
    assert all(p.complete for p in pings)
    assert {p.sample_interval_ns for p in pings} == {46_080}
    assert {p.sample_frequency_hz for p in pings} == {21_701}
    interval_times_rate = 46_080e-9 * 21_701
    assert interval_times_rate == pytest.approx(1.0, abs=1e-4)
    assert {p.start_frequency_hz for p in pings} == {700}
    assert {p.end_frequency_hz for p in pings} == {12_000}
    assert {p.sweep_length_ms for p in pings} == {20.0}
    assert {p.coordinate_units for p in pings} == {2}

    assert pings[0].ping_number == 14
    assert pings[-1].ping_number == 8434
    assert len(pings) == 8434 - 14 + 1  # consecutive, none dropped
    assert all(b.ping_number == a.ping_number + 1
               for a, b in zip(pings, pings[1:], strict=False))
    duration = pings[-1].time - pings[0].time
    assert duration == pytest.approx(1684.208, abs=0.01)
    assert (len(pings) - 1) / duration == pytest.approx(5.0, abs=0.01)

    # The CPU calendar block restates the seconds word on every ping.
    for ping in pings:
        parts = time.gmtime(ping.time_sec)
        assert ping.cpu_time == (parts.tm_year, parts.tm_yday, parts.tm_hour,
                                 parts.tm_min, parts.tm_sec)
        seconds_today = (ping.cpu_time[2] * 3600 + ping.cpu_time[3] * 60
                         + ping.cpu_time[4])
        assert abs(ping.milliseconds_today // 1000 - seconds_today) <= 1

    # Navigation riders: fix time carries the true survey date and the
    # positions sit in the Galveston offshore box the GPRMC stream pins.
    assert pings[0].fix_time == (2017, 143, 15, 55, 6)  # 2017-05-23
    latitudes = [p.latitude_degrees for p in pings]
    longitudes = [p.longitude_degrees for p in pings]
    assert min(latitudes) == pytest.approx(29.14216, abs=1e-4)
    assert max(latitudes) == pytest.approx(29.16180, abs=1e-4)
    assert min(longitudes) == pytest.approx(-94.80072, abs=1e-4)
    assert max(longitudes) == pytest.approx(-94.77537, abs=1e-4)
    rmc = [n for n in survey.nmea if n.text.startswith("$GPRMC")]
    gga = [n for n in survey.nmea if n.text.startswith("$GPGGA")]
    assert len(rmc) == len(gga) == 1684  # one pair per second of line
    assert rmc[0].text.startswith("$GPRMC,155507,A,2908.5307,N,09448.0423,W")
    assert ",230517," in rmc[0].text  # 23 May 2017

    # Block floating point: per-ping exponents, mantissas normalized
    # into the 16-bit range, and physical scale restored by 2^-N.
    weights = {}
    for ping in pings:
        weights[ping.weighting_factor] = weights.get(ping.weighting_factor, 0) + 1
    assert weights == {3: 257, 4: 4105, 5: 3978, 6: 80, 7: 1}
    raw_maxima = [max(abs(v) for v in p.trace) for p in pings]
    assert max(raw_maxima) == 31_999  # inside int16
    assert sum(1 for m in raw_maxima if m >= 16_384) == 8140
    scaled_max = max(max(abs(v) for v in p.scaled()) for p in pings)
    assert scaled_max == pytest.approx(3851.5)
