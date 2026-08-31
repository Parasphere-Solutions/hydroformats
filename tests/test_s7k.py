"""Teledyne RESON s7k dialect: frame walking, decoding, swath loading.

Fixtures are synthetic bytes assembled in-test from the Teledyne 7k
Data Format Definition tables (see hydroformats/s7k.py for the
citation); all values are fictional. The real-sample integration test
at the bottom runs only when S7K_SAMPLE points at a real s7k file.
"""
import math
import os
import struct

import pytest

from hydroformats.records import MalformedRecord
from hydroformats.s7k import (
    S7kBathymetry,
    S7kBeamformedHeader,
    S7kBeamGeometry,
    S7kCompressedWaterColumnHeader,
    S7kCtd,
    S7kFrame,
    S7kGap,
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
    iter_records,
    load_swath,
    read_s7k,
)

# ---------------------------------------------------------------------------
# byte builders (assembled by hand from the DFD tables, not via the parser)
# ---------------------------------------------------------------------------

TIME = (2000, 1, 30.0, 0, 0)  # year, day, seconds, hours, minutes (7KTIME)
EPOCH = 946_684_830.0         # 2000-01-01T00:00:30Z, by hand


def drf(record_type: int, payload: bytes, *, device: int = 7125,
        enumerator: int = 0, protocol: int = 5, rth_offset: int = 60,
        checksum: str = "valid", optional: bytes = b"", od_id: int = 0,
        time: tuple = TIME, size_override: int | None = None) -> bytes:
    """One whole record: the 64-byte Data Record Frame of Table 5 (plus
    any header expansion bytes when rth_offset exceeds 60), the data
    section, and the trailing checksum word. ``checksum`` is "valid",
    "corrupt" (flag set, stored word off by one) or "off" (flag clear,
    stored word meaningless)."""
    padding = b"\x00" * (rth_offset - 60)
    size = 4 + rth_offset + len(payload) + len(optional) + 4
    od_offset = 4 + rth_offset + len(payload) if optional else 0
    flags = 0x0000 if checksum == "off" else 0x0001
    head = (
        struct.pack("<HH", protocol, rth_offset)
        + b"\xff\xff\x00\x00"                       # sync 0x0000FFFF
        + struct.pack("<III", size if size_override is None else
                      size_override, od_offset, od_id)
        + struct.pack("<HHfBB", *time)              # 7KTIME
        + struct.pack("<HII", 1, record_type, device)
        + struct.pack("<HHIHHIII", 0, enumerator, 0, flags, 0, 0, 0, 0)
    )
    body = head + padding + payload + optional
    word = sum(body) & 0xFFFFFFFF
    if checksum == "corrupt":
        word = (word + 1) & 0xFFFFFFFF
    if checksum == "off":
        word = 0xDEADBEEF
    return body + struct.pack("<I", word)


def stream(*records: bytes) -> bytes:
    return b"".join(records)


def position_payload(position_type: int = 0) -> bytes:
    """Table 14: fictional fix at 41.5 N 71.4 W (or the same doubles as
    grid coordinates when position_type is 1)."""
    return struct.pack(
        "<IfdddBBBBB", 0, 0.05, math.radians(41.5), math.radians(-71.4),
        -12.5, position_type, 19, 0, 1, 9)


def ctd_payload(count: int = 2) -> bytes:
    """Tables 24-25: a two-sample cast, salinity and depth flavors."""
    head = struct.pack(
        "<f6BH2dfI", 0.0, 1, 1, 1, 1, 1, 0b11111, 0,
        math.radians(41.5), math.radians(-71.4), 1.0, count)
    samples = (
        (31.5, 18.5, 0.5, 1481.5, 82.0),
        (31.6, 18.1, 5.5, 1480.0, 81.5),
    )[:count]
    return head + b"".join(struct.pack("<5f", *row) for row in samples)


def geodesy_payload() -> bytes:
    """Table 26: WGS84 with a UTM grid, 320 bytes."""
    return struct.pack(
        "<32s2d16x32sIB7d35x32s2B5di50x",
        b"WGS84", 6378137.0, 298.257223563, b"WGS84", 0, 3,
        1.5, -2.5, 3.5, 0.0, 0.0, 0.0, 1.0,
        b"UTM", 0, 0, 0.0, math.radians(-75.0), 500000.0, 0.0, 0.9996, -1)


def motion_payload() -> bytes:
    return struct.pack("<3f", math.radians(5.0), math.radians(-2.0), 0.25)


def heading_payload() -> bytes:
    return struct.pack("<f", math.radians(91.5))


def settings_payload(ping: int = 42, sample_rate: float = 34722.0) -> bytes:
    """Table 39, built in the DFD's own blocks; 156 bytes."""
    parts = (
        struct.pack("<QIH", 4_002_017, ping, 0),               # 0-13
        struct.pack("<4f", 400_000.0, sample_rate,
                    30_000.0, 0.000034),                       # 14-29
        struct.pack("<2I", 0, 1),                              # 30-37
        struct.pack("<f", 0.5),                                # 38-41
        struct.pack("<2H", 1, 0),                              # 42-45
        struct.pack("<5f", 12.0, 0.1, 60.0, 220.0, 20.0),      # 46-65
        struct.pack("<I", 0x8000),                             # 66-69
        struct.pack("<I", 1),                                  # 70-73
        struct.pack("<5f", 0.0, 0.01, 0.02, 2.2, 0.0),         # 74-93
        struct.pack("<If", 1, 25.0),                           # 94-101
        struct.pack("<2I", 0, 1),                              # 102-109
        struct.pack("<If", 0, 30.0),                           # 110-117
        struct.pack("<I", 0x1),                                # 118-121
        struct.pack("<8f", 0.017, 1.0, 75.0, 0.5, 40.0,
                    82.0, 1481.5, 30.0),                       # 122-153
        struct.pack("<H", 0),                                  # 154-155
    )
    return b"".join(parts)


BEAM_ANGLES = (-1.2, -0.4, 0.4, 1.2)


def beam_geometry_payload(with_delays: bool = False) -> bytes:
    """Tables 44-45: four beams; the transmit delay array only when the
    sonar model has one (detected from the record length)."""
    count = len(BEAM_ANGLES)
    body = struct.pack("<QI", 4_002_017, count)
    body += struct.pack(f"<{count}f", *([0.0] * count))
    body += struct.pack(f"<{count}f", *BEAM_ANGLES)
    body += struct.pack(f"<{count}f", *([0.017] * count))
    body += struct.pack(f"<{count}f", *([0.008] * count))
    if with_delays:
        body += struct.pack(f"<{count}f", 0.0, 0.25, 0.5, 0.75)
    return body


def bathymetry_payload(gates: bool = True) -> bytes:
    """Tables 46-47: three beams, quality bits and relative intensity,
    with or without the travel time filter gates."""
    body = struct.pack("<QIHIBBf", 4_002_017, 42, 0, 3, 0b11, 0, 1481.5)
    body += struct.pack("<3f", 0.0136, 0.0141, 0.0150)   # two-way seconds
    body += bytes((0b1111, 0b0101, 0b0000))              # quality
    body += struct.pack("<3f", 8.5, 7.25, 0.0)           # intensity
    if gates:
        body += struct.pack("<3f", 0.010, 0.010, 0.011)
        body += struct.pack("<3f", 0.020, 0.021, 0.022)
    return body


DETECTIONS = (
    # beam, detection point, rx angle, flags, quality, uncertainty,
    # intensity, min limit, max limit (Table 72)
    (19, 1920.5, -1.28, 0x5, 0x3, 0.02, 90.5, 1800.0, 2100.0),
    (250, 470.0, 0.0, 0x1, 0x3, 0.01, 120.0, 450.0, 500.0),
    (480, 2222.25, 1.25, 0x4006, 0x0, 0.05, 63.0, 2000.0, 2400.0),
)


def raw_detection_payload(block: int = 34, count: int | None = None,
                          sample_rate: float = 34722.0) -> bytes:
    """Tables 71-72: the declared detection block size says which of
    the appended per-detection fields are present (34 bytes is the full
    Version 3.10 block; 26 was a common earlier vintage)."""
    rows = DETECTIONS[:count if count is not None else len(DETECTIONS)]
    head = struct.pack("<QIHIIBIfff", 4_002_017, 42, 0, len(rows), block,
                       3, 0x2, sample_rate, 0.01, 0.0) + b"\x00" * 60
    body = b""
    for beam, point, angle, flags, quality, *extras in rows:
        full = struct.pack("<HffII", beam, point, angle, flags, quality)
        full += struct.pack("<4f", *extras)
        full = full[:block].ljust(block, b"\xEE")  # newer fields, undecoded
        body += full
    return head + body


SNIPPET_WINDOWS = (
    # beam, first sample, detection sample, last sample (Table 75)
    (19, 1918, 1920, 1922),
    (250, 469, 470, 471),
    (480, 2225, 2222, 2224),  # start past end: no data for this beam
)
SNIPPET_SAMPLES = ((100, 4000, 900, 80, 7), (65535, 2, 1))


def snippet_payload(flags: int = 0, error_flag: int = 0) -> bytes:
    """Tables 74-75: three windows, one of them empty, followed by the
    16-bit (or 32-bit, flags bit 0) intensity series."""
    head = struct.pack("<QIHHBBI", 4_002_017, 42, 0, len(SNIPPET_WINDOWS),
                       error_flag, 0x9, flags) + b"\x00" * 24
    if error_flag:
        return head
    body = b"".join(struct.pack("<HIII", *w) for w in SNIPPET_WINDOWS)
    code = "I" if flags & 1 else "H"
    for series in SNIPPET_SAMPLES:
        body += struct.pack(f"<{len(series)}{code}", *series)
    return head + body


BACKSCATTER = ((-22.5, -20.25, -24.0, -21.0, -23.5), (-31.5, -30.0, -32.25))
FOOTPRINTS = ((0.5, 0.5, 0.55, 0.55, 0.6), (1.5, 1.5, 1.6))


def backscatter_payload(footprints: bool = False,
                        error_flag: int = 0) -> bytes:
    """Tables 100-101: same windows as the snippets, float dB samples,
    footprint areas riding along when control flag bit 6 is set."""
    control = 0x107 | (0x40 if footprints else 0)
    head = struct.pack("<QIHHBIf", 4_002_017, 42, 0, len(SNIPPET_WINDOWS),
                       error_flag, control, 82.0) + b"\x00" * 24
    body = b"".join(struct.pack("<HIII", *w) for w in SNIPPET_WINDOWS)
    for series in BACKSCATTER:
        body += struct.pack(f"<{len(series)}f", *series)
    if footprints:
        for series in FOOTPRINTS:
            body += struct.pack(f"<{len(series)}f", *series)
    return head + body


def sound_velocity_payload(fields: int = 3) -> bytes:
    """Table 117: the temperature and pressure words are absent on
    older writers (detected from the record length)."""
    return struct.pack("<3f", 1481.5, 291.65, 202_650.0)[:4 * fields]


def remote_settings_payload(truncate_at: int | None = None) -> bytes:
    """Table 113, built in blocks; 260 bytes complete. Truncating after
    the 148-byte 7000-equivalent core mimics an older vintage (the DFD
    appends new fields at the end)."""
    core = (
        struct.pack("<QI", 4_002_017, 42),                     # 0-11
        struct.pack("<4f", 400_000.0, 34722.0,
                    30_000.0, 0.000034),                       # 12-27
        struct.pack("<2If2H", 0, 1, 0.5, 1, 0),                # 28-41
        struct.pack("<5f", 12.0, 0.1, 60.0, 220.0, 20.0),      # 42-61
        struct.pack("<2I", 0x8000, 1),                         # 62-69
        struct.pack("<5f", 0.0, 0.01, 0.02, 2.2, 0.0),         # 70-89
        struct.pack("<If2I", 1, 25.0, 0, 1),                   # 90-105
        struct.pack("<IfI", 0, 30.0, 0x1),                     # 106-117
        struct.pack("<7f", 1.0, 75.0, 0.5, 40.0,
                    82.0, 1481.5, 30.0),                       # 118-145
    )
    tail = (
        struct.pack("<2B", 0, 15),                             # 148-149
        struct.pack("<3f", 0.1, -2.0, 0.3),                    # tx offsets
        struct.pack("<3f", 0.0, 0.01, 0.0),                    # head tilt
        struct.pack("<I2H", 1, 2, 0),                          # ping state
        struct.pack("<2f", 1.0, 30.0),                         # adaptive gate
        struct.pack("<2d", 0.001, 0.0),                        # trigger out
        struct.pack("<H8x", 0),                                # 81xx + res.
        struct.pack("<fBx", 15.0, 0),                          # alt gain
        struct.pack("<Hf", 512, 2.618),                        # beams, cover
        struct.pack("<2B", 0, 1),                              # mode, filter
        struct.pack("<4f", 0.0, 1.309, 0.0, 0.25),             # steer, flex
        struct.pack("<H", 3),                                  # beam mode
        struct.pack("<2fI", 0.0, 400_000.0, 7),                # tilt, f, elem
    )
    body = b"".join(core) + b"".join(tail)
    return body[:truncate_at] if truncate_at is not None else body


def beamformed_payload(beams: int = 4, samples: int = 6) -> bytes:
    """Table 63 header plus a sample matrix the reader must skip."""
    head = struct.pack("<QIHHI", 4_002_017, 42, 0, beams, samples)
    head += b"\x00" * 32
    return head + b"\x11" * (beams * samples * 4)


def compressed_wc_payload() -> bytes:
    """Table 82 header plus per-beam data the reader must skip."""
    head = struct.pack("<QIHHIIIIffI", 4_002_017, 42, 0, 2, 100, 50,
                       0x102, 0, 17361.0, 1.0, 0)
    return head + b"\x22" * 40


# ---------------------------------------------------------------------------
# frame walking
# ---------------------------------------------------------------------------


def test_drf_bytes_are_little_endian_and_sync_pinned():
    built = drf(1013, heading_payload())
    head = bytes((
        0x05, 0x00,              # protocol version 5
        0x3C, 0x00,              # offset 60 to the RTH
        0xFF, 0xFF, 0x00, 0x00,  # sync pattern 0x0000FFFF
        0x48, 0x00, 0x00, 0x00,  # size 72 (frame + 4-byte payload + word)
        0x00, 0x00, 0x00, 0x00,  # no optional data
        0x00, 0x00, 0x00, 0x00,  # optional data identifier
        0xD0, 0x07,              # 7KTIME year 2000
        0x01, 0x00,              # day 1
        0x00, 0x00, 0xF0, 0x41,  # seconds 30.0, f32
        0x00, 0x00,              # hours, minutes
        0x01, 0x00,              # record version 1
        0xF5, 0x03, 0x00, 0x00,  # record type 1013
        0xD5, 0x1B, 0x00, 0x00,  # device identifier 7125
        0x00, 0x00, 0x00, 0x00,  # reserved, enumerator 0
        0x00, 0x00, 0x00, 0x00,  # reserved
        0x01, 0x00,              # flags: checksum valid
        0x00, 0x00,              # reserved
        0x00, 0x00, 0x00, 0x00,  # reserved
        0x00, 0x00, 0x00, 0x00,  # fragmented total, always zero
        0x00, 0x00, 0x00, 0x00,  # fragment number, always zero
    ))
    assert built[:64] == head
    assert built[64:68] == heading_payload()
    assert built[68:] == struct.pack("<I", sum(built[:68]))


def test_walker_yields_frames_in_file_order():
    data = stream(drf(1013, heading_payload()),
                  drf(1012, motion_payload(), enumerator=1))
    frames = list(iter_records(data))
    assert [f.record_type for f in frames] == [1013, 1012]
    assert all(isinstance(f, S7kFrame) for f in frames)
    assert frames[0].offset == 0
    assert frames[1].offset == 72
    assert frames[0].protocol_version == 5
    assert frames[0].device_identifier == 7125
    assert frames[1].system_enumerator == 1
    assert frames[0].payload == heading_payload()
    assert (frames[0].year, frames[0].day) == (2000, 1)
    assert frames[0].seconds == 30.0


def test_walker_resynchronizes_after_garbage():
    good = drf(1013, heading_payload())
    events = list(iter_records(b"\xde\xad\xbe\xef\xf0\x0d" + good))
    assert isinstance(events[0], S7kGap)
    assert (events[0].offset, events[0].size) == (0, 6)
    assert isinstance(events[1], S7kFrame)
    assert events[1].offset == 6


def test_walker_skips_sync_pattern_inside_garbage():
    # A stray sync pattern whose declared size overruns the file must
    # not swallow the valid record behind it.
    decoy = drf(1013, heading_payload(), size_override=0x7FFFFFFF)[:64]
    good = drf(1012, motion_payload())
    events = list(iter_records(decoy + good))
    frames = [e for e in events if isinstance(e, S7kFrame)]
    assert [f.record_type for f in frames] == [1012]
    assert frames[0].offset == 64
    gaps = [e for e in events if isinstance(e, S7kGap)]
    assert sum(g.size for g in gaps) == 64


def test_walker_degrades_on_truncated_final_record():
    good = drf(1013, heading_payload())
    cut = drf(1012, motion_payload())[:-5]
    events = list(iter_records(stream(good, cut)))
    assert isinstance(events[0], S7kFrame)
    assert isinstance(events[-1], S7kGap)
    assert events[-1].offset == len(good)
    assert events[-1].size == len(cut)


def test_walker_rejects_offsets_below_the_version_5_frame():
    # A frame whose RTH offset is below 60 cannot satisfy the version 5
    # DRF layout; it degrades to a gap.
    bent = bytearray(drf(1013, heading_payload()))
    bent[2:4] = struct.pack("<H", 32)
    events = list(iter_records(bytes(bent)))
    assert all(isinstance(e, S7kGap) for e in events)


def test_walker_accepts_expanded_frame_header():
    # The offset field exists so the DRF can grow; a frame that pushes
    # the RTH to 68 bytes still walks and decodes.
    record = drf(1013, heading_payload(), rth_offset=68)
    frames = list(iter_records(record))
    assert len(frames) == 1
    assert frames[0].payload == heading_payload()
    decoded = list(read_s7k(record))
    assert isinstance(decoded[0], S7kHeading)


def test_walker_rejects_non_s7k_bytes():
    assert list(iter_records(b"")) == []
    events = list(iter_records(b"not an s7k file, not even close"))
    assert all(isinstance(e, S7kGap) for e in events)


def test_checksum_verified_when_flags_promise_it():
    ok = list(iter_records(drf(1013, heading_payload())))[0]
    assert ok.checksum_ok
    bad = list(iter_records(drf(1013, heading_payload(),
                                checksum="corrupt")))[0]
    assert isinstance(bad, S7kFrame)
    assert not bad.checksum_ok
    # The record still frames and decodes; a bad sum is reported, not
    # fatal.
    assert isinstance(next(read_s7k(drf(1013, heading_payload(),
                                        checksum="corrupt"))), S7kHeading)


def test_checksum_ignored_when_flags_say_invalid():
    frame = list(iter_records(drf(1013, heading_payload(),
                                  checksum="off")))[0]
    assert frame.flags & 0x0001 == 0
    assert frame.checksum == 0xDEADBEEF
    assert frame.checksum_ok


def test_optional_data_is_split_from_the_payload():
    rider = b"\x01\x02\x03\x04optional"
    record = drf(1012, motion_payload(), optional=rider, od_id=7)
    frame = list(iter_records(record))[0]
    assert frame.payload == motion_payload()
    assert frame.optional_data == rider
    assert frame.optional_data_identifier == 7
    assert isinstance(next(read_s7k(record)), S7kRollPitchHeave)


# ---------------------------------------------------------------------------
# record decoding
# ---------------------------------------------------------------------------


def decode_one(record_bytes: bytes):
    records = list(read_s7k(record_bytes))
    assert len(records) == 1
    return records[0]


def test_time_property_from_the_frame():
    record = decode_one(drf(1013, heading_payload()))
    assert record.time == EPOCH
    untimed = decode_one(drf(1013, heading_payload(),
                             time=(0, 0, 0.0, 0, 0)))
    assert untimed.time is None


def test_position_geographic():
    record = decode_one(drf(1003, position_payload()))
    assert isinstance(record, S7kPosition)
    assert record.tag == "POS"
    assert record.datum_identifier == 0
    assert record.latency_sec == pytest.approx(0.05)
    assert record.position_type == 0
    assert record.latitude_degrees == pytest.approx(41.5)
    assert record.longitude_degrees == pytest.approx(-71.4)
    assert record.height_m == -12.5
    assert record.northing_m is None and record.easting_m is None
    assert record.utm_zone == 19
    assert record.positioning_method == 1
    assert record.number_of_satellites == 9


def test_position_without_the_optional_satellite_count():
    # Table 14 marks the trailing satellite count optional, and real
    # PDS-logged files write the record 36 bytes long, without it.
    record = decode_one(drf(1003, position_payload()[:-1]))
    assert isinstance(record, S7kPosition)
    assert record.latitude_degrees == pytest.approx(41.5)
    assert record.number_of_satellites is None


def test_position_grid():
    record = decode_one(drf(1003, position_payload(position_type=1)))
    assert record.latitude_degrees is None
    assert record.longitude_degrees is None
    assert record.northing_m == pytest.approx(math.radians(41.5))
    assert record.easting_m == pytest.approx(math.radians(-71.4))


def test_ctd_profile():
    record = decode_one(drf(1010, ctd_payload()))
    assert isinstance(record, S7kCtd)
    assert record.num_samples == 2
    assert record.conductivity_flag == 1   # salinity flavor
    assert record.pressure_flag == 1       # depth flavor
    assert record.sample_validity == 0b11111
    assert record.conductivity_salinity == pytest.approx((31.5, 31.6))
    assert record.temperature_c == pytest.approx((18.5, 18.1))
    assert record.pressure_depth == pytest.approx((0.5, 5.5))
    assert record.sound_velocity_mps == pytest.approx((1481.5, 1480.0))
    assert record.absorption_db_per_km == pytest.approx((82.0, 81.5))
    assert record.latitude_rad == pytest.approx(math.radians(41.5))


def test_ctd_truncated_samples_degrade_to_malformed():
    cut = ctd_payload()[:-8]
    record = decode_one(drf(1010, cut))
    assert isinstance(record, MalformedRecord)
    assert record.tag == "CTD"
    assert "record_type=1010" in record.fields


def test_geodesy():
    record = decode_one(drf(1011, geodesy_payload()))
    assert isinstance(record, S7kGeodesy)
    assert record.spheroid == "WGS84"
    assert record.semi_major_axis_m == 6378137.0
    assert record.inverse_flattening == pytest.approx(298.257223563)
    assert record.datum == "WGS84"
    assert record.number_of_parameters == 3
    assert (record.dx_m, record.dy_m, record.dz_m) == (1.5, -2.5, 3.5)
    assert record.grid_name == "UTM"
    assert record.central_meridian == pytest.approx(math.radians(-75.0))
    assert record.false_easting_m == 500000.0
    assert record.central_scale_factor == 0.9996
    assert record.custom_identifier == -1


def test_roll_pitch_heave_and_heading():
    motion = decode_one(drf(1012, motion_payload()))
    assert isinstance(motion, S7kRollPitchHeave)
    assert motion.roll_degrees == pytest.approx(5.0)
    assert motion.pitch_degrees == pytest.approx(-2.0)
    assert motion.heave_m == 0.25
    heading = decode_one(drf(1013, heading_payload()))
    assert isinstance(heading, S7kHeading)
    assert heading.heading_degrees == pytest.approx(91.5)


def test_sonar_settings():
    record = decode_one(drf(7000, settings_payload()))
    assert isinstance(record, S7kSonarSettings)
    assert len(settings_payload()) == 156  # Table 39 fixed size
    assert record.sonar_id == 4_002_017
    assert record.ping_number == 42
    assert record.frequency_hz == 400_000.0
    assert record.sample_rate_hz == pytest.approx(34722.0)
    assert record.tx_pulse_width_sec == pytest.approx(0.000034)
    assert record.tx_pulse_envelope == 1
    assert record.tx_pulse_envelope_parameter == 0.5
    assert record.max_ping_rate_hz == 12.0
    assert record.range_selection_m == 60.0
    assert record.power_selection_db == 220.0
    assert record.gain_selection_db == 20.0
    assert record.active  # control flag bit 15
    assert record.projector_beam_width_vertical_rad == pytest.approx(0.02)
    assert record.projector_weighting_parameter == 25.0
    assert record.receive_flags == 0x1
    assert record.receive_beam_width_rad == pytest.approx(0.017)
    assert record.min_range_m == 1.0 and record.max_range_m == 75.0
    assert record.min_depth_m == 0.5 and record.max_depth_m == 40.0
    assert record.absorption_db_per_km == 82.0
    assert record.sound_velocity_mps == 1481.5
    assert record.spreading_loss_db == 30.0


def test_beam_geometry_without_transmit_delays():
    record = decode_one(drf(7004, beam_geometry_payload()))
    assert isinstance(record, S7kBeamGeometry)
    assert record.num_beams == 4
    assert record.horizontal_angles_rad == pytest.approx(BEAM_ANGLES)
    assert record.horizontal_angles_degrees[0] == pytest.approx(-68.7549354)
    assert record.vertical_angles_rad == (0.0,) * 4
    assert record.beam_width_y_rad == pytest.approx((0.017,) * 4)
    assert record.beam_width_x_rad == pytest.approx((0.008,) * 4)
    assert record.tx_delays is None


def test_beam_geometry_with_transmit_delays():
    record = decode_one(drf(7004, beam_geometry_payload(with_delays=True)))
    assert record.tx_delays == pytest.approx((0.0, 0.25, 0.5, 0.75))


def test_beam_geometry_truncated_degrades_to_malformed():
    record = decode_one(drf(7004, beam_geometry_payload()[:-3]))
    assert isinstance(record, MalformedRecord)
    assert record.tag == "BEAM"


def test_bathymetry_with_gates():
    record = decode_one(drf(7006, bathymetry_payload()))
    assert isinstance(record, S7kBathymetry)
    assert record.num_beams == 3
    assert record.flags == 0b11
    assert record.sound_velocity_manual == 0
    assert record.sound_velocity_mps == 1481.5
    assert record.travel_times_sec == pytest.approx((0.0136, 0.0141, 0.0150))
    assert record.qualities == (0b1111, 0b0101, 0b0000)
    assert record.intensities == pytest.approx((8.5, 7.25, 0.0))
    assert record.min_filter_sec == pytest.approx((0.010, 0.010, 0.011))
    assert record.max_filter_sec == pytest.approx((0.020, 0.021, 0.022))


def test_bathymetry_older_vintage_without_gates():
    record = decode_one(drf(7006, bathymetry_payload(gates=False)))
    assert record.travel_times_sec == pytest.approx((0.0136, 0.0141, 0.0150))
    assert record.min_filter_sec is None
    assert record.max_filter_sec is None


def test_raw_detections_full_block():
    record = decode_one(drf(7027, raw_detection_payload()))
    assert isinstance(record, S7kRawDetections)
    assert record.num_detections == 3
    assert record.detection_size == 34
    assert record.detection_algorithm == 3
    assert record.sampling_rate_hz == pytest.approx(34722.0)
    assert record.tx_angle_rad == pytest.approx(0.01)
    assert record.beam_numbers == (19, 250, 480)
    assert record.detection_points == pytest.approx((1920.5, 470.0, 2222.25))
    assert record.rx_angles_rad == pytest.approx((-1.28, 0.0, 1.25))
    assert record.rx_angles_degrees[2] == pytest.approx(71.6197244)
    assert record.detection_flags == (0x5, 0x1, 0x4006)
    assert record.qualities == (0x3, 0x3, 0x0)
    assert record.uncertainties == pytest.approx((0.02, 0.01, 0.05))
    assert record.intensities == pytest.approx((90.5, 120.0, 63.0))
    assert record.min_limits == pytest.approx((1800.0, 450.0, 2000.0))
    assert record.max_limits == pytest.approx((2100.0, 500.0, 2400.0))
    # Appendix F: detection point over sampling rate is two-way time.
    assert record.two_way_travel_times_sec[1] == pytest.approx(
        470.0 / 34722.0)


def test_raw_detections_older_block_vintages():
    base = decode_one(drf(7027, raw_detection_payload(block=18)))
    assert base.beam_numbers == (19, 250, 480)
    assert base.uncertainties is None
    assert base.intensities is None
    with_uncertainty = decode_one(drf(7027, raw_detection_payload(block=22)))
    assert with_uncertainty.uncertainties == pytest.approx(
        (0.02, 0.01, 0.05))
    assert with_uncertainty.intensities is None
    vintage_26 = decode_one(drf(7027, raw_detection_payload(block=26)))
    assert vintage_26.intensities == pytest.approx((90.5, 120.0, 63.0))
    assert vintage_26.min_limits is None
    assert vintage_26.max_limits is None


def test_raw_detections_newer_block_keeps_known_fields():
    # A block bigger than the Version 3.10 layout means appended fields
    # this reader does not know; the known ones still decode.
    record = decode_one(drf(7027, raw_detection_payload(block=38)))
    assert record.detection_size == 38
    assert record.beam_numbers == (19, 250, 480)
    assert record.max_limits == pytest.approx((2100.0, 500.0, 2400.0))


def test_raw_detections_undersized_block_degrades_to_malformed():
    record = decode_one(drf(7027, raw_detection_payload(block=12)))
    assert isinstance(record, MalformedRecord)
    assert record.tag == "DET"


def test_raw_detections_truncated_degrades_to_malformed():
    record = decode_one(drf(7027, raw_detection_payload()[:-10]))
    assert isinstance(record, MalformedRecord)


def test_snippets_16_bit():
    record = decode_one(drf(7028, snippet_payload()))
    assert isinstance(record, S7kSnippets)
    assert record.num_detections == 3
    assert not record.is_32_bit
    assert record.error_flag == 0
    assert record.control_flags == 0x9
    assert record.beam_numbers == (19, 250, 480)
    assert record.snippet_starts == (1918, 469, 2225)
    assert record.detection_samples == (1920, 470, 2222)
    assert record.snippet_ends == (1922, 471, 2224)
    assert record.snippets == ((100, 4000, 900, 80, 7), (65535, 2, 1), ())


def test_snippets_32_bit():
    record = decode_one(drf(7028, snippet_payload(flags=1)))
    assert record.is_32_bit
    assert record.snippets == ((100, 4000, 900, 80, 7), (65535, 2, 1), ())


def test_snippets_error_flag_means_no_data():
    record = decode_one(drf(7028, snippet_payload(error_flag=6)))
    assert record.error_flag == 6
    assert record.beam_numbers == ()
    assert record.snippets == ()


def test_snippets_truncated_degrade_to_malformed():
    record = decode_one(drf(7028, snippet_payload()[:-3]))
    assert isinstance(record, MalformedRecord)
    assert record.tag == "SNIP"


def test_snippet_backscatter():
    record = decode_one(drf(7058, backscatter_payload()))
    assert isinstance(record, S7kSnippetBackscatter)
    assert record.num_detections == 3
    assert record.calibrated
    assert record.absorption_db_per_km == 82.0
    assert record.begin_samples == (1918, 469, 2225)
    assert record.end_samples == (1922, 471, 2224)
    assert record.backscatter_db[0] == pytest.approx(BACKSCATTER[0])
    assert record.backscatter_db[1] == pytest.approx(BACKSCATTER[1])
    assert record.backscatter_db[2] == ()   # begin past end: empty window
    assert record.footprints_m2 is None


def test_snippet_backscatter_with_footprints():
    record = decode_one(drf(7058, backscatter_payload(footprints=True)))
    assert record.footprints_m2 is not None
    assert record.footprints_m2[0] == pytest.approx(FOOTPRINTS[0])
    assert record.footprints_m2[2] == ()


def test_snippet_backscatter_uncalibrated_still_carries_data():
    record = decode_one(drf(7058, backscatter_payload(error_flag=3)))
    assert not record.calibrated
    assert record.error_flag == 3
    assert record.backscatter_db[0] == pytest.approx(BACKSCATTER[0])


def test_sound_velocity_vintages():
    full = decode_one(drf(7610, sound_velocity_payload()))
    assert isinstance(full, S7kSoundVelocity)
    assert full.sound_velocity_mps == 1481.5
    assert full.temperature_k == pytest.approx(291.65)
    assert full.pressure_pa == pytest.approx(202_650.0)
    no_pressure = decode_one(drf(7610, sound_velocity_payload(fields=2)))
    assert no_pressure.temperature_k == pytest.approx(291.65)
    assert no_pressure.pressure_pa is None
    oldest = decode_one(drf(7610, sound_velocity_payload(fields=1)))
    assert oldest.temperature_k is None
    assert oldest.pressure_pa is None


def test_remote_sonar_settings_complete():
    payload = remote_settings_payload()
    assert len(payload) == 260  # Table 113 complete size
    record = decode_one(drf(7503, payload))
    assert isinstance(record, S7kRemoteSonarSettings)
    assert record.sonar_id == 4_002_017
    assert record.ping_number == 42
    assert record.sample_rate_hz == pytest.approx(34722.0)
    assert record.sound_velocity_mps == 1481.5
    assert record.spreading_loss_db == 30.0
    assert record.automatic_filter_window == 15
    assert record.tx_offset_x_m == pytest.approx(0.1)
    assert record.tx_offset_y_m == pytest.approx(-2.0)
    assert record.tx_offset_z_m == pytest.approx(0.3)
    assert record.head_tilt_y_rad == pytest.approx(0.01)
    assert record.ping_state == 1
    assert record.beam_spacing_mode == 2
    assert record.adaptive_gate_max_depth_m == 30.0
    assert record.trigger_out_width_sec == pytest.approx(0.001)
    assert record.alternate_gain_db == 15.0
    assert record.custom_beams == 512
    assert record.coverage_angle_rad == pytest.approx(2.618)
    assert record.quality_filter_flags == 1
    assert record.flexmode_coverage_rad == pytest.approx(1.309)
    assert record.constant_spacing_m == pytest.approx(0.25)
    assert record.beam_mode_selection == 3
    assert record.applied_frequency_hz == 400_000.0
    assert record.element_number == 7


def test_remote_sonar_settings_older_vintage_tail_is_none():
    record = decode_one(drf(7503, remote_settings_payload(truncate_at=148)))
    assert record.spreading_loss_db == 30.0
    assert record.vernier_operation_mode is None
    assert record.tx_offset_x_m is None
    assert record.element_number is None
    # A vintage ending mid-tail keeps the fields it reaches.
    partial = decode_one(drf(7503, remote_settings_payload(truncate_at=174)))
    assert partial.head_tilt_z_rad == 0.0
    assert partial.ping_state is None


def test_water_column_headers_skip_their_payloads():
    beamformed = decode_one(drf(7018, beamformed_payload()))
    assert isinstance(beamformed, S7kBeamformedHeader)
    assert (beamformed.beams, beamformed.samples) == (4, 6)
    compressed = decode_one(drf(7042, compressed_wc_payload()))
    assert isinstance(compressed, S7kCompressedWaterColumnHeader)
    assert compressed.beams == 2
    assert compressed.samples == 100
    assert compressed.compressed_samples == 50
    assert compressed.flags == 0x102
    assert compressed.sample_rate_hz == pytest.approx(17361.0)


def test_unknown_record_types_are_skipped():
    data = stream(drf(7300, b"\x00" * 16), drf(1013, heading_payload()))
    records = list(read_s7k(data))
    assert len(records) == 1
    assert isinstance(records[0], S7kHeading)


# ---------------------------------------------------------------------------
# swath loading
# ---------------------------------------------------------------------------


def full_stream() -> bytes:
    return stream(
        drf(7200, b"\x00" * 40),                    # file header, unanchored
        drf(1003, position_payload()),
        drf(1012, motion_payload()),
        drf(1013, heading_payload()),
        drf(7610, sound_velocity_payload()),
        drf(7000, settings_payload()),
        drf(7004, beam_geometry_payload()),
        drf(7027, raw_detection_payload()),
        drf(7028, snippet_payload()),
        drf(7058, backscatter_payload()),
        drf(7006, bathymetry_payload()),
        drf(7503, remote_settings_payload()),
        drf(7018, beamformed_payload()),
        drf(7042, compressed_wc_payload()),
        drf(1010, ctd_payload()),
        drf(1011, geodesy_payload()),
    )


def test_load_swath_assembles_pings_and_series():
    swath = load_swath(full_stream())
    assert swath.counters.records == 16
    assert swath.counters.unknown_record_types == ((7200, 1),)
    assert swath.counters.checksum_failures == 0
    assert swath.counters.bytes_skipped == 0

    assert len(swath.pings) == 1
    ping = swath.pings[0]
    assert ping.ping_number == 42
    assert ping.device_identifier == 7125
    assert ping.settings is not None
    assert ping.raw_detections is not None
    assert ping.snippets is not None
    assert ping.backscatter is not None
    assert ping.bathymetry is not None
    assert ping.raw_detections.num_detections == 3
    assert ping.snippets.snippets[0] == (100, 4000, 900, 80, 7)

    assert len(swath.positions) == 1
    assert len(swath.roll_pitch_heaves) == 1
    assert len(swath.headings) == 1
    assert len(swath.sound_velocities) == 1
    assert len(swath.beam_geometries) == 1
    assert len(swath.remote_settings) == 1
    assert len(swath.ctds) == 1
    assert len(swath.geodesies) == 1
    assert len(swath.water_column) == 2
    assert isinstance(swath.water_column[0], S7kBeamformedHeader)
    assert isinstance(swath.water_column[1],
                      S7kCompressedWaterColumnHeader)


def test_load_swath_groups_pings_by_device_and_number():
    data = stream(
        drf(7000, settings_payload(ping=7)),
        drf(7027, raw_detection_payload()),          # ping 42
        drf(7000, settings_payload()),               # ping 42
        drf(7027, raw_detection_payload(), enumerator=1),  # twin head
    )
    swath = load_swath(data)
    assert [(p.ping_number, p.system_enumerator) for p in swath.pings] == [
        (7, 0), (42, 0), (42, 1)]
    assert swath.pings[0].raw_detections is None
    assert swath.pings[1].settings is not None
    assert swath.pings[1].raw_detections is not None
    assert swath.pings[2].settings is None


def test_load_swath_counts_checksum_failures():
    data = stream(drf(7610, sound_velocity_payload(), checksum="corrupt"),
                  drf(7610, sound_velocity_payload(), checksum="off"),
                  drf(1013, heading_payload()))
    swath = load_swath(data)
    assert swath.counters.records == 3
    assert swath.counters.checksum_failures == 1
    assert len(swath.sound_velocities) == 2  # reported, never dropped


def test_load_swath_drops_malformed_but_counts_the_frame():
    data = stream(drf(1010, ctd_payload()[:-8]),
                  drf(1013, heading_payload()))
    swath = load_swath(data)
    assert swath.counters.records == 2
    assert swath.ctds == ()
    assert len(swath.headings) == 1


def test_load_swath_counts_skipped_bytes():
    good = drf(1013, heading_payload())
    swath = load_swath(b"\x99" * 11 + good + b"\x77" * 5)
    assert swath.counters.records == 1
    assert swath.counters.bytes_skipped == 16


def test_load_swath_never_raises_on_garbage():
    swath = load_swath(b"\x00\x01" * 40)
    assert swath.pings == ()
    assert swath.counters.records == 0
    assert swath.counters.bytes_skipped == 80


# ---------------------------------------------------------------------------
# real sample validation (NOAA NCEI multibeam archive)
# ---------------------------------------------------------------------------

_SAMPLE = os.environ.get("S7K_SAMPLE", "")


@pytest.mark.skipif(not (_SAMPLE and os.path.exists(_SAMPLE)),
                    reason="S7K_SAMPLE not set or file missing")
def test_real_sample_statistics():
    """Statistics measured 2026-08-31 from NCEI file
    20170522_181322.s7k (survey SP1701, R/V Scott Petty, Reson SeaBat
    T50-P, Galveston Bay approaches, 2017-05-22; 425,864,523 bytes
    decompressed; source URL in docs/FORMAT-SOURCES.md anchor S12).

    Every byte of the file frames: zero gaps, zero malformed records,
    120,287 records. The line is natively logged by the 7k sonar
    source: 7,957 pings, each carrying sonar settings (7000), beam
    geometry (7004), raw detections (7027), snippets (7028) and a
    remote settings record (7503), with 1003/1012/1013 navigation and
    motion at sensor rate and a 7610 surface sound velocity series.
    All 15,857 checksum mismatches sit on 7610 records (a writer
    quirk of this vintage; see the anchor errata in FORMAT-SOURCES),
    every other record's byte sum verifies.
    """
    swath = load_swath(_SAMPLE)
    counters = swath.counters
    assert counters.records == 120_287
    assert counters.bytes_skipped == 0
    assert counters.checksum_failures == 15_857
    assert counters.unknown_record_types == (
        (7001, 1), (7002, 7957), (7010, 7957), (7021, 855),
        (7022, 1), (7200, 1), (7300, 1), (7504, 1))

    # Record census of the decoded series.
    assert len(swath.pings) == 7957
    assert len(swath.positions) == 15_937
    assert len(swath.roll_pitch_heaves) == 15_937
    assert len(swath.headings) == 15_937
    assert len(swath.sound_velocities) == 15_917
    assert len(swath.beam_geometries) == 7957
    assert len(swath.remote_settings) == 7957
    assert swath.ctds == () and swath.geodesies == ()
    assert swath.water_column == ()

    # Ping completeness: every ping has settings, geometry-consistent
    # raw detections and snippets.
    assert all(p.settings is not None for p in swath.pings)
    assert all(p.raw_detections is not None for p in swath.pings)
    assert all(p.snippets is not None for p in swath.pings)
    assert swath.pings[0].ping_number == 865
    assert swath.pings[-1].ping_number == 8821
    assert {p.device_identifier for p in swath.pings} == {50}  # a T50
    first = swath.pings[0].raw_detections.time
    last = swath.pings[-1].raw_detections.time
    assert first == pytest.approx(1_495_476_802.596, abs=0.001)  # 18:13:22.6
    assert (last - first) / 60.0 == pytest.approx(26.52, abs=0.01)

    # Beams: a 512-beam sonar; detections per ping bounded by it, with
    # the vintage-26 detection block (uncertainty and intensity, no
    # gate limits).
    assert {g.num_beams for g in swath.beam_geometries} == {512}
    detections = [p.raw_detections for p in swath.pings]
    assert max(d.num_detections for d in detections) == 512
    assert sum(d.num_detections for d in detections) == 3_986_864
    assert {d.detection_size for d in detections} == {26}
    assert all(d.intensities is not None for d in detections)
    assert all(d.min_limits is None for d in detections)

    # Snippet presence: real intensity samples around the detections.
    with_snippets = [p.snippets for p in swath.pings if p.snippets.snippets]
    assert len(with_snippets) == 7957
    assert {s.error_flag for s in with_snippets} == {0}
    assert sum(len(s) for snip in with_snippets[:100]
               for s in snip.snippets) == 480_331
    assert not any(s.is_32_bit for s in with_snippets)

    # Navigation bounds: the line sits in the Galveston Bay approaches.
    latitudes = [p.latitude_degrees for p in swath.positions]
    longitudes = [p.longitude_degrees for p in swath.positions]
    assert min(latitudes) == pytest.approx(29.309839, abs=0.000001)
    assert max(latitudes) == pytest.approx(29.327023, abs=0.000001)
    assert min(longitudes) == pytest.approx(-94.815628, abs=0.000001)
    assert max(longitudes) == pytest.approx(-94.777783, abs=0.000001)

    # Depth sanity: reduce the raw observables of the center beam
    # (two-way time, receive angle, applied sound velocity) to a
    # vertical distance; a shipping channel a dozen meters deep.
    depths = []
    for ping in swath.pings:
        det = ping.raw_detections
        sound_velocity = ping.settings.sound_velocity_mps
        index = min(range(det.num_detections),
                    key=lambda i: abs(det.rx_angles_rad[i]))
        depths.append(det.two_way_travel_times_sec[index] / 2.0
                      * sound_velocity * math.cos(det.rx_angles_rad[index]))
    assert min(depths) == pytest.approx(8.633, abs=0.005)
    assert max(depths) == pytest.approx(15.708, abs=0.005)

    # The 7610 checksum quirk: every failure is a 7610 frame, and the
    # 7610 values themselves are a coherent surface sound velocity
    # series. The temperature and pressure words are present (12-byte
    # payload) but zero, which the DFD reads as not valid.
    velocities = [v.sound_velocity_mps for v in swath.sound_velocities]
    assert min(velocities) == pytest.approx(1522.25, abs=0.001)
    assert max(velocities) == pytest.approx(1525.563, abs=0.001)
    assert {v.temperature_k for v in swath.sound_velocities} == {0.0}
    assert {v.pressure_pa for v in swath.sound_velocities} == {0.0}

    failed_types = {frame.record_type
                    for frame in iter_records(_SAMPLE)
                    if isinstance(frame, S7kFrame) and not frame.checksum_ok}
    assert failed_types == {7610}

    # Nothing in the file is malformed.
    assert not [r for r in read_s7k(_SAMPLE)
                if isinstance(r, MalformedRecord)]
