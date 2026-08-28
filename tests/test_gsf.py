"""Generic Sensor Format dialect: record walking, decoding, swath loading.

Fixtures are synthetic bytes assembled in-test from the Leidos GSF
specification tables (see hydroformats/gsf.py for the citation); all
values are fictional. The real-sample integration test at the bottom runs
only when GSF_SAMPLE points at a real GSF file (NOAA NCEI's multibeam
archive publishes them; see docs/FORMAT-SOURCES.md anchor S7).
"""
import os
import struct

import pytest

from hydroformats.gsf import (
    GsfAttitude,
    GsfComment,
    GsfFrame,
    GsfGap,
    GsfHeader,
    GsfHistory,
    GsfPing,
    GsfProcessingParameters,
    GsfSummary,
    GsfSvp,
    beam_usable,
    iter_records,
    load_swath,
    read_gsf,
)
from hydroformats.records import MalformedRecord

# ---------------------------------------------------------------------------
# byte builders (assembled by hand from the spec tables, not via the parser)
# ---------------------------------------------------------------------------


def record(record_id: int, payload: bytes, checksum: bool = False) -> bytes:
    """One framed record: u32 size of the data portion, u32 identifier
    (bit 31 checksum flag, bits 21-12 registry, bits 11-0 data type),
    optional u32 checksum, then the data padded to a multiple of four.
    All fields big endian per spec section 3.6.2."""
    padded = payload + b"\x00" * (-len(payload) % 4)
    identifier = record_id | (0x80000000 if checksum else 0)
    head = struct.pack(">II", len(padded), identifier)
    if checksum:
        head += struct.pack(">I", sum(padded) & 0xFFFFFFFF)
    return head + padded


def subrecord(sub_id: int, payload: bytes) -> bytes:
    """One ping subrecord: u8 identifier, u24 size, then the payload."""
    return bytes([sub_id]) + len(payload).to_bytes(3, "big") + payload


def scale_entry(sub_id: int, multiplier: int, offset: int,
                compression: int = 0) -> bytes:
    """One 12-byte scale-factor array element: id byte, compression byte,
    u16 reserved, u32 multiplier, i32 offset."""
    return struct.pack(">BBHIi", sub_id, compression, 0, multiplier, offset)


def scale_factors(*entries: bytes) -> bytes:
    return subrecord(100, struct.pack(">i", len(entries)) + b"".join(entries))


def beam_array(sub_id: int, values: tuple[int, ...], width: int = 2,
               signed: bool | None = None) -> bytes:
    if signed is None:
        signed = any(v < 0 for v in values)
    code = {1: "b", 2: "h", 4: "i"}[width]
    fmt = f">{len(values)}{code if signed else code.upper()}"
    return subrecord(sub_id, struct.pack(fmt, *values))


def header_record(version: str = "GSF-v03.09") -> bytes:
    return record(1, version.encode("ascii").ljust(12, b"\x00"))


def ping_header(
    time_sec: int = 1_772_000_450,
    time_nsec: int = 250_000_000,
    longitude_e7: int = -635_123_456,
    latitude_e7: int = 446_012_345,
    num_beams: int = 4,
    center_beam: int = 2,
    ping_flags: int = 0,
    tide_cm: int = -35,
    depth_corrector_cm: int = 120,
    heading_cdeg: int = 9000,
    pitch_cdeg: int = -150,
    roll_cdeg: int = 275,
    heave_cm: int = -8,
    course_cdeg: int = 9100,
    speed_chots: int = 450,
    height_mm: int = -21_500,
    separation_mm: int = -22_000,
    gps_tide_mm: int = -350,
) -> bytes:
    """The 56-byte fixed ping header of GSF v03.01+ (Table 4-3)."""
    return struct.pack(
        ">4i2hHhhiH3h2H3ih",
        time_sec, time_nsec, longitude_e7, latitude_e7,
        num_beams, center_beam, ping_flags, 0,
        tide_cm, depth_corrector_cm,
        heading_cdeg, pitch_cdeg, roll_cdeg, heave_cm,
        course_cdeg, speed_chots,
        height_mm, separation_mm, gps_tide_mm, 0,
    )


DEFAULT_SCALES = (
    scale_entry(1, 100, 0),       # depth: meters * 100
    scale_entry(2, 100, 0),       # across track: meters * 100
    scale_entry(3, 100, 0),       # along track: meters * 100
    scale_entry(4, 10_000, 0),    # travel time: seconds * 10000
    scale_entry(5, 100, 180),     # beam angle: (degrees + 180) * 100
)


def ping_record(header: bytes | None = None, *subrecords: bytes,
                with_scales: bool = True) -> bytes:
    parts = [header if header is not None else ping_header()]
    if with_scales:
        parts.append(scale_factors(*DEFAULT_SCALES))
    if subrecords:
        parts.extend(subrecords)
    else:
        parts.extend(default_arrays())
    return record(2, b"".join(parts))


def default_arrays() -> tuple[bytes, ...]:
    """Four-beam arrays matching DEFAULT_SCALES; values fictional."""
    return (
        beam_array(1, (1234, 2345, 3456, 60_000), signed=False),
        beam_array(2, (-4500, -1500, 1500, 4500)),
        beam_array(3, (120, 80, 80, 120), signed=True),
        beam_array(4, (1200, 1250, 1250, 1300), signed=False),
        beam_array(5, (12_000, 16_000, 20_000, 24_000), signed=True),
        beam_array(16, (0, 1, 9, 6), width=1, signed=False),
    )


def svp_payload(
    points: tuple[tuple[int, int], ...] = ((150, 148_050), (1500, 147_820)),
) -> bytes:
    head = struct.pack(
        ">7i",
        1_772_000_100, 0, 1_772_000_400, 500_000_000,
        -635_120_000, 446_010_000, len(points),
    )
    return head + b"".join(struct.pack(">2i", d, v) for d, v in points)


def attitude_payload(
    offsets_ms: tuple[int, ...] = (0, 100, 200),
    pitch_cdeg: tuple[int, ...] = (-120, -95, -70),
    roll_cdeg: tuple[int, ...] = (250, 240, 230),
    heave_cm: tuple[int, ...] = (-5, -3, 0),
    heading_cdeg: tuple[int, ...] = (8995, 9000, 9005),
) -> bytes:
    """Measurements interleave as (time, pitch, roll, heave, heading)
    groups; proven on real data, see the anchor errata for S7."""
    rows = zip(offsets_ms, pitch_cdeg, roll_cdeg, heave_cm, heading_cdeg,
               strict=True)
    return struct.pack(">2ih", 1_772_000_450, 0, len(offsets_ms)) + b"".join(
        struct.pack(">4hH", *row) for row in rows
    )


def comment_payload(text: str = "line 42 start, weather calm") -> bytes:
    body = text.encode("ascii")
    return struct.pack(">3i", 1_772_000_460, 0, len(body)) + body


def history_payload() -> bytes:
    def block(text: str) -> bytes:
        body = text.encode("ascii")
        return struct.pack(">h", len(body)) + body
    return (
        struct.pack(">2i", 1_772_000_470, 0)
        + block("surveypc-01") + block("j.doe")
        + block("swathproc --filter median") + block("median filter pass")
    )


def params_payload(texts: tuple[str, ...] = (
    "REFERENCE TIME=1970/001 00:00:00",
    "ROLL_COMPENSATED=YES",
)) -> bytes:
    body = struct.pack(">2ih", 1_772_000_050, 0, len(texts))
    for text in texts:
        encoded = text.encode("ascii")
        body += struct.pack(">h", len(encoded)) + encoded
    return body


def summary_payload() -> bytes:
    return struct.pack(
        ">10i",
        1_772_000_450, 0, 1_772_003_450, 0,
        446_010_000, -635_130_000, 446_020_000, -635_110_000,
        1230, 60_150,
    )


def stream(*records: bytes) -> bytes:
    return b"".join(records)


# ---------------------------------------------------------------------------
# record walking
# ---------------------------------------------------------------------------


def test_record_header_bytes_are_big_endian_size_then_identifier():
    built = header_record()
    assert built[:8] == struct.pack(">II", 12, 1)
    assert built[:8] == bytes((0, 0, 0, 12, 0, 0, 0, 1))


def test_walker_yields_records_in_file_order():
    data = stream(header_record(), record(6, comment_payload()))
    frames = list(iter_records(data))
    assert [f.data_type for f in frames] == [1, 6]
    assert all(isinstance(f, GsfFrame) for f in frames)
    assert frames[0].offset == 0
    assert frames[1].offset == len(header_record())
    assert frames[0].payload == b"GSF-v03.09\x00\x00"


def test_walker_pins_registry_and_data_type_split():
    # registry bits 21-12, data type bits 11-0 (spec section 4.3.1.2)
    private = record((5 << 12) | 3, b"\x01\x02\x03\x04")
    (frame,) = iter_records(private)
    assert frame.registry == 5
    assert frame.data_type == 3


def test_walker_reads_optional_checksum():
    data = record(6, comment_payload(), checksum=True)
    (frame,) = iter_records(data)
    assert frame.checksum == sum(frame.payload) & 0xFFFFFFFF
    assert frame.checksum_ok is True


def test_walker_flags_checksum_mismatch_without_raising():
    data = bytearray(record(6, comment_payload(), checksum=True))
    data[-1] ^= 0xFF
    (frame,) = iter_records(bytes(data))
    assert frame.checksum_ok is False


def test_walker_degrades_on_truncated_final_record():
    good = record(6, comment_payload())
    cut = record(6, comment_payload())[:-7]
    events = list(iter_records(stream(good, cut)))
    assert isinstance(events[0], GsfFrame)
    assert isinstance(events[1], GsfGap)
    assert events[1].offset == len(good)
    assert events[1].size == len(cut)


def test_walker_rejects_non_gsf_bytes():
    assert list(iter_records(b"")) == []
    events = list(iter_records(b"not a gsf file"))
    assert all(isinstance(e, GsfGap) for e in events)


def test_walker_treats_insane_declared_size_as_gap():
    data = struct.pack(">II", 0x7FFFFFFF, 2) + b"\x00" * 16
    events = list(iter_records(data))
    assert all(isinstance(e, GsfGap) for e in events)


# ---------------------------------------------------------------------------
# record decoding (round-trip: build bytes, parse, compare)
# ---------------------------------------------------------------------------


def _records(data: bytes):
    return list(read_gsf(data))


def test_header_record_roundtrip():
    (header,) = _records(header_record())
    assert isinstance(header, GsfHeader)
    assert header.version == "GSF-v03.09"
    assert header.version_major == 3
    assert header.version_minor == 9


def test_ping_header_roundtrip():
    records = _records(stream(header_record(), ping_record()))
    ping = records[1]
    assert isinstance(ping, GsfPing)
    assert ping.time_sec == 1_772_000_450
    assert ping.time_nsec == 250_000_000
    assert ping.time == pytest.approx(1_772_000_450.25)
    assert ping.longitude == pytest.approx(-63.5123456)
    assert ping.latitude == pytest.approx(44.6012345)
    assert ping.num_beams == 4
    assert ping.center_beam == 2
    assert ping.ping_flags == 0
    assert ping.usable is True
    assert ping.tide_corrector_m == pytest.approx(-0.35)
    assert ping.depth_corrector_m == pytest.approx(1.20)
    assert ping.heading_degrees == pytest.approx(90.0)
    assert ping.pitch_degrees == pytest.approx(-1.50)
    assert ping.roll_degrees == pytest.approx(2.75)
    assert ping.heave_m == pytest.approx(-0.08)
    assert ping.course_degrees == pytest.approx(91.0)
    assert ping.speed_knots == pytest.approx(4.50)
    assert ping.height_m == pytest.approx(-21.5)
    assert ping.separation_m == pytest.approx(-22.0)
    assert ping.gps_tide_corrector_m == pytest.approx(-0.35)


def test_ping_flag_bit_zero_marks_unusable():
    ping = _records(ping_record(ping_header(ping_flags=0x0801)))[0]
    assert ping.ping_flags == 0x0801
    assert ping.usable is False


def test_scale_factor_application_hand_computed():
    # depth entry: multiplier 100, offset 10; stored 12345 must decode to
    # 12345 / 100 - 10 = 113.45 (spec 4.3.4.2: divide, then subtract)
    data = ping_record(
        ping_header(num_beams=2),
        scale_factors(scale_entry(1, 100, 10)),
        beam_array(1, (12_345, 23_456), signed=False),
        with_scales=False,
    )
    (ping,) = _records(data)
    assert ping.depths == pytest.approx((113.45, 224.56))
    assert [s.subrecord_id for s in ping.scale_factors] == [1]
    assert ping.scale_factors[0].multiplier == 100
    assert ping.scale_factors[0].offset == 10


def test_beam_arrays_decode_through_default_scales():
    (ping,) = _records(ping_record())
    assert ping.depths == pytest.approx((12.34, 23.45, 34.56, 600.0))
    assert ping.across_track == pytest.approx((-45.0, -15.0, 15.0, 45.0))
    assert ping.along_track == pytest.approx((1.2, 0.8, 0.8, 1.2))
    assert ping.travel_times == pytest.approx((0.12, 0.125, 0.125, 0.13))
    assert ping.beam_angles == pytest.approx((-60.0, -20.0, 20.0, 60.0))
    assert ping.beam_flags == (0, 1, 9, 6)


def test_beam_flags_bit_zero_marks_unusable():
    (ping,) = _records(ping_record())
    assert [beam_usable(flag) for flag in ping.beam_flags] == \
        [True, False, False, True]
    # 9 = 0b00001001: ignore, filter edited; 6 = 0b00000110: selected
    assert ping.beam_flags[2] & 0x01
    assert ping.beam_flags[3] & 0x02


def test_scale_factors_persist_across_pings():
    # spec 4.3.4: the subrecord appears on the first ping it applies to
    # and stays in force until replaced
    first = ping_record(
        ping_header(num_beams=2),
        scale_factors(scale_entry(1, 100, 0)),
        beam_array(1, (1000, 2000), signed=False),
        with_scales=False,
    )
    second = record(2, ping_header(num_beams=2)
                    + beam_array(1, (3000, 4000), signed=False))
    records = _records(stream(first, second))
    assert records[0].depths == pytest.approx((10.0, 20.0))
    assert records[1].depths == pytest.approx((30.0, 40.0))


def test_four_byte_field_size_derived_from_subrecord_size():
    data = ping_record(
        ping_header(num_beams=2),
        scale_factors(scale_entry(1, 1000, 0)),
        beam_array(1, (1_234_567, 9_876_543), width=4, signed=False),
        with_scales=False,
    )
    (ping,) = _records(data)
    assert ping.depths == pytest.approx((1234.567, 9876.543))


def test_array_without_scale_factor_is_skipped_and_counted():
    data = ping_record(
        ping_header(num_beams=2),
        scale_factors(scale_entry(1, 100, 0)),
        beam_array(1, (1000, 2000), signed=False),
        beam_array(2, (-500, 500)),  # no across-track scale entry
        with_scales=False,
    )
    (ping,) = _records(data)
    assert ping.depths == pytest.approx((10.0, 20.0))
    assert ping.across_track is None
    assert (2, 4) in ping.skipped_subrecords


def test_zero_multiplier_never_raises():
    data = ping_record(
        ping_header(num_beams=2),
        scale_factors(scale_entry(1, 0, 0)),
        beam_array(1, (1000, 2000), signed=False),
        with_scales=False,
    )
    (ping,) = _records(data)
    assert ping.depths is None
    assert (1, 4) in ping.skipped_subrecords


def test_sensor_specific_subrecord_is_skipped_and_identifies_sensor():
    data = ping_record(
        ping_header(num_beams=2),
        scale_factors(scale_entry(1, 100, 0)),
        beam_array(1, (1000, 2000), signed=False),
        subrecord(138, b"\x00" * 32),
        with_scales=False,
    )
    (ping,) = _records(data)
    assert ping.sensor_id == 138
    assert (138, 32) in ping.skipped_subrecords
    assert ping.depths == pytest.approx((10.0, 20.0))


def test_quality_factors_decode_unscaled_when_no_entry_present():
    data = ping_record(
        ping_header(num_beams=2),
        beam_array(9, (7, 12), width=1, signed=False),
        with_scales=False,
    )
    (ping,) = _records(data)
    assert ping.quality_factors == (7, 12)


def test_short_ping_payload_is_malformed_not_fatal():
    data = record(2, ping_header()[:30])
    (rec,) = _records(data)
    assert isinstance(rec, MalformedRecord)
    assert rec.tag == "PING"


def test_svp_roundtrip():
    (svp,) = _records(record(3, svp_payload()))
    assert isinstance(svp, GsfSvp)
    assert svp.observed_sec == 1_772_000_100
    assert svp.applied_sec == 1_772_000_400
    assert svp.applied_nsec == 500_000_000
    assert svp.longitude == pytest.approx(-63.512)
    assert svp.latitude == pytest.approx(44.601)
    assert svp.depths_m == pytest.approx((1.5, 15.0))
    assert svp.sound_speeds_mps == pytest.approx((1480.5, 1478.2))


def test_svp_short_payload_is_malformed_not_fatal():
    (rec,) = _records(record(3, svp_payload()[:-4]))
    assert isinstance(rec, MalformedRecord)
    assert rec.tag == "SVP"


def test_attitude_roundtrip():
    (att,) = _records(record(12, attitude_payload()))
    assert isinstance(att, GsfAttitude)
    assert att.base_sec == 1_772_000_450
    assert att.time_offsets == (0, 100, 200)
    assert att.times == pytest.approx(
        (1_772_000_450.0, 1_772_000_450.1, 1_772_000_450.2))
    assert att.pitch_degrees == pytest.approx((-1.2, -0.95, -0.7))
    assert att.roll_degrees == pytest.approx((2.5, 2.4, 2.3))
    assert att.heave_m == pytest.approx((-0.05, -0.03, 0.0))
    assert att.heading_degrees == pytest.approx((89.95, 90.0, 90.05))


def test_comment_roundtrip():
    (com,) = _records(record(6, comment_payload()))
    assert isinstance(com, GsfComment)
    assert com.time_sec == 1_772_000_460
    assert com.text == "line 42 start, weather calm"


def test_history_roundtrip():
    (hist,) = _records(record(7, history_payload()))
    assert isinstance(hist, GsfHistory)
    assert hist.machine == "surveypc-01"
    assert hist.operator == "j.doe"
    assert hist.command == "swathproc --filter median"
    assert hist.comment == "median filter pass"


def test_processing_parameters_roundtrip():
    (params,) = _records(record(4, params_payload()))
    assert isinstance(params, GsfProcessingParameters)
    assert params.texts == (
        "REFERENCE TIME=1970/001 00:00:00", "ROLL_COMPENSATED=YES")
    assert dict(params.parameters)["ROLL_COMPENSATED"] == "YES"


def test_summary_roundtrip():
    (summary,) = _records(record(9, summary_payload()))
    assert isinstance(summary, GsfSummary)
    assert summary.begin_sec == 1_772_000_450
    assert summary.end_sec == 1_772_003_450
    assert summary.min_latitude == pytest.approx(44.601)
    assert summary.min_longitude == pytest.approx(-63.513)
    assert summary.max_latitude == pytest.approx(44.602)
    assert summary.max_longitude == pytest.approx(-63.511)
    assert summary.min_depth_m == pytest.approx(12.30)
    assert summary.max_depth_m == pytest.approx(601.50)


def test_read_gsf_yields_records_in_file_order():
    data = stream(
        header_record(),
        record(4, params_payload()),
        ping_record(),
        record(6, comment_payload()),
        ping_record(),
    )
    kinds = [type(r).__name__ for r in _records(data)]
    assert kinds == ["GsfHeader", "GsfProcessingParameters", "GsfPing",
                     "GsfComment", "GsfPing"]


def test_unknown_record_id_is_skipped_by_read_gsf():
    data = stream(header_record(), record(42, b"\x01\x02\x03\x04"))
    records = _records(data)
    assert len(records) == 1
    assert isinstance(records[0], GsfHeader)


def test_docstring_layout_sizes():
    """The fixed-part byte counts quoted in the record docstrings."""
    assert len(ping_header()) == 56
    assert len(svp_payload(points=())) == 28
    assert len(summary_payload()) == 40
    assert len(comment_payload(text="")) == 12
    assert len(attitude_payload((), (), (), (), ())) == 10
    assert len(scale_entry(1, 100, 0)) == 12


# ---------------------------------------------------------------------------
# load_swath
# ---------------------------------------------------------------------------


def _swath_stream() -> bytes:
    return stream(
        header_record(),
        record(4, params_payload()),
        record(3, svp_payload()),
        ping_record(),
        record(12, attitude_payload()),
        record(6, comment_payload()),
        ping_record(ping_header(time_sec=1_772_000_451)),
        record(7, history_payload()),
        record(9, summary_payload()),
        record(42, b"\xaa\xbb\xcc\xdd"),
        record((5 << 12) | 3, b"\x01\x02\x03\x04"),
    )


def test_load_swath_bundles_series_and_counters():
    swath = load_swath(_swath_stream())
    assert swath.header is not None
    assert swath.header.version == "GSF-v03.09"
    assert [p.time_sec for p in swath.pings] == [1_772_000_450, 1_772_000_451]
    assert len(swath.svps) == 1
    assert len(swath.attitude) == 1
    assert len(swath.comments) == 1
    assert len(swath.history) == 1
    assert len(swath.processing_parameters) == 1
    assert swath.summary is not None
    assert swath.summary.max_depth_m == pytest.approx(601.50)
    assert swath.counters.records == 11
    assert dict(swath.counters.unknown_record_ids) == {42: 1, (5 << 12) | 3: 1}
    assert swath.counters.bytes_skipped == 0


def test_load_swath_counts_sensor_subrecords_by_id():
    ping = ping_record(
        ping_header(num_beams=2),
        scale_factors(scale_entry(1, 100, 0)),
        beam_array(1, (1000, 2000), signed=False),
        subrecord(138, b"\x00" * 32),
        subrecord(21, b"\x00" * 8),
        with_scales=False,
    )
    swath = load_swath(stream(header_record(), ping, ping))
    assert dict(swath.counters.unknown_subrecord_ids) == {138: 2, 21: 2}


def test_load_swath_counts_truncated_tail_bytes():
    data = stream(header_record(), ping_record(), ping_record()[:-9])
    swath = load_swath(data)
    assert len(swath.pings) == 1
    assert swath.counters.bytes_skipped == len(ping_record()) - 9


def test_load_swath_never_raises_on_garbage():
    swath = load_swath(b"\x00\x01" * 40)
    assert swath.pings == ()
    assert swath.counters.records == 0
    assert swath.counters.bytes_skipped == 80


# ---------------------------------------------------------------------------
# real sample validation (NOAA NCEI multibeam archive)
# ---------------------------------------------------------------------------

_SAMPLE = os.environ.get("GSF_SAMPLE", "")


@pytest.mark.skipif(not (_SAMPLE and os.path.exists(_SAMPLE)),
                    reason="GSF_SAMPLE not set or file missing")
def test_real_sample_statistics():
    """Statistics measured 2026-08-28 from NCEI file ahmba03214.d05
    (survey AHI-03-06, Midway Atoll, Reson SeaBat 8101 on the NOAA launch
    AHI; 13,233,784 bytes decompressed; source URL in
    docs/FORMAT-SOURCES.md anchor S7).

    A GSF-v02.02 file, so this also exercises the 42-byte pre-03.01 ping
    header path. Every byte frames (nothing skipped, nothing malformed);
    the census is 1 header, 516 pings, 1 SVP, 1 processing-parameter,
    1 sensor-parameter (undecoded by design), 1 comment, 7 history,
    1 summary and 51 attitude records. Every ping carries 101 beams, a
    RESON_8101 sensor subrecord (id 122) and an intensity series (id 21),
    both counted, not decoded. The maximum usable depth equals the
    summary record's maximum exactly, which pins the centimeter reading
    of the summary depth extremes; the attitude series decodes to
    monotonic 20 ms offsets (a 50 Hz motion sensor) with headings
    tracking the ping headers, which pins the interleaved layout and the
    millisecond offsets.
    """
    swath = load_swath(_SAMPLE)
    counters = swath.counters
    assert counters.records == 580
    assert counters.bytes_skipped == 0
    assert counters.unknown_record_ids == ((5, 1),)
    assert counters.unknown_subrecord_ids == ((21, 516), (122, 516))
    assert not [r for r in read_gsf(_SAMPLE) if isinstance(r, MalformedRecord)]

    assert swath.header is not None
    assert swath.header.version == "GSF-v02.02"
    assert swath.header.version_major == 2
    assert len(swath.pings) == 516
    assert len(swath.svps) == 1
    assert len(swath.attitude) == 51
    assert len(swath.comments) == 1
    assert len(swath.history) == 7
    assert len(swath.processing_parameters) == 1
    assert swath.summary is not None

    assert {p.num_beams for p in swath.pings} == {101}
    assert {p.sensor_id for p in swath.pings} == {122}
    assert all(p.height_m is None for p in swath.pings)  # pre-03.01 header
    for ping in swath.pings:
        assert swath.summary.min_latitude <= ping.latitude \
            <= swath.summary.max_latitude
        assert swath.summary.min_longitude <= ping.longitude \
            <= swath.summary.max_longitude
        assert ping.travel_times is not None
        assert ping.beam_angles is not None
    usable = [
        depth
        for ping in swath.pings
        for depth, flag in zip(ping.depths, ping.beam_flags, strict=True)
        if beam_usable(flag)
    ]
    assert len(usable) == 41_727
    assert max(usable) == pytest.approx(swath.summary.max_depth_m)
    assert swath.summary.min_depth_m == pytest.approx(65.09)
    assert swath.summary.max_depth_m == pytest.approx(73.36)

    svp = swath.svps[0]
    assert svp.num_points == 158
    assert all(1500.0 < v < 1550.0 for v in svp.sound_speeds_mps)

    first = swath.attitude[0]
    assert first.time_offsets[:3] == (0, 20, 40)
    for att in swath.attitude:
        assert all(b >= a for a, b in zip(att.time_offsets,
                                          att.time_offsets[1:], strict=False))
    assert dict(swath.processing_parameters[0].parameters)[
        "REFERENCE TIME"] == "1970/001 00:00:00"
