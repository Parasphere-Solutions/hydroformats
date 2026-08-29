"""Sound Metrics DDF dialect: frame walking, decoding, imaging loading.

Fixtures are synthetic bytes assembled in-test from the layouts in the
MIT-licensed Sound Metrics aris-file-sdk type definitions (see
hydroformats/aris.py for the citation); all values are fictional. The
real-sample integration tests at the bottom run only when ARIS_SAMPLE
points at the SDK's sample.aris and DDF_SAMPLE_DIR at a directory of raw
DIDSON .ddf clips (see docs/FORMAT-SOURCES.md anchor S8).
"""
import os
import struct

import pytest

from hydroformats.aris import (
    ARIS_SIGNATURE,
    DIDSON_V3_SIGNATURE,
    ArisFrame,
    DdfFileHeader,
    DidsonFrame,
    beam_count_for_ping_mode,
    didson_delay_period,
    load_imaging,
    read_aris,
)
from hydroformats.records import MalformedRecord

# ---------------------------------------------------------------------------
# byte builders (assembled by hand from the SDK offset tables, not via the
# parser; ArisFileHeaderOffsets / ArisFrameHeaderOffsets in the SDK's
# FileHeader.h and FrameHeader.h)
# ---------------------------------------------------------------------------


def put(buffer: bytearray, offset: int, fmt: str, *values) -> None:
    struct.pack_into(fmt, buffer, offset, *values)


def v5_file_header(
    frame_count: int = 2,
    num_raw_beams: int = 48,
    samples_per_channel: int = 6,
    serial_number: int = 4242,
    date_text: bytes = b"2026-08-01",
    header_id: bytes = b"synthetic tank calibration",
) -> bytes:
    head = bytearray(1024)
    put(head, 0, "<I", ARIS_SIGNATURE)
    put(head, 4, "<I", frame_count)
    put(head, 16, "<I", num_raw_beams)
    put(head, 24, "<I", samples_per_channel)
    put(head, 44, "<I", serial_number)
    head[48:48 + len(date_text)] = date_text
    head[80:80 + len(header_id)] = header_id
    put(head, 336, "<4i", 7, -7, 0, 0)          # user ids
    put(head, 352, "<2I", 100, 101)             # start/end frame
    put(head, 396, "<2I", 1, 0)                 # water temp code, salinity code
    return bytes(head)


def v5_frame_header(
    frame_index: int = 0,
    frame_time_us: int = 1_756_000_000_000_000,
    pc_time_us: int = 1_756_000_000_500_000,
    ping_mode: int = 1,
    samples_per_beam: int = 6,
    sample_period_us: int = 10,
    sample_start_delay_us: int = 2000,
    sound_speed_mps: float = 1500.0,
    window_start_m: float = 1.51,
    window_length_m: float = 0.046,
    frequency_hi_low: int = 1,
    receiver_gain: int = 18,
    system_type: int = 2,
    version: int = ARIS_SIGNATURE,
    reordered: int = 1,
) -> bytes:
    head = bytearray(1024)
    put(head, 0, "<I", frame_index)
    put(head, 4, "<Q", frame_time_us)
    put(head, 12, "<I", version)
    put(head, 20, "<Q", pc_time_us)
    put(head, 48, "<I", 3)                       # transmit mode
    put(head, 52, "<2f", window_start_m, window_length_m)
    put(head, 60, "<Ii", 12, 90)                 # threshold, intensity
    put(head, 68, "<I", receiver_gain)
    put(head, 136, "<f", -1.25)                  # platform pitch
    put(head, 144, "<f", 2.5)                    # platform roll
    put(head, 152, "<f", 181.0)                  # platform heading
    put(head, 160, "<3f", 233.75, 2.65, -1.0)    # compass heading/pitch/roll
    put(head, 172, "<2d", 44.6012345, -63.5123456)
    put(head, 224, "<f", 9.0)                    # water temperature
    put(head, 420, "<f", 1e6 / sample_period_us)
    put(head, 436, "<I", ping_mode)
    put(head, 440, "<I", frequency_hi_low)
    put(head, 444, "<2I", 24, 12_000)            # pulse width, cycle period
    put(head, 452, "<I", sample_period_us)
    put(head, 456, "<I", 1)                      # transmit enable
    put(head, 460, "<2f", 6.5, sound_speed_mps)  # frame rate, sound speed
    put(head, 468, "<I", samples_per_beam)
    put(head, 476, "<I", sample_start_delay_us)
    put(head, 480, "<2I", 0, system_type)        # large lens, system type
    put(head, 488, "<I", 4242)                   # sonar serial number
    put(head, 516, "<2I", reordered, 15)         # reordered samples, salinity
    return bytes(head)


def v5_frame(header: bytes | None = None, beams: int = 48,
             samples_per_beam: int = 6) -> bytes:
    body = header if header is not None else v5_frame_header(
        samples_per_beam=samples_per_beam)
    count = beams * samples_per_beam
    return body + bytes(i % 251 for i in range(count))


def v3_file_header(
    frame_count: int = 2,
    high_resolution: int = 0,
    num_raw_beams: int = 96,
    sample_rate_hz: float = 29_000.0,
    samples_per_channel: int = 8,
    window_start_code: int = 2,
    window_length_code: int = 1,
    serial_number: int = 10,
    sound_speed: int = 1450,
) -> bytes:
    head = bytearray(512)
    put(head, 0, "<I", DIDSON_V3_SIGNATURE)
    put(head, 4, "<I", frame_count)
    put(head, 8, "<I", 7)                        # frame rate
    put(head, 12, "<I", high_resolution)
    put(head, 16, "<I", num_raw_beams)
    put(head, 20, "<f", sample_rate_hz)
    put(head, 24, "<I", samples_per_channel)
    put(head, 28, "<I", 40)                      # receiver gain
    put(head, 32, "<2I", window_start_code, window_length_code)
    put(head, 40, "<I", 1)                       # reverse
    put(head, 44, "<I", serial_number)
    head[48:58] = b"2026-08-02"
    head[80:96] = b"synthetic didson"
    put(head, 352, "<2I", 626, 826)              # start/end frame
    put(head, 384, "<I", sound_speed)            # sspd, m/s
    put(head, 396, "<2I", 1, 1)                  # water temp code, salinity code
    return bytes(head)


def v3_frame_header(
    frame_index: int = 0,
    sonar_time_s: int = 1_786_000_000,
    clock: tuple[int, ...] = (2026, 8, 2, 14, 1, 33, 81),
    window_start_code: int = 2,
    window_length_code: int = 1,
    version: int = DIDSON_V3_SIGNATURE,
) -> bytes:
    head = bytearray(256)
    put(head, 0, "<I", frame_index)
    put(head, 4, "<Q", sonar_time_s)
    put(head, 12, "<I", version)
    put(head, 20, "<7I", *clock)
    put(head, 48, "<I", 3)                       # transmit mode
    put(head, 52, "<2I", window_start_code, window_length_code)
    put(head, 60, "<Ii", 15, 90)                 # threshold, intensity
    put(head, 68, "<I", 40)                      # receiver gain
    put(head, 72, "<5I", 36, 34, 32, 206, 123)   # degC1/degC2/humidity/focus/battery
    put(head, 160, "<3f", 233.75, 2.65, -1.0)    # compass heading/pitch/roll
    put(head, 172, "<2d", 44.6012345, -63.5123456)
    put(head, 228, "<I", 143)                    # timer period
    return bytes(head)


def v3_frame(header: bytes | None = None, beams: int = 96,
             samples_per_channel: int = 8) -> bytes:
    body = header if header is not None else v3_frame_header()
    count = beams * samples_per_channel
    return body + bytes(i % 251 for i in range(count))


def v5_stream(*frames: bytes, header: bytes | None = None) -> bytes:
    parts = [header if header is not None else v5_file_header()]
    parts.extend(frames if frames else
                 (v5_frame(v5_frame_header(frame_index=0)),
                  v5_frame(v5_frame_header(frame_index=1))))
    return b"".join(parts)


def v3_stream(*frames: bytes, header: bytes | None = None) -> bytes:
    parts = [header if header is not None else v3_file_header()]
    parts.extend(frames if frames else
                 (v3_frame(v3_frame_header(frame_index=0)),
                  v3_frame(v3_frame_header(frame_index=1))))
    return b"".join(parts)


def _records(data: bytes):
    return list(read_aris(data))


# ---------------------------------------------------------------------------
# signatures and layout sizes
# ---------------------------------------------------------------------------


def test_signatures_are_the_ddf_magic_bytes():
    # The version word is the ASCII "DDF" tag plus a version byte, stored
    # little endian, so the file starts with the literal bytes DDF.
    assert ARIS_SIGNATURE == 0x05464444
    assert DIDSON_V3_SIGNATURE == 0x03464444
    assert struct.pack("<I", ARIS_SIGNATURE) == b"DDF\x05"
    assert struct.pack("<I", DIDSON_V3_SIGNATURE) == b"DDF\x03"


def test_docstring_layout_sizes():
    """The header byte counts quoted in the module and record docstrings."""
    assert len(v5_file_header()) == 1024
    assert len(v5_frame_header()) == 1024
    assert len(v3_file_header()) == 512
    assert len(v3_frame_header()) == 256


def test_beam_count_table_matches_the_sdk():
    # get_beams_from_pingmode in the SDK's FrameFuncs.c
    expected = {1: 48, 2: 48, 3: 96, 4: 96, 5: 96, 6: 64, 7: 64, 8: 64,
                9: 128, 10: 128, 11: 128, 12: 128}
    for mode, beams in expected.items():
        assert beam_count_for_ping_mode(mode) == beams
    assert beam_count_for_ping_mode(0) == 0
    assert beam_count_for_ping_mode(13) == 0
    assert beam_count_for_ping_mode(99) == 0


def test_didson_delay_period_lookup():
    # Echoview's DelayPeriod table: rows by HighResolution, columns by SN
    assert didson_delay_period(0, 10) == pytest.approx(0.001024)
    assert didson_delay_period(0, 30) == pytest.approx(0.001144)
    assert didson_delay_period(1, 18) == pytest.approx(0.000512)
    assert didson_delay_period(1, 189) == pytest.approx(0.000572)


# ---------------------------------------------------------------------------
# file header decoding (round-trip: build bytes, parse, compare)
# ---------------------------------------------------------------------------


def test_v5_file_header_roundtrip():
    header = _records(v5_stream())[0]
    assert isinstance(header, DdfFileHeader)
    assert header.version == ARIS_SIGNATURE
    assert header.is_aris is True
    assert header.frame_count == 2
    assert header.num_raw_beams == 48
    assert header.samples_per_channel == 6
    assert header.serial_number == 4242
    assert header.date_text == "2026-08-01"
    assert header.header_id_text == "synthetic tank calibration"
    assert header.user_ids == (7, -7, 0, 0)
    assert header.start_frame == 100
    assert header.end_frame == 101
    assert header.water_temp_code == 1
    assert header.salinity_code == 0
    assert header.window_start_code is None
    assert header.window_length_code is None


def test_v3_file_header_roundtrip():
    header = _records(v3_stream())[0]
    assert isinstance(header, DdfFileHeader)
    assert header.version == DIDSON_V3_SIGNATURE
    assert header.is_aris is False
    assert header.frame_count == 2
    assert header.high_resolution == 0
    assert header.num_raw_beams == 96
    assert header.sample_rate_hz == pytest.approx(29_000.0)
    assert header.samples_per_channel == 8
    assert header.receiver_gain == 40
    assert header.window_start_code == 2
    assert header.window_length_code == 1
    assert header.window_start_m is None
    assert header.window_length_m is None
    assert header.reverse == 1
    assert header.serial_number == 10
    assert header.sound_speed_code == 1450
    assert header.date_text == "2026-08-02"
    assert header.header_id_text == "synthetic didson"


def test_version_discrimination():
    v5_records = _records(v5_stream())
    v3_records = _records(v3_stream())
    assert all(isinstance(r, ArisFrame) for r in v5_records[1:])
    assert all(isinstance(r, DidsonFrame) for r in v3_records[1:])
    assert len(v5_records) == 3
    assert len(v3_records) == 3


# ---------------------------------------------------------------------------
# ARIS (DDF v5) frames
# ---------------------------------------------------------------------------


def test_v5_frame_roundtrip():
    frame = _records(v5_stream())[1]
    assert isinstance(frame, ArisFrame)
    assert frame.frame_index == 0
    assert frame.frame_time_us == 1_756_000_000_000_000
    assert frame.time == pytest.approx(1_756_000_000.0)
    assert frame.pc_time_us == 1_756_000_000_500_000
    assert frame.version == ARIS_SIGNATURE
    assert frame.transmit_mode == 3
    assert frame.window_start_m == pytest.approx(1.51)
    assert frame.window_length_m == pytest.approx(0.046)
    assert frame.threshold == 12
    assert frame.intensity == 90
    assert frame.receiver_gain == 18
    assert frame.platform_pitch == pytest.approx(-1.25)
    assert frame.platform_roll == pytest.approx(2.5)
    assert frame.platform_heading == pytest.approx(181.0)
    assert frame.compass_heading == pytest.approx(233.75)
    assert frame.compass_pitch == pytest.approx(2.65)
    assert frame.compass_roll == pytest.approx(-1.0)
    assert frame.latitude == pytest.approx(44.6012345)
    assert frame.longitude == pytest.approx(-63.5123456)
    assert frame.water_temp_c == pytest.approx(9.0)
    assert frame.ping_mode == 1
    assert frame.beam_count == 48
    assert frame.frequency_hi_low == 1
    assert frame.is_high_frequency is True
    assert frame.pulse_width_us == 24
    assert frame.cycle_period_us == 12_000
    assert frame.sample_period_us == 10
    assert frame.transmit_enable == 1
    assert frame.frame_rate_hz == pytest.approx(6.5)
    assert frame.sound_speed_mps == pytest.approx(1500.0)
    assert frame.samples_per_beam == 6
    assert frame.sample_start_delay_us == 2000
    assert frame.large_lens == 0
    assert frame.system_type == 2
    assert frame.system_model == "ARIS 1200"
    assert frame.sonar_serial_number == 4242
    assert frame.reordered_samples == 1
    assert frame.salinity == 15
    assert len(frame.samples) == 48 * 6
    assert len(frame.header_bytes) == 1024


def test_v5_derived_geometry_hand_computed():
    # SampleStartDelay 2000 us at 1500 m/s: 2000e-6 * 1500 / 2 = 1.5 m.
    # SamplePeriod 10 us over 6 samples: 10 * 6 * 1e-6 * 1500 / 2 = 0.045 m.
    # The header's own stored floats are deliberately offset from these so
    # the test proves the derivation reads the settings, not the floats.
    frame = _records(v5_stream())[1]
    assert frame.derived_window_start_m == pytest.approx(1.5)
    assert frame.derived_window_length_m == pytest.approx(0.045)
    assert frame.sample_spacing_m == pytest.approx(0.0075)
    assert frame.window_start_m != frame.derived_window_start_m


def test_v5_frame_indices_and_order():
    records = _records(v5_stream())
    assert [f.frame_index for f in records[1:]] == [0, 1]


def test_v5_ping_mode_sets_beam_count():
    header = v5_frame_header(ping_mode=9, samples_per_beam=4)
    data = v5_stream(v5_frame(header, beams=128, samples_per_beam=4),
                     header=v5_file_header(num_raw_beams=128,
                                           samples_per_channel=4))
    (_, frame) = _records(data)
    assert frame.beam_count == 128
    assert len(frame.samples) == 128 * 4


def test_v5_unknown_ping_mode_falls_back_to_file_header_beams():
    header = v5_frame_header(ping_mode=99, samples_per_beam=4)
    data = v5_stream(v5_frame(header, beams=64, samples_per_beam=4),
                     header=v5_file_header(num_raw_beams=64,
                                           samples_per_channel=4))
    (_, frame) = _records(data)
    assert frame.beam_count == 64
    assert len(frame.samples) == 64 * 4


def test_v5_unsizable_first_frame_is_malformed_not_fatal():
    header = v5_frame_header(ping_mode=99, samples_per_beam=0)
    data = v5_stream(v5_frame(header, beams=0, samples_per_beam=1),
                     header=v5_file_header(num_raw_beams=0))
    records = _records(data)
    assert isinstance(records[0], DdfFileHeader)
    assert isinstance(records[1], MalformedRecord)
    assert records[1].tag == "FRAME"


def test_v5_sample_layout_is_range_row_major():
    # One range row spans every beam before the next row starts, so byte
    # (sample * beam_count + beam) belongs to that beam and range sample.
    beams, spb = 48, 6
    data = bytes((s * beams + b) % 251 for s in range(spb) for b in range(beams))
    stream = v5_stream(v5_frame_header() + data)
    (_, frame) = _records(stream)
    rows = frame.rows()
    assert len(rows) == spb
    assert all(len(row) == beams for row in rows)
    assert rows[2][5] == (2 * beams + 5) % 251
    profile = frame.beam_profile(5)
    assert len(profile) == spb
    assert profile[2] == (2 * beams + 5) % 251
    with pytest.raises(ValueError):
        frame.beam_profile(beams)


# ---------------------------------------------------------------------------
# DIDSON (DDF v3) frames
# ---------------------------------------------------------------------------


def test_v3_frame_roundtrip():
    frame = _records(v3_stream())[1]
    assert isinstance(frame, DidsonFrame)
    assert frame.frame_index == 0
    assert frame.sonar_time_s == 1_786_000_000
    assert frame.version == DIDSON_V3_SIGNATURE
    assert (frame.year, frame.month, frame.day) == (2026, 8, 2)
    assert (frame.hour, frame.minute, frame.second, frame.hsecond) == (14, 1, 33, 81)
    assert frame.time_of_day == pytest.approx(14 * 3600 + 60 + 33.81)
    assert frame.transmit_mode == 3
    assert frame.window_start_code == 2
    assert frame.window_length_code == 1
    assert frame.threshold == 15
    assert frame.intensity == 90
    assert frame.receiver_gain == 40
    assert frame.deg_c1 == 36
    assert frame.deg_c2 == 34
    assert frame.humidity == 32
    assert frame.focus == 206
    assert frame.battery == 123
    assert frame.compass_heading == pytest.approx(233.75)
    assert frame.compass_pitch == pytest.approx(2.65)
    assert frame.compass_roll == pytest.approx(-1.0)
    assert frame.latitude == pytest.approx(44.6012345)
    assert frame.longitude == pytest.approx(-63.5123456)
    assert frame.timer_period == 143
    assert frame.beam_count == 96
    assert frame.samples_per_beam == 8
    assert len(frame.samples) == 96 * 8
    assert len(frame.header_bytes) == 256


def test_v3_window_bounds_hand_computed():
    # HighResolution 0, SN 10: delay period 0.001024 s. Start code 2 at
    # 1450 m/s: 2 * 0.001024 * 1450 / 2 = 1.4848 m. Length over 8 samples
    # at 29 kHz: 8 * 1450 / (2 * 29000) = 0.2 m.
    frame = _records(v3_stream())[1]
    assert frame.delay_period_s == pytest.approx(0.001024)
    assert frame.sound_speed_mps == pytest.approx(1450.0)
    assert frame.window_start_m == pytest.approx(1.4848)
    assert frame.window_length_m == pytest.approx(0.2)
    assert frame.sample_spacing_m == pytest.approx(0.025)


def test_v3_zero_sample_rate_never_raises():
    data = v3_stream(header=v3_file_header(sample_rate_hz=0.0))
    (_, frame, _) = _records(data)
    assert frame.window_length_m is None
    assert frame.sample_spacing_m is None
    assert frame.window_start_m == pytest.approx(1.4848)


def test_v3_geometry_comes_from_the_file_header():
    data = v3_stream(
        v3_frame(v3_frame_header(), beams=24, samples_per_channel=5),
        header=v3_file_header(num_raw_beams=24, samples_per_channel=5,
                              frame_count=1),
    )
    (_, frame) = _records(data)
    assert frame.beam_count == 24
    assert frame.samples_per_beam == 5
    assert len(frame.samples) == 24 * 5


# ---------------------------------------------------------------------------
# degradation: truncation, garbage, bad signatures
# ---------------------------------------------------------------------------


def test_truncated_final_frame_degrades_to_malformed():
    whole = v5_stream()
    records = _records(whole[:-10])
    assert isinstance(records[1], ArisFrame)
    assert isinstance(records[2], MalformedRecord)
    assert records[2].tag == "FRAME"
    assert "truncat" in records[2].error


def test_truncated_frame_header_degrades_to_malformed():
    data = v5_stream()[: 1024 + 100]
    records = _records(data)
    assert isinstance(records[0], DdfFileHeader)
    assert isinstance(records[1], MalformedRecord)


def test_truncated_file_header_is_malformed_not_fatal():
    (record,) = _records(v5_file_header()[:100])
    assert isinstance(record, MalformedRecord)
    assert record.tag == "HDR"


def test_garbage_and_empty_inputs_never_raise():
    (record,) = _records(b"")
    assert isinstance(record, MalformedRecord)
    (record,) = _records(b"not a sonar recording at all" * 40)
    assert isinstance(record, MalformedRecord)
    assert record.tag == "HDR"


def test_frame_with_wrong_signature_still_decodes():
    bad = v5_frame(v5_frame_header(frame_index=1, version=0xDEADBEEF))
    data = v5_stream(v5_frame(v5_frame_header(frame_index=0)), bad)
    records = _records(data)
    assert isinstance(records[2], ArisFrame)
    assert records[2].version == 0xDEADBEEF


def test_header_text_fields_tolerate_garbage_after_nul():
    date = b"2026-08-02\x00\x02\x08\xff\xb3"
    header = _records(v3_stream(header=v3_file_header() ))[0]
    assert header.date_text == "2026-08-02"
    raw = bytearray(v3_file_header())
    raw[48:48 + len(date)] = date
    stream = bytes(raw) + v3_frame() + v3_frame()
    header = _records(stream)[0]
    assert header.date_text == "2026-08-02"


# ---------------------------------------------------------------------------
# load_imaging
# ---------------------------------------------------------------------------


def test_load_imaging_bundles_frames_and_counters():
    bundle = load_imaging(v5_stream())
    assert bundle.file_header is not None
    assert bundle.file_header.is_aris is True
    assert len(bundle.frames) == 2
    assert [f.frame_index for f in bundle.frames] == [0, 1]
    assert bundle.counters.frames == 2
    assert bundle.counters.malformed == 0
    assert bundle.counters.signature_mismatches == 0
    assert bundle.counters.geometry_mismatches == 0
    assert bundle.counters.bytes_skipped == 0


def test_load_imaging_counts_truncated_tail_bytes():
    whole = v5_stream()
    bundle = load_imaging(whole[:-10])
    assert len(bundle.frames) == 1
    assert bundle.counters.malformed == 1
    assert bundle.counters.bytes_skipped == 1024 + 48 * 6 - 10


def test_load_imaging_counts_signature_mismatches():
    bad = v5_frame(v5_frame_header(frame_index=1, version=0x0BAD0BAD))
    bundle = load_imaging(v5_stream(v5_frame(v5_frame_header()), bad))
    assert bundle.counters.frames == 2
    assert bundle.counters.signature_mismatches == 1


def test_load_imaging_counts_geometry_mismatches():
    # A mid-file frame declaring different geometry than the lattice built
    # from frame zero: its samples slice keeps the lattice size, and the
    # divergence is counted, never trusted for walking.
    odd = v5_frame(v5_frame_header(frame_index=1, ping_mode=9,
                                   samples_per_beam=6),
                   beams=48, samples_per_beam=6)
    bundle = load_imaging(v5_stream(v5_frame(v5_frame_header()), odd))
    assert bundle.counters.frames == 2
    assert bundle.counters.geometry_mismatches == 1
    assert len(bundle.frames[1].samples) == 48 * 6


def test_load_imaging_on_garbage_never_raises():
    bundle = load_imaging(b"\x00\x01" * 700)
    assert bundle.file_header is None
    assert bundle.frames == ()
    assert bundle.counters.frames == 0
    assert bundle.counters.bytes_skipped == 1400


def test_load_imaging_v3_bundle():
    bundle = load_imaging(v3_stream())
    assert bundle.file_header is not None
    assert bundle.file_header.is_aris is False
    assert len(bundle.frames) == 2
    assert all(isinstance(f, DidsonFrame) for f in bundle.frames)
    assert bundle.counters.bytes_skipped == 0


# ---------------------------------------------------------------------------
# real sample validation (SDK sample.aris; raw DIDSON sturgeon clips)
# ---------------------------------------------------------------------------

_ARIS_SAMPLE = os.environ.get("ARIS_SAMPLE", "")
_DDF_DIR = os.environ.get("DDF_SAMPLE_DIR", "")


@pytest.mark.skipif(not (_ARIS_SAMPLE and os.path.exists(_ARIS_SAMPLE)),
                    reason="ARIS_SAMPLE not set or file missing")
def test_real_aris_sample_statistics():
    """Statistics measured 2026-08-29 from the SDK's own sample.aris
    (583,168 bytes; source URL in docs/FORMAT-SOURCES.md anchor S8).

    Six frames of 48 beams by 2000 samples (ping mode 1 on an ARIS 1200,
    serial 1098, telephoto lens fitted), every frame signature intact and
    nothing skipped. Frame times are microsecond-resolution and strictly
    increasing at about 152.6 ms spacing, matching the header's own 6.55
    Hz frame rate. Every frame carries strong image energy (mean sample
    value above 80 on the 0-255 scale). The stored window floats
    (3.3299 m start, 20.30 m length) disagree with the SDK formula
    applied at the frame's own calculated sound speed of 1435.93 m/s
    (3.2976 m and 20.103 m); both back-solve to a nominal 1450 m/s, so
    the writer baked a default sound speed into the floats. The derived
    values are the self-consistent ones (see the anchor errata for S8).
    """
    bundle = load_imaging(_ARIS_SAMPLE)
    counters = bundle.counters
    assert counters.frames == 6
    assert counters.malformed == 0
    assert counters.signature_mismatches == 0
    assert counters.geometry_mismatches == 0
    assert counters.bytes_skipped == 0

    header = bundle.file_header
    assert header is not None
    assert header.is_aris is True
    assert header.frame_count == 6
    assert header.num_raw_beams == 48
    assert header.samples_per_channel == 2000
    assert header.serial_number == 1098

    assert len(bundle.frames) == 6
    assert [f.frame_index for f in bundle.frames] == list(range(6))
    for frame in bundle.frames:
        assert frame.ping_mode == 1
        assert frame.beam_count == 48
        assert frame.samples_per_beam == 2000
        assert frame.system_type == 2
        assert frame.system_model == "ARIS 1200"
        assert frame.large_lens == 1
        assert frame.sonar_serial_number == 1098
        assert frame.reordered_samples == 1
        assert frame.frequency_hi_low == 1
        assert frame.sample_period_us == 14
        assert frame.sample_start_delay_us == 4593
        assert frame.receiver_gain == 24
        assert len(frame.samples) == 48 * 2000
        assert sum(frame.samples) / len(frame.samples) > 80.0

    times = [f.frame_time_us for f in bundle.frames]
    assert all(b > a for a, b in zip(times, times[1:], strict=False))
    spacing = (times[-1] - times[0]) / 5 / 1e6
    assert spacing == pytest.approx(0.1528, abs=0.001)
    assert bundle.frames[0].frame_rate_hz == pytest.approx(6.548, abs=0.001)

    first = bundle.frames[0]
    assert first.sound_speed_mps == pytest.approx(1435.93, abs=0.01)
    assert first.window_start_m == pytest.approx(3.3299, abs=0.0001)
    assert first.window_length_m == pytest.approx(20.30, abs=0.001)
    assert first.derived_window_start_m == pytest.approx(3.2976, abs=0.0001)
    assert first.derived_window_length_m == pytest.approx(20.103, abs=0.001)
    baked = first.window_length_m / first.derived_window_length_m \
        * first.sound_speed_mps
    assert baked == pytest.approx(1450.0, abs=0.01)
    assert first.water_temp_c == pytest.approx(9.0, abs=0.1)
    assert first.compass_heading == pytest.approx(49.6, abs=0.1)
    assert first.compass_pitch == pytest.approx(-2.2, abs=0.1)
    assert first.compass_roll == pytest.approx(-2.7, abs=0.1)


@pytest.mark.skipif(not (_DDF_DIR and os.path.isdir(_DDF_DIR)),
                    reason="DDF_SAMPLE_DIR not set or directory missing")
def test_real_didson_sample_statistics():
    """Statistics measured 2026-08-29 from ten raw DIDSON DDF v3 sturgeon
    clips recorded 2007-10-31 to 2007-11-02 (CC0; anchor S8).

    All ten files are HF clips from the same sonar (serial 189): 96 beams
    by 512 samples, receiver gain 40, sound speed word 1457 m/s, and the
    declared frame count equals the frame count implied by the file size
    exactly, 1569 frames in all with zero leftover bytes. Every frame
    signature is the v3 magic and the frame indices count up from zero.
    The calendar clock fields are non-decreasing within every file, while
    the whole-second sonar time word rolls late against them (the reason
    this library treats the calendar fields as the frame clock). Window
    start codes span 1 to 12 across the set, refuting the 0-to-3 range in
    Echoview's description; the first clip decodes to a 1.667 m start and
    a 10.003 m window at the header's 1457 m/s. Image energy is strong
    throughout (per-file mean sample values between 54 and 92).
    """
    expected_frames = {
        "2007-10-31_140001_HF_Clip1.ddf": 201,
        "2007-10-31_162000_HF_Clip9.ddf": 129,
        "2007-10-31_172000_HF_Clip2.ddf": 233,
        "2007-11-01_113000_HF_Clip6.ddf": 151,
        "2007-11-01_141000_HF_Clip10.ddf": 129,
        "2007-11-01_160549_HF_Clip3.ddf": 126,
        "2007-11-01_160549_HF_Clip8.ddf": 134,
        "2007-11-02_082740_HF_Clip4.ddf": 115,
        "2007-11-02_101000_HF_Clip5.ddf": 203,
        "2007-11-02_101000_HF_Clip7.ddf": 148,
    }
    names = sorted(n for n in os.listdir(_DDF_DIR) if n.endswith(".ddf"))
    assert names == sorted(expected_frames)

    start_codes = set()
    total = 0
    for name in names:
        bundle = load_imaging(os.path.join(_DDF_DIR, name))
        counters = bundle.counters
        header = bundle.file_header
        assert header is not None
        assert header.is_aris is False
        assert header.high_resolution == 1
        assert header.num_raw_beams == 96
        assert header.samples_per_channel == 512
        assert header.receiver_gain == 40
        assert header.serial_number == 189
        assert header.sound_speed_code == 1457
        assert header.frame_count == expected_frames[name]
        assert counters.frames == expected_frames[name]
        assert counters.malformed == 0
        assert counters.signature_mismatches == 0
        assert counters.bytes_skipped == 0
        total += counters.frames
        start_codes.add(header.window_start_code)

        assert [f.frame_index for f in bundle.frames] == \
            list(range(len(bundle.frames)))
        clocks = [(f.year, f.month, f.day, f.time_of_day)
                  for f in bundle.frames]
        assert all(b >= a for a, b in zip(clocks, clocks[1:], strict=False))
        sonar_seconds = [f.sonar_time_s for f in bundle.frames]
        assert all(b >= a for a, b in
                   zip(sonar_seconds, sonar_seconds[1:], strict=False))
        for frame in bundle.frames:
            assert frame.beam_count == 96
            assert frame.samples_per_beam == 512
            assert frame.window_start_code == header.window_start_code
            assert frame.window_length_code == header.window_length_code
        mean = sum(bundle.frames[0].samples) / len(bundle.frames[0].samples)
        assert 54.0 <= mean <= 92.0

    assert total == 1569
    assert start_codes == {1, 4, 6, 12}
    assert max(start_codes) > 3  # beyond Echoview's stated 0..3 range

    first = load_imaging(
        os.path.join(_DDF_DIR, "2007-10-31_140001_HF_Clip1.ddf"))
    frame = first.frames[0]
    assert frame.delay_period_s == pytest.approx(0.000572)
    assert frame.window_start_m == pytest.approx(1.667, abs=0.001)
    assert frame.window_length_m == pytest.approx(10.003, abs=0.001)
    assert frame.compass_heading == pytest.approx(233.75, abs=0.01)
    assert frame.compass_pitch == pytest.approx(2.65, abs=0.01)
    assert frame.compass_roll == pytest.approx(-1.0, abs=0.01)
    assert (frame.year, frame.month, frame.day) == (2007, 10, 31)
    assert (frame.hour, frame.minute) == (14, 1)
