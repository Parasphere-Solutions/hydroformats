"""Blueprint Oculus dialect: item walking, ping decoding, loading.

Fixtures are synthetic bytes assembled by hand from the S13 layouts
(builders in oculus_builders.py; see hydroformats/oculus.py for the
citations); all values are fictional. The real-sample integration tests at the bottom run only
when OCULUS_SAMPLE points at a ViewPoint .oculus log from the CC0
Parnum et al. 2024 survey and OCULUS_RAW_SAMPLE at liboculus's
three_pings_8bit.raw capture (docs/FORMAT-SOURCES.md anchor S13).
"""
import os
import struct

import pytest
from oculus_builders import (
    container,
    file_header,
    image_rows,
    item_header,
    msg_header,
    v1_ping,
    v2_ping,
)

from hydroformats.oculus import (
    FILE_MAGIC,
    ITEM_MAGIC,
    OCULUS_ID,
    SONAR_ITEM,
    V1_PING_STRUCT_SIZE,
    V2_PING_STRUCT_SIZE,
    OculusFileHeader,
    OculusGap,
    OculusItem,
    OculusPing,
    bytes_per_sample,
    iter_items,
    load_imaging,
    read_oculus,
    read_oculus_raw,
)
from hydroformats.records import MalformedRecord


def _records(data: bytes):
    return list(read_oculus(data))


# ---------------------------------------------------------------------------
# magics and layout sizes
# ---------------------------------------------------------------------------


def test_magic_words_and_wire_bytes():
    # The message magic is the ASCII bytes "SO" on the wire; the file
    # and item magics are the byte-order tell-tales.
    assert FILE_MAGIC == 0x11223344
    assert ITEM_MAGIC == 0xAABBCCDD
    assert OCULUS_ID == 0x4F53
    assert struct.pack("<H", OCULUS_ID) == b"SO"
    assert struct.pack("<I", FILE_MAGIC) == b"\x44\x33\x22\x11"
    assert struct.pack("<I", ITEM_MAGIC) == b"\xdd\xcc\xbb\xaa"


def test_docstring_layout_sizes():
    """The byte counts quoted in the module and record docstrings."""
    assert len(file_header()) == 48
    assert len(item_header(0)) == 40
    assert len(msg_header(0)) == 16
    assert V1_PING_STRUCT_SIZE == 122
    assert V2_PING_STRUCT_SIZE == 202
    assert len(v1_ping(filler=b"", n_beams=4)) == 122 + 8 + 3 * 4
    assert len(v2_ping(filler=b"", n_beams=4)) == 202 + 8 + 3 * 4


def test_bytes_per_sample_table():
    # DataSizeType: 0 through 3 count 1 through 4 bytes; else unknown.
    assert {code: bytes_per_sample(code) for code in range(4)} == \
        {0: 1, 1: 2, 2: 3, 3: 4}
    assert bytes_per_sample(4) is None
    assert bytes_per_sample(255) is None


# ---------------------------------------------------------------------------
# file header and item walking
# ---------------------------------------------------------------------------


def test_file_header_roundtrip():
    header = _records(container())[0]
    assert isinstance(header, OculusFileHeader)
    assert header.magic == FILE_MAGIC
    assert header.size_header == 48
    assert header.source_text == "Oculus"
    assert header.version == 1
    assert header.encryption == 0
    assert header.key == 0
    assert header.time == pytest.approx(1_756_000_000.5)
    assert len(header.header_bytes) == 48


def test_iter_items_frames_the_container():
    events = list(iter_items(container(v1_ping(), v2_ping())))
    assert isinstance(events[0], OculusFileHeader)
    assert [type(e) for e in events[1:]] == [OculusItem, OculusItem]
    first = events[1]
    assert first.item_type == SONAR_ITEM
    assert first.size_header == 40
    assert first.compression == 0
    assert first.time == pytest.approx(1_756_000_001.25)
    assert first.original_size == len(first.payload)
    assert events[2].time == pytest.approx(1_756_000_001.5)


def test_grown_headers_skip_by_declared_size():
    # A future writer may grow either header; the declared sizes rule
    # (the item builder already pads itself to its declared size).
    ping = v1_ping()
    data = (file_header(size_header=64) + b"\x00" * 16
            + item_header(len(ping), size_header=56) + ping)
    records = _records(data)
    assert isinstance(records[0], OculusFileHeader)
    assert isinstance(records[1], OculusPing)
    assert records[1].ping_id == 77


def test_garbage_between_items_resynchronizes():
    ping = v1_ping()
    tail = item_header(len(ping)) + ping
    data = container() + b"not an item header" + tail
    events = list(iter_items(data))
    gaps = [e for e in events if isinstance(e, OculusGap)]
    assert len(gaps) == 1
    assert gaps[0].size == len(b"not an item header")
    assert sum(isinstance(e, OculusItem) for e in events) == 2


def test_item_overrunning_the_file_degrades():
    ping = v1_ping()
    data = file_header() + item_header(len(ping) + 500) + ping
    records = _records(data)
    assert isinstance(records[0], OculusFileHeader)
    assert isinstance(records[1], MalformedRecord)
    assert "truncated" in records[1].error


def test_corrupt_length_cannot_swallow_the_next_item():
    ping = v1_ping()
    bad = item_header(len(ping) + 10_000) + ping[:30]
    good = item_header(len(ping)) + ping
    records = _records(file_header() + bad + good)
    assert isinstance(records[1], MalformedRecord)  # the bad range
    assert isinstance(records[2], OculusPing)
    assert records[2].ping_id == 77


def test_truncated_item_header_degrades():
    data = file_header() + item_header(100)[:20]
    records = _records(data)
    assert isinstance(records[1], MalformedRecord)
    assert "truncated" in records[1].error


def test_garbage_and_empty_inputs_never_raise():
    (record,) = _records(b"")
    assert isinstance(record, MalformedRecord)
    (record,) = _records(b"not a sonar log at all, not even close")
    assert isinstance(record, MalformedRecord)
    assert record.tag == "HDR"


def test_sqlite_viewpoint_v2_log_is_refused_by_name():
    data = b"SQLite format 3\x00" + b"\x00" * 100
    (record,) = _records(data)
    assert isinstance(record, MalformedRecord)
    assert "ViewPoint V2" in record.error
    assert "SQLite" in record.error


def test_encrypted_log_refuses_loudly():
    ping = v1_ping()
    data = (file_header(encryption=7) + item_header(len(ping)) + ping)
    records = _records(data)
    assert isinstance(records[0], OculusFileHeader)
    assert records[0].encryption == 7
    assert isinstance(records[1], MalformedRecord)
    assert "encrypted" in records[1].error
    assert len(records) == 2


# ---------------------------------------------------------------------------
# version 1 pings
# ---------------------------------------------------------------------------


def test_v1_ping_roundtrip():
    (_, ping) = _records(container(v1_ping()))
    assert isinstance(ping, OculusPing)
    assert ping.message_version == 0
    assert ping.src_device_id == 4242
    assert ping.dst_device_id == 0
    assert ping.log_time == pytest.approx(1_756_000_001.25)
    assert ping.ping_id == 77
    assert ping.status == 0
    assert ping.master_mode == 1
    assert ping.is_high_frequency is False
    assert ping.ping_rate_raw == 7
    assert ping.network_speed_raw == 255
    assert ping.gamma_correction == 127
    assert ping.flags == 0x19
    assert ping.range_is_meters is True
    assert ping.is_16bit_data is False
    assert ping.sends_gain is False
    assert ping.is_simple_return is True
    assert ping.gain_assistance is True
    assert ping.wants_512_beams is False
    assert ping.range_setting == pytest.approx(3.0)
    assert ping.gain_percent == pytest.approx(55.0)
    assert ping.speed_of_sound_mps == pytest.approx(1490.0)
    assert ping.salinity_ppt == pytest.approx(0.0)
    assert ping.frequency_hz == pytest.approx(750_000.0)
    assert ping.temperature_c == pytest.approx(11.5)
    assert ping.pressure_bar == pytest.approx(0.25)
    assert ping.speed_of_sound_used_mps == pytest.approx(1481.25)
    assert ping.ping_start_word == 123_456_789
    assert ping.ping_start_time_s is None
    assert ping.heading_deg is None
    assert ping.pitch_deg is None
    assert ping.roll_deg is None
    assert ping.ext_flags is None
    assert ping.fire_reserved is None
    assert ping.data_size == 0
    assert ping.sample_size == 1
    assert ping.range_resolution_m == pytest.approx(0.01)
    assert ping.range_m == pytest.approx(0.03)
    assert (ping.n_ranges, ping.n_beams) == (3, 4)
    assert ping.bearings_raw == (-150, -50, 50, 150)
    assert ping.bearings_deg == (-1.5, -0.5, 0.5, 1.5)
    assert ping.aperture_deg == pytest.approx(3.0)
    assert ping.gains is None
    assert len(ping.samples) == 3 * 4
    assert len(ping.header_bytes) == 122


def test_message_version_zero_and_one_both_decode_as_v1():
    # Real version 1 hardware stamps msgVersion 0 (anchor errata); a
    # hypothetical writer stamping 1 must land on the same layout.
    for version in (0, 1):
        (_, ping) = _records(container(v1_ping(msg_version=version)))
        assert isinstance(ping, OculusPing)
        assert ping.message_version == version
        assert ping.ping_start_word == 123_456_789


def test_image_offset_is_honored_not_computed():
    # The filler between bearings and image is nonzero on real
    # hardware; none of it may leak into the samples.
    ping_bytes = v1_ping(filler=b"\xde\xad\xbe\xef" * 5)
    (_, ping) = _records(container(ping_bytes))
    assert ping.image_offset == 122 + 8 + 20
    assert ping.samples == image_rows(3, 4, 1, gain_rows=False)
    assert b"\xde\xad" not in ping.samples


def test_v1_sample_layout_is_range_row_major():
    (_, ping) = _records(container(v1_ping()))
    rows = ping.rows()
    assert len(rows) == 3
    assert all(len(row) == 4 for row in rows)
    assert rows[2][1] == (2 * 4 + 1) % 251
    assert ping.row_values(2) == tuple((2 * 4 + i) % 251 for i in range(4))
    assert ping.beam_values(1) == tuple((r * 4 + 1) % 251 for r in range(3))
    with pytest.raises(ValueError):
        ping.beam_values(4)
    with pytest.raises(ValueError):
        ping.row_values(3)


def test_16bit_samples_decode_little_endian():
    image = struct.pack("<12H", *(512 + i for i in range(12)))
    (_, ping) = _records(container(v1_ping(data_size=1, image=image)))
    assert ping.sample_size == 2
    assert len(ping.samples) == 24
    assert ping.row_values(0) == (512, 513, 514, 515)
    assert ping.beam_values(3) == (515, 519, 523)


# ---------------------------------------------------------------------------
# version 2 pings
# ---------------------------------------------------------------------------


def test_v2_ping_roundtrip():
    (_, ping) = _records(container(v2_ping()))
    assert isinstance(ping, OculusPing)
    assert ping.message_version == 2
    assert ping.ping_id == 88
    assert ping.master_mode == 2
    assert ping.is_high_frequency is True
    assert ping.range_setting == pytest.approx(7.5)
    assert ping.gain_percent == pytest.approx(92.0)
    assert ping.salinity_ppt == pytest.approx(35.0)
    assert ping.ext_flags == 0x200
    assert ping.fire_reserved == (0xA5A5A5A5,) * 8
    assert ping.frequency_hz == pytest.approx(1_200_000.0)
    assert ping.temperature_c == pytest.approx(26.5)
    assert ping.pressure_bar == pytest.approx(1.75)
    assert ping.heading_deg == pytest.approx(212.5)
    assert ping.pitch_deg == pytest.approx(8.25)
    assert ping.roll_deg == pytest.approx(-3.5)
    assert ping.speed_of_sound_used_mps == pytest.approx(1500.25)
    assert ping.ping_start_time_s == pytest.approx(2851.75)
    assert ping.ping_start_word is None
    assert ping.range_resolution_m == pytest.approx(0.02)
    assert ping.range_m == pytest.approx(0.06)
    assert len(ping.header_bytes) == 202
    assert len(ping.samples) == 3 * 4


def test_v2_gain_rows_split_from_samples():
    (_, ping) = _records(container(v2_ping(flags=0x1D)))
    assert ping.sends_gain is True
    assert ping.gains == (1000, 1001, 1002)
    assert ping.samples == image_rows(3, 4, 1, gain_rows=False)
    assert ping.image_size == 3 * (4 + 4)


def test_gain_flag_with_wrong_lattice_is_refused():
    # Flag says gain rows, but the image size fits plain rows: refuse
    # loudly rather than guess which one lies.
    plain = image_rows(3, 4, 1, gain_rows=False)
    (_, record) = _records(container(v2_ping(flags=0x1D, image=plain)))
    assert isinstance(record, MalformedRecord)
    assert "gain-prefixed rows" in record.error


def test_plain_image_with_wrong_lattice_is_refused():
    (_, record) = _records(container(v1_ping(image=b"\x01" * 13)))
    assert isinstance(record, MalformedRecord)
    assert "rows of" in record.error


def test_unknown_data_size_word_is_refused():
    (_, record) = _records(container(v1_ping(data_size=9)))
    assert isinstance(record, MalformedRecord)
    assert "data size word 9" in record.error


def test_bearing_table_overlapping_image_is_refused():
    # Patch the beam count upward after building, so the bearing table
    # would run past the declared image offset: a refusal, not a read
    # into the image bytes.
    raw = bytearray(v1_ping())
    struct.pack_into("<H", raw, 108, 10)
    (_, record) = _records(container(bytes(raw)))
    assert isinstance(record, MalformedRecord)
    assert "overlaps" in record.error


def test_image_overrunning_payload_is_refused():
    whole = v1_ping()
    (_, record) = _records(container(whole[:-5]))
    assert isinstance(record, MalformedRecord)
    assert "overruns" in record.error


# ---------------------------------------------------------------------------
# skipping and counting
# ---------------------------------------------------------------------------


def test_non_sonar_items_are_skipped_and_counted():
    settings = item_header(12, item_type=1) + b"\x00" * 12
    ping = v1_ping()
    data = container(items=(settings, item_header(len(ping)) + ping))
    records = _records(data)
    assert len(records) == 2  # header + ping
    bundle = load_imaging(data)
    assert bundle.counters.items == 2
    assert bundle.counters.pings == 1
    assert bundle.counters.skipped_item_types == ((1, 1),)


def test_non_ping_message_ids_are_skipped_and_counted():
    dummy = msg_header(0, msg_id=0xFF)
    data = container(items=(item_header(len(dummy)) + dummy,))
    records = _records(data)
    assert len(records) == 1
    bundle = load_imaging(data)
    assert bundle.counters.unknown_message_ids == ((0xFF, 1),)


def test_compressed_sonar_items_are_skipped_and_counted():
    ping = v1_ping()
    data = container(items=(
        item_header(len(ping), compression=1) + ping,))
    records = _records(data)
    assert len(records) == 1
    bundle = load_imaging(data)
    assert bundle.counters.compressed_items == 1
    assert bundle.counters.pings == 0


def test_wrong_message_magic_is_malformed():
    bad = b"XX" + msg_header(0)[2:]
    data = container(items=(item_header(len(bad)) + bad,))
    (_, record) = _records(data)
    assert isinstance(record, MalformedRecord)
    assert "0x4F53" in record.error


def test_load_imaging_bundles_pings_and_counters():
    bundle = load_imaging(container(v1_ping(), v2_ping()))
    assert bundle.file_header is not None
    assert [p.ping_id for p in bundle.pings] == [77, 88]
    assert [p.message_version for p in bundle.pings] == [0, 2]
    counters = bundle.counters
    assert counters.items == 2
    assert counters.pings == 2
    assert counters.malformed == 0
    assert counters.bytes_skipped == 0
    assert counters.skipped_item_types == ()
    assert counters.unknown_message_ids == ()
    assert counters.compressed_items == 0


def test_load_imaging_on_garbage_never_raises():
    bundle = load_imaging(b"\x00\x01" * 700)
    assert bundle.file_header is None
    assert bundle.pings == ()
    assert bundle.counters.malformed == 1
    assert bundle.counters.bytes_skipped == 1400


# ---------------------------------------------------------------------------
# raw message streams
# ---------------------------------------------------------------------------


def test_raw_stream_yields_pings_without_log_times():
    stream = v1_ping() + v2_ping()
    records = list(read_oculus_raw(stream))
    assert [type(r) for r in records] == [OculusPing, OculusPing]
    assert [r.ping_id for r in records] == [77, 88]
    assert all(r.log_time is None for r in records)


def test_raw_stream_resynchronizes_on_garbage():
    stream = b"leading garbage" + v1_ping()
    records = list(read_oculus_raw(stream))
    assert isinstance(records[0], MalformedRecord)
    assert isinstance(records[1], OculusPing)


def test_raw_stream_truncated_tail_degrades():
    stream = v1_ping() + v1_ping()[:50]
    records = list(read_oculus_raw(stream))
    assert isinstance(records[0], OculusPing)
    assert isinstance(records[-1], MalformedRecord)


def test_raw_stream_on_garbage_never_raises():
    assert list(read_oculus_raw(b"")) == []
    records = list(read_oculus_raw(b"SO but not a message" * 3))
    assert all(isinstance(r, MalformedRecord) for r in records)


# ---------------------------------------------------------------------------
# real sample validation (Parnum CC0 survey; liboculus BSD capture)
# ---------------------------------------------------------------------------

_SAMPLE = os.environ.get("OCULUS_SAMPLE", "")
_RAW_SAMPLE = os.environ.get("OCULUS_RAW_SAMPLE", "")


@pytest.mark.skipif(not (_SAMPLE and os.path.exists(_SAMPLE)),
                    reason="OCULUS_SAMPLE not set or file missing")
def test_real_sample_statistics():
    """Statistics measured 2026-08-31 from Oculus_20210916_105445.oculus
    (14,734,024 bytes; CC0, Parnum et al. 2024; source URL in
    docs/FORMAT-SOURCES.md anchor S13).

    A dual-frequency unit (srcDeviceId 19125) pinging its
    high-frequency mode at 1.197 MHz: 151 version 2 simple ping
    results and nothing else, the item chain consuming the file
    exactly. Ping ids run 6629 through 6779 without a gap. Every ping
    is 373 range lines by 256 beams of 8-bit samples at 20.085 mm
    resolution (7.49 m imaged range against the 7.5 m demand), with a
    monotonic bearing table spanning exactly 130 degrees. The sonar
    clock ticks 66.7 ms per ping (a 15 Hz rate) and agrees with the
    item timestamps, which span 9.994 s. Temperature and pressure are
    physically plausible (a warm pool at the surface), the demanded
    sound speed equals the applied one, and the demanded-rate and
    network-speed bytes carry 0xA5 fill, as does the first reserved
    fire word (anchor errata).
    """
    bundle = load_imaging(_SAMPLE)
    counters = bundle.counters
    assert counters.items == 151
    assert counters.pings == 151
    assert counters.malformed == 0
    assert counters.bytes_skipped == 0
    assert counters.skipped_item_types == ()
    assert counters.unknown_message_ids == ()
    assert counters.compressed_items == 0

    header = bundle.file_header
    assert header is not None
    assert header.source_text == "Oculus"
    assert header.version == 1
    assert header.encryption == 0
    assert header.key == 0
    assert header.size_header == 48
    assert header.time == pytest.approx(1_631_760_885.031, abs=0.001)

    pings = bundle.pings
    assert [p.ping_id for p in pings] == list(range(6629, 6780))
    for ping in pings:
        assert ping.message_version == 2
        assert ping.src_device_id == 19125
        assert ping.master_mode == 2
        assert ping.is_high_frequency is True
        assert ping.flags == 0x19
        assert ping.range_is_meters is True
        assert ping.sends_gain is False
        assert ping.is_simple_return is True
        assert ping.gain_assistance is True
        assert ping.frequency_hz == pytest.approx(1_196_808.5, abs=1.0)
        assert ping.range_setting == pytest.approx(7.5)
        assert ping.gain_percent == pytest.approx(92.0)
        assert ping.salinity_ppt == pytest.approx(0.0)
        assert ping.speed_of_sound_mps == pytest.approx(1500.379, abs=0.001)
        assert ping.speed_of_sound_used_mps == ping.speed_of_sound_mps
        assert 26.5 < ping.temperature_c < 27.0
        assert 0.0 < ping.pressure_bar < 0.1
        assert ping.data_size == 0
        assert ping.sample_size == 1
        assert (ping.n_ranges, ping.n_beams) == (373, 256)
        assert ping.range_resolution_m == pytest.approx(0.0200851, abs=1e-6)
        assert ping.range_m == pytest.approx(7.4917, abs=0.001)
        assert ping.image_offset == 2048
        assert ping.image_size == 373 * 256
        assert ping.message_size == 2048 + 373 * 256
        assert ping.bearings_raw[0] == -6500
        assert ping.bearings_raw[-1] == 6500
        assert ping.aperture_deg == pytest.approx(130.0)
        assert all(b > a for a, b in
                   zip(ping.bearings_raw, ping.bearings_raw[1:],
                       strict=False))
        assert ping.gains is None
        assert len(ping.samples) == 373 * 256
        assert ping.ping_rate_raw == 0xA5
        assert ping.network_speed_raw == 0xA5
        assert ping.fire_reserved[0] == 0xA5A5A5A5
        assert ping.ext_flags == 0
        assert 56.0 < ping.heading_deg < 78.0
        assert 7.0 < ping.pitch_deg < 10.0
        assert -7.0 < ping.roll_deg < -4.0

    log_times = [p.log_time for p in pings]
    assert all(b >= a for a, b in zip(log_times, log_times[1:], strict=False))
    assert log_times[-1] - log_times[0] == pytest.approx(9.994, abs=0.001)
    starts = [p.ping_start_time_s for p in pings]
    assert all(b > a for a, b in zip(starts, starts[1:], strict=False))
    assert (starts[-1] - starts[0]) / 150 == pytest.approx(1 / 15, abs=0.001)

    mean = sum(pings[0].samples) / len(pings[0].samples)
    assert mean == pytest.approx(7.98, abs=0.01)


@pytest.mark.skipif(not (_RAW_SAMPLE and os.path.exists(_RAW_SAMPLE)),
                    reason="OCULUS_RAW_SAMPLE not set or file missing")
def test_real_raw_stream_statistics():
    """Statistics measured 2026-08-31 from liboculus's
    three_pings_8bit.raw (546,048 bytes, BSD-3-Clause; anchor S13).

    Three consecutive version 1 simple ping results (msgVersion 0 on
    the wire, the reason the layout is keyed on "2 or not"): ping ids
    415323 through 415325, an M1200d-class unit (srcDeviceId 7892) in
    its high-frequency mode at 2.099 MHz, 703 range lines by 256
    beams at 2.842 mm resolution reproducing the 2.0 m range demand,
    bearings spanning exactly 60 degrees. The applied sound speed
    equals the fire echo's 1490.66 m/s. The temperature and pressure
    doubles carry garbage bit patterns and the ping start word ticks
    62.50 million counts per ping, junk under the one published
    float-seconds reading (anchor errata); image energy is strong in
    every ping.
    """
    records = list(read_oculus_raw(_RAW_SAMPLE))
    assert len(records) == 3
    assert all(isinstance(r, OculusPing) for r in records)
    assert [p.ping_id for p in records] == [415323, 415324, 415325]
    for ping in records:
        assert ping.message_version == 0
        assert ping.src_device_id == 7892
        assert ping.log_time is None
        assert ping.master_mode == 2
        assert ping.flags == 0x19
        assert ping.ping_rate_raw == 0xC3
        assert ping.network_speed_raw == 25
        assert ping.gamma_correction == 127
        assert ping.range_setting == pytest.approx(2.0)
        assert ping.gain_percent == pytest.approx(50.0)
        assert ping.salinity_ppt == pytest.approx(0.0)
        assert ping.speed_of_sound_mps == pytest.approx(1490.659, abs=0.001)
        assert ping.speed_of_sound_used_mps == ping.speed_of_sound_mps
        assert ping.frequency_hz == pytest.approx(2_098_881, abs=1.0)
        assert ping.data_size == 0
        assert (ping.n_ranges, ping.n_beams) == (703, 256)
        assert ping.range_resolution_m == pytest.approx(0.0028422, abs=1e-6)
        assert ping.range_m == pytest.approx(1.998, abs=0.001)
        assert ping.image_offset == 2048
        assert ping.image_size == 703 * 256
        assert ping.message_size == 182_016
        assert ping.bearings_raw[0] == -3000
        assert ping.bearings_raw[-1] == 3000
        assert ping.aperture_deg == pytest.approx(60.0)
        assert ping.heading_deg is None
        assert ping.ping_start_time_s is None
        # Garbage sensor words, kept verbatim (anchor errata): the
        # temperature is a denormal-range speck, the pressure an
        # astronomically negative double.
        assert 0.0 < ping.temperature_c < 1e-9
        assert ping.pressure_bar < -1e100
        mean = sum(ping.samples) / len(ping.samples)
        assert 55.0 < mean < 57.0

    words = [p.ping_start_word for p in records]
    assert [b - a for a, b in zip(words, words[1:], strict=False)] == \
        [62_502_130, 62_502_097]
