"""Cerulean SVLog dialect: frame scanning, record decoding, survey loading.

Fixtures are synthetic bytes assembled in-test from the public Cerulean
Ping Protocol packet definitions (see hydroformats/svlog.py for citations);
all values are fictional. The real-sample integration test at the bottom
runs only when SVLOG_SAMPLE points at the vendor's published 737-reef
survey (docs.ceruleansonar.com, Surveyor 240-16 sample data page).
"""
import gzip
import json
import math
import os
import struct

import pytest

from hydroformats.records import MalformedRecord
from hydroformats.svlog import (
    AtofPointData,
    AttitudeReport,
    DeviceInformation,
    MavlinkWrapper,
    NmeaWrapper,
    PingParameters,
    SvlogFrame,
    SvlogGap,
    WaterStats,
    YzPointData,
    atof_to_yz,
    iter_frames,
    load_survey,
    read_svlog,
)

# ---------------------------------------------------------------------------
# byte builders (assembled by hand from the public ICD, not via the parser)
# ---------------------------------------------------------------------------


def frame(packet_id: int, payload: bytes, src: int = 0, dst: int = 0) -> bytes:
    """One framed packet: 'B','R', u16 length, u16 id, u8 src, u8 dst,
    payload, u16 checksum (16-bit truncated sum of all preceding bytes)."""
    body = b"BR" + struct.pack("<HHBB", len(payload), packet_id, src, dst) + payload
    return body + struct.pack("<H", sum(body) & 0xFFFF)


def atof_payload(
    pwr_up_msec: int = 731_000,
    utc_msec: int = 1_772_000_450_123,
    listening_sec: float = 0.25,
    sos_mps: float = 1480.0,
    ping_number: int = 512,
    ping_hz: int = 240_000,
    pulse_sec: float = 0.0001220703125,
    flags: int = 0,
    points: tuple[tuple[float, float], ...] = ((0.5, 0.02), (-0.25, 0.03)),
) -> bytes:
    head = struct.pack(
        "<IQ2f2IfI2H",
        pwr_up_msec, utc_msec, listening_sec, sos_mps,
        ping_number, ping_hz, pulse_sec, flags, len(points), 0,
    )
    body = b"".join(struct.pack("<2f2I", a, t, 0, 0) for a, t in points)
    return head + body


def yz_payload(
    timestamp_msec: int = 731_000,
    ping_number: int = 512,
    sos_mps: float = 1480.0,
    up_vec: tuple[float, float, float] = (0.0, 0.0, 1.0),
    points: tuple[tuple[float, float], ...] = ((7.5, -12.0), (-3.25, -11.5)),
) -> bytes:
    head = struct.pack(
        "<2If3f3f10I5f2H",
        timestamp_msec, ping_number, sos_mps,
        *up_vec, 0.0, 0.0, 0.0, *((0,) * 10),
        21.5, 2.25, 0.0, 2.0, 40.0,
        0, len(points),
    )
    return head + b"".join(struct.pack("<2f", y, z) for y, z in points)


def attitude_payload(
    up_vec: tuple[float, float, float] = (0.1, 0.05, 0.99),
    utc_msec: int = 1_772_000_450_500,
    pwr_up_msec: int = 731_500,
) -> bytes:
    return struct.pack("<3f3fQI", *up_vec, 0.0, 0.0, 0.0, utc_msec, pwr_up_msec)


def water_payload(temperature: float = 12.5, pressure: float = 1.25) -> bytes:
    return struct.pack("<2f", temperature, pressure)


def device_info_payload() -> bytes:
    return struct.pack("<6B", 14, 2, 1, 4, 7, 0)


def params_payload() -> bytes:
    return struct.pack(
        "<2if2hH6Bi2Hf",
        500, -1, 1480.0, -1, 100, 0,
        0, 1, 0, 0, 0, 1,
        240_000, 400, 0, 1.5,
    )


NMEA_TEXT = "$GPGGA,120000.00,4436.0000,N,06333.0000,W,1,10,0.9,5.0,M,0.0,M,,*47"
MAVLINK_TEXT = '{"message":{"type":"GLOBAL_POSITION_INT","lat":446000000},"header":{}}'


def stream(*frames: bytes) -> bytes:
    return b"".join(frames)


# ---------------------------------------------------------------------------
# frame scanning
# ---------------------------------------------------------------------------


def test_frame_checksum_is_truncated_sum_of_preceding_bytes():
    built = frame(118, water_payload())
    (stored,) = struct.unpack_from("<H", built, len(built) - 2)
    assert stored == sum(built[:-2]) & 0xFFFF


def test_scanner_walks_consecutive_frames():
    data = stream(frame(118, water_payload()), frame(4, device_info_payload()))
    events = list(iter_frames(data))
    assert [e.packet_id for e in events] == [118, 4]
    assert all(isinstance(e, SvlogFrame) for e in events)
    assert events[0].offset == 0
    assert events[1].offset == len(frame(118, water_payload()))
    assert events[0].payload == water_payload()


def test_scanner_skips_leading_garbage():
    data = b"\x00\x07garbage" + frame(118, water_payload())
    events = list(iter_frames(data))
    assert isinstance(events[0], SvlogGap)
    assert (events[0].offset, events[0].size) == (0, 9)
    assert events[0].checksum_failures == 0
    assert isinstance(events[1], SvlogFrame)
    assert events[1].packet_id == 118


def test_scanner_resyncs_after_checksum_corruption():
    good_a = frame(118, water_payload())
    good_b = frame(4, device_info_payload())
    corrupted = bytearray(frame(118, water_payload()))
    corrupted[8] ^= 0xFF  # first payload byte no longer matches the checksum
    data = stream(good_a, bytes(corrupted), good_b)
    events = list(iter_frames(data))
    frames = [e for e in events if isinstance(e, SvlogFrame)]
    gaps = [e for e in events if isinstance(e, SvlogGap)]
    assert [f.packet_id for f in frames] == [118, 4]
    assert len(gaps) == 1
    assert gaps[0].size == len(corrupted)
    assert gaps[0].checksum_failures == 1


def test_scanner_counts_truncated_final_packet_as_skipped_bytes():
    good = frame(118, water_payload())
    truncated = frame(118, water_payload())[:-5]
    events = list(iter_frames(stream(good, truncated)))
    frames = [e for e in events if isinstance(e, SvlogFrame)]
    gaps = [e for e in events if isinstance(e, SvlogGap)]
    assert len(frames) == 1
    assert len(gaps) == 1
    assert gaps[0].size == len(truncated)
    assert gaps[0].checksum_failures == 0


def test_scanner_tolerates_sync_bytes_inside_payload():
    payload = b"BR" + bytes(range(14))
    data = frame(9999, payload)
    events = list(iter_frames(data))
    assert len(events) == 1
    assert events[0].payload == payload


def test_scanner_rejects_non_svlog_bytes():
    assert list(iter_frames(b"")) == []
    events = list(iter_frames(b"not a log"))
    assert all(isinstance(e, SvlogGap) for e in events)


# ---------------------------------------------------------------------------
# record decoding (round-trip: build bytes, parse, compare)
# ---------------------------------------------------------------------------


def _records(data: bytes):
    return list(read_svlog(data))


def test_atof_roundtrip():
    records = _records(frame(3012, atof_payload()))
    ping = records[0]
    assert isinstance(ping, AtofPointData)
    assert ping.pwr_up_msec == 731_000
    assert ping.utc_msec == 1_772_000_450_123
    assert ping.listening_sec == pytest.approx(0.25)
    assert ping.sos_mps == pytest.approx(1480.0)
    assert ping.ping_number == 512
    assert ping.ping_hz == 240_000
    assert ping.pulse_sec == pytest.approx(0.0001220703125)
    assert ping.flags == 0
    assert ping.num_points == 2
    assert ping.angles == pytest.approx((0.5, -0.25))
    assert ping.tofs == pytest.approx((0.02, 0.03))


def test_atof_short_payload_is_malformed_not_fatal():
    payload = atof_payload()[:-10]  # truncated inside the last point
    records = _records(frame(3012, payload))
    assert len(records) == 1
    assert isinstance(records[0], MalformedRecord)
    assert records[0].tag == "ATOF"


def test_yz_short_payload_is_malformed_not_fatal():
    payload = yz_payload()[:-4]  # truncated inside the last pair
    records = _records(frame(3011, payload))
    assert len(records) == 1
    assert isinstance(records[0], MalformedRecord)
    assert records[0].tag == "YZ"


def test_yz_roundtrip():
    records = _records(frame(3011, yz_payload()))
    ping = records[0]
    assert isinstance(ping, YzPointData)
    assert ping.timestamp_msec == 731_000
    assert ping.ping_number == 512
    assert ping.sos_mps == pytest.approx(1480.0)
    assert ping.up_vec == pytest.approx((0.0, 0.0, 1.0))
    assert ping.water_degc == pytest.approx(21.5)
    assert ping.water_bar == pytest.approx(2.25)
    assert ping.start_m == pytest.approx(2.0)
    assert ping.end_m == pytest.approx(40.0)
    assert ping.num_points == 2
    assert ping.ys == pytest.approx((7.5, -3.25))
    assert ping.zs == pytest.approx((-12.0, -11.5))


def test_attitude_pitch_roll_from_up_vector():
    records = _records(frame(504, attitude_payload()))
    att = records[0]
    assert isinstance(att, AttitudeReport)
    assert att.utc_msec == 1_772_000_450_500
    assert att.pwr_up_msec == 731_500
    # pitch = asin(x), roll = atan2(y, z); hand-computed for (0.1, 0.05, 0.99)
    assert att.pitch_radians == pytest.approx(math.asin(0.1), rel=1e-6)
    assert att.roll_radians == pytest.approx(math.atan2(0.05, 0.99), rel=1e-6)
    assert att.pitch_degrees == pytest.approx(5.739170, rel=1e-5)
    assert att.roll_degrees == pytest.approx(2.891270, rel=1e-5)


def test_attitude_pitch_clamps_out_of_range_up_x():
    records = _records(frame(504, attitude_payload(up_vec=(1.0000001, 0.0, 0.0))))
    assert records[0].pitch_radians == pytest.approx(math.pi / 2)


def test_water_stats_roundtrip():
    records = _records(frame(118, water_payload()))
    water = records[0]
    assert isinstance(water, WaterStats)
    assert water.temperature_degc == pytest.approx(12.5)
    assert water.pressure_bar == pytest.approx(1.25)


def test_device_information_roundtrip():
    records = _records(frame(4, device_info_payload()))
    info = records[0]
    assert isinstance(info, DeviceInformation)
    assert info.device_type == 14
    assert info.device_revision == 2
    assert info.firmware_version == "1.4.7"


def test_ping_parameters_roundtrip():
    records = _records(frame(3023, params_payload()))
    params = records[0]
    assert isinstance(params, PingParameters)
    assert params.start_mm == 500
    assert params.end_mm == -1
    assert params.sos_mps == pytest.approx(1480.0)
    assert params.gain_index == -1
    assert params.msec_per_ping == 100
    assert params.ping_enable is True
    assert params.enable_channel_data is False
    assert params.enable_atof_data is True
    assert params.target_ping_hz == 240_000
    assert params.n_range_steps == 400
    assert params.pulse_len_steps == pytest.approx(1.5)


def test_nmea_wrapper_text_extraction():
    payload = (NMEA_TEXT + "\r\n").encode("ascii")
    records = _records(frame(109, payload))
    nmea = records[0]
    assert isinstance(nmea, NmeaWrapper)
    assert nmea.text == NMEA_TEXT


def test_mavlink_wrapper_json_text_extraction():
    records = _records(frame(150, MAVLINK_TEXT.encode("ascii") + b"\x00"))
    mav = records[0]
    assert isinstance(mav, MavlinkWrapper)
    assert mav.text == MAVLINK_TEXT


def test_unknown_packet_id_is_skipped_by_read_svlog():
    data = stream(frame(118, water_payload()), frame(9999, b"\x01\x02\x03\x04"))
    records = _records(data)
    assert len(records) == 1
    assert isinstance(records[0], WaterStats)


def test_read_svlog_yields_records_in_file_order():
    data = stream(
        frame(4, device_info_payload()),
        frame(3012, atof_payload()),
        frame(109, NMEA_TEXT.encode("ascii")),
        frame(3012, atof_payload(ping_number=513)),
    )
    kinds = [type(r).__name__ for r in _records(data)]
    assert kinds == ["DeviceInformation", "AtofPointData", "NmeaWrapper", "AtofPointData"]


def test_docstring_layout_sizes():
    """The fixed-part byte counts quoted in the record docstrings."""
    assert len(atof_payload(points=())) == 40
    assert len(atof_payload(points=((0.5, 0.02),))) == 40 + 16
    assert len(yz_payload(points=())) == 100
    assert len(attitude_payload()) == 36
    assert len(water_payload()) == 8
    assert len(device_info_payload()) == 6
    assert len(params_payload()) == 36


# ---------------------------------------------------------------------------
# gzip handling (.svlz)
# ---------------------------------------------------------------------------


def test_gzip_variant_parses_identically(tmp_path):
    raw = stream(frame(118, water_payload()), frame(3012, atof_payload()))
    path = tmp_path / "line.svlz"
    path.write_bytes(gzip.compress(raw))
    assert [type(r).__name__ for r in read_svlog(path)] == ["WaterStats", "AtofPointData"]


def test_concatenated_gzip_members_parse_as_one_stream():
    part_a = gzip.compress(frame(118, water_payload()))
    part_b = gzip.compress(frame(4, device_info_payload()))
    records = _records(part_a + part_b)
    assert [type(r).__name__ for r in records] == ["WaterStats", "DeviceInformation"]


def test_truncated_gzip_yields_leading_records():
    raw = stream(frame(118, water_payload()), frame(3012, atof_payload()))
    cut = gzip.compress(raw)[:-20]
    records = _records(cut)
    assert isinstance(records[0], WaterStats)


def test_corrupt_gzip_body_never_raises():
    raw = stream(frame(118, water_payload()), frame(3012, atof_payload()))
    corrupt = bytearray(gzip.compress(raw))
    corrupt[-15] ^= 0xFF  # damage the deflate stream near its end
    records = _records(bytes(corrupt))
    # whatever survives the damage decodes cleanly, starting at the front
    assert all(not isinstance(r, MalformedRecord) for r in records)
    if records:
        assert isinstance(records[0], WaterStats)


# ---------------------------------------------------------------------------
# ATOF geometry helper
# ---------------------------------------------------------------------------


def test_atof_to_yz_hand_computed():
    # distance = 0.5 * 1480 * 0.02 = 14.8 m (two-way time of flight)
    y, z = atof_to_yz(0.0, 0.02, 1480.0)
    assert y == pytest.approx(0.0)
    assert z == pytest.approx(-14.8)
    # 30 degrees to port: y = 14.8 * sin = +7.4, z = -14.8 * cos = -12.8171760
    y, z = atof_to_yz(math.pi / 6, 0.02, 1480.0)
    assert y == pytest.approx(7.4)
    assert z == pytest.approx(-12.817175976009692)


def test_atof_yz_points_method_uses_ping_sos():
    ping = _records(frame(3012, atof_payload(points=((math.pi / 6, 0.02),))))[0]
    ((y, z),) = ping.yz_points()
    assert y == pytest.approx(7.4, rel=1e-6)
    assert z == pytest.approx(-12.8171760, rel=1e-6)


# ---------------------------------------------------------------------------
# load_survey
# ---------------------------------------------------------------------------


def _survey_stream() -> bytes:
    return stream(
        frame(4, device_info_payload()),
        frame(3023, params_payload()),
        frame(109, (NMEA_TEXT + "\r\n").encode("ascii")),
        frame(3012, atof_payload(ping_number=512)),
        frame(504, attitude_payload()),
        frame(150, MAVLINK_TEXT.encode("ascii")),
        frame(3012, atof_payload(ping_number=513)),
        frame(118, water_payload()),
        frame(9999, b"\xaa\xbb"),
    )


def test_load_survey_bundles_series_and_counters():
    survey = load_survey(_survey_stream())
    assert [p.ping_number for p in survey.pings] == [512, 513]
    assert len(survey.attitude) == 1
    assert len(survey.water_stats) == 1
    assert survey.device_info is not None
    assert survey.device_info.firmware_version == "1.4.7"
    assert survey.counters.packets == 9
    assert survey.counters.checksum_failures == 0
    assert survey.counters.unknown_ids == 1
    assert survey.counters.bytes_skipped == 0


def test_load_survey_nav_offsets_are_file_positions():
    survey = load_survey(_survey_stream())
    offsets = [offset for offset, _ in survey.nav]
    records = [record for _, record in survey.nav]
    assert [type(r).__name__ for r in records] == ["NmeaWrapper", "MavlinkWrapper"]
    expected_nmea = len(frame(4, device_info_payload())) + len(frame(3023, params_payload()))
    assert offsets[0] == expected_nmea
    assert offsets == sorted(offsets)


def test_load_survey_counts_corruption_and_truncation():
    corrupted = bytearray(frame(118, water_payload()))
    corrupted[8] ^= 0xFF
    data = stream(
        frame(3012, atof_payload()),
        bytes(corrupted),
        frame(118, water_payload())[:-3],
    )
    survey = load_survey(data)
    assert survey.counters.packets == 1
    assert survey.counters.checksum_failures == 1
    assert survey.counters.bytes_skipped == len(corrupted) + len(frame(118, water_payload())) - 3


# ---------------------------------------------------------------------------
# real sample validation (vendor's published 737-reef survey)
# ---------------------------------------------------------------------------

_SAMPLE = os.environ.get("SVLOG_SAMPLE", "")


@pytest.mark.skipif(not (_SAMPLE and os.path.exists(_SAMPLE)),
                    reason="SVLOG_SAMPLE not set or file missing")
def test_real_sample_statistics():
    """Statistics measured 2026-08-28 from the vendor's published 737-reef
    sample .svlz (177,446,210 bytes gzipped).

    The sample predates the current ATOF-era API: it carries no
    ATOF_POINT_DATA / YZ_POINT_DATA / ATTITUDE_REPORT / WATER_STATS /
    DEVICE_INFORMATION packets at all. Its 121,832 frames are 87,882 of
    id 3009 (3,220-byte constant payloads, channel data, no public
    layout), 10,986 of id 3010 (END_PING_INFO per the vendor's packet
    index, layout unpublished, so the sample's ping count is 10,986),
    22,962 MAVLINK_WRAPPER frames (nav comes as mavlink2rest JSON:
    ATTITUDE 12,062, GLOBAL_POSITION_INT 3,623, LOCAL_POSITION_NED
    3,623, HEARTBEAT 2,448, HOME_POSITION 1,206), and one frame each of
    ids 10 and 12. Every frame checksum verifies and the stream has no
    gaps, which anchors the framing, checksum, and gzip layers against
    real vendor output end to end.
    """
    survey = load_survey(_SAMPLE)
    counters = survey.counters
    assert counters.packets == 121_832
    assert counters.checksum_failures == 0
    assert counters.bytes_skipped == 0
    assert counters.unknown_ids == 98_870  # ids 10, 12, 3009, 3010
    assert survey.pings == ()
    assert survey.attitude == ()
    assert survey.water_stats == ()
    assert survey.device_info is None
    assert len(survey.nav) == 22_962
    assert {type(r).__name__ for _, r in survey.nav} == {"MavlinkWrapper"}
    offsets = [offset for offset, _ in survey.nav]
    assert offsets == sorted(offsets)
    first = json.loads(survey.nav[0][1].text)
    assert "message" in first
