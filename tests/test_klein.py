"""Klein SDF dialect: page framing, header decoding, channel walking,
survey loading.

Fixtures are synthetic bytes assembled by hand from the typedef and
tables of the Klein SDF data page specification, document 15300018
Rev 2.05 (builders in klein_builders.py; citations in
hydroformats/klein.py); all values are fictional. The real-sample
integration test at the bottom runs only when KLEIN_SAMPLE points at a
real SDF file (see docs/FORMAT-SOURCES.md anchor S14 for a public
source).
"""
import math
import os
import struct

import pytest
from klein_builders import channel, header, page, ping_3001, put, stream

from hydroformats.klein import (
    CHANNEL_PLANS,
    Klein7000Page,
    KleinPing,
    iter_pages,
    load_survey,
    read_klein,
    towfish_name,
)
from hydroformats.klein_records import (
    BASE_FIELDS,
    BASE_HEADER_SIZE,
    FREQUENCY_3500_OFFSET,
    V3_FIELDS,
    V3_HEADER_SIZE,
    V4_FIELDS,
    V4_HEADER_SIZE,
)
from hydroformats.records import MalformedRecord

# ---------------------------------------------------------------------------
# layout tables
# ---------------------------------------------------------------------------


def test_marker_and_endianness_pinned():
    built = ping_3001()
    assert built[:4] == b"\xff\xff\xff\xff"          # ping marker, spec sect 3
    number_bytes = struct.unpack_from("<I", built, 4)[0]
    assert number_bytes == len(built) - 4            # marker excluded
    assert built[8:12] == bytes((0xB9, 0x0B, 0, 0))  # 3001 little endian


def test_header_field_tables_are_contiguous():
    """The typedef is packed: each field starts where the last ended,
    and the documented 176/256/512 byte boundaries fall out exactly."""
    position = 0
    for name, offset, fmt in BASE_FIELDS + V3_FIELDS + V4_FIELDS:
        assert offset == position, name
        position += struct.calcsize(fmt)
    assert BASE_FIELDS[-1][1] + 8 == BASE_HEADER_SIZE == 176
    assert V3_FIELDS[-1][1] + 4 == V3_HEADER_SIZE == 256
    assert V4_FIELDS[-1][1] + 4 == 336            # reserved3[44] pads to 512
    assert V4_HEADER_SIZE == 336 + 44 * 4 == 512


def test_doubles_are_eight_aligned():
    for name, offset, fmt in BASE_FIELDS + V3_FIELDS + V4_FIELDS:
        if fmt == "<d":
            assert offset % 8 == 0, name


def test_docstring_layout_sizes():
    assert len(header(page_version=3000)) == 256
    assert len(header(page_version=5000)) == 256
    assert len(header(page_version=3001)) == 512
    assert len(header(page_version=3501)) == 512
    assert len(channel(1, 2, 3)) == 2 + 6
    assert len(channel(1, 2, count_width=4, sample_width=4)) == 4 + 8


# ---------------------------------------------------------------------------
# header decoding
# ---------------------------------------------------------------------------


def test_header_roundtrip_version_4():
    (ping,) = read_klein(ping_3001())
    assert isinstance(ping, KleinPing)
    assert ping.page_version == 3001
    assert ping.towfish == "System 3000"
    assert ping.header_version == 4
    assert ping.configuration == 0x1F
    assert ping.ping_number == 4242
    assert ping.num_samples == 4
    assert ping.beams_to_display == 0x3FF
    assert ping.error_flags == 0
    assert ping.range_m == 75
    assert ping.speed_fish_cms == 250
    assert ping.speed_sound_cms == 150000
    assert ping.res_mode == 1
    assert ping.tx_waveform == 0x0102
    assert ping.resp_div == 5
    assert ping.resp_freq == 7
    assert ping.manual_speed_switch == 1
    assert ping.despeckle_switch == 2
    assert ping.speed_filter_switch == 1
    assert (ping.year, ping.month, ping.day) == (2008, 4, 15)
    assert (ping.hour, ping.minute, ping.second, ping.h_second) == \
        (13, 45, 30, 25)
    assert (ping.fix_time_hour, ping.fix_time_minute) == (13, 44)
    assert ping.fix_time_second == pytest.approx(58.5)
    assert ping.heading_degrees == pytest.approx(87.5)
    assert ping.pitch_degrees == pytest.approx(1.5)
    assert ping.roll_degrees == pytest.approx(-0.75)
    assert ping.depth_m == pytest.approx(12.25)
    assert ping.altitude_m == pytest.approx(9.5)
    assert ping.temperature_c == pytest.approx(14.5)
    assert ping.ship_speed_mps == pytest.approx(2.25)
    assert ping.ship_heading_degrees == pytest.approx(91.25)
    assert ping.magnetic_variation_degrees == pytest.approx(-14.5)
    assert ping.ship_lat_radians == pytest.approx(0.77)
    assert ping.ship_lon_radians == pytest.approx(-1.11)
    assert ping.fish_lat_radians == pytest.approx(0.775)
    assert ping.fish_lon_radians == pytest.approx(-1.115)
    assert ping.tvg_page == 3
    assert ping.header_size == 512
    assert (ping.fix_time_year, ping.fix_time_month, ping.fix_time_day) == \
        (2008, 4, 15)
    assert ping.aux_pitch_degrees == pytest.approx(0.5)
    assert ping.aux_roll_degrees == pytest.approx(-0.25)
    assert ping.aux_depth_m == pytest.approx(11.75)
    assert ping.aux_altitude_m == pytest.approx(10.25)
    assert ping.cable_out_m == pytest.approx(150.5)
    assert ping.fseconds == pytest.approx(0.31)
    assert ping.altimeter == 1
    assert ping.sample_freq_hz == 20000
    assert ping.depressor_type == 1
    assert ping.cable_type == 3
    assert ping.shieve_x_off_m == pytest.approx(1.5)
    assert ping.shieve_y_off_m == pytest.approx(-2.5)
    assert ping.shieve_z_off_m == pytest.approx(0.75)
    assert ping.gps_height_m == pytest.approx(3.25)
    assert ping.raw_data_config == 0x00030003
    assert ping.header3_extension_size == 256
    assert ping.sbp_tx_waveform == 2
    assert ping.sbp_preamp_gain == 1
    assert ping.sbp_data_raw == 0
    assert ping.sbp_num_samples == 3
    assert ping.sbp_sample_freq_hz == 10000
    assert ping.sbp_tx_waveform_version == 4
    assert ping.wing_angle_degrees == pytest.approx(5.5)
    assert ping.emergency_switch_state == 1
    assert ping.layback_method == 0
    assert ping.layback_fish_lat_radians == pytest.approx(0.78)
    assert ping.layback_fish_lon_radians == pytest.approx(-1.12)
    assert ping.fish_heading_offset_degrees == pytest.approx(2.5)
    assert ping.pressure_sensor_offset_psi == pytest.approx(1.25)
    assert ping.tpu_sw_version == 0x06160401
    assert ping.capability_mask == 0x2C
    assert ping.tx_version == 1
    assert ping.num_samples_extra == 16
    assert ping.center_frequency_khz is None       # not a 3500-series page
    assert len(ping.header_bytes) == 512


def test_version_3_page_has_no_version_4_fields():
    built = page(header(page_version=3000),
                 channel(1), channel(2), channel(3), channel(4),
                 channel(count_width=4, sample_width=2, signed=True))
    (ping,) = read_klein(built)
    assert ping.page_version == 3000
    assert ping.header_version == 3
    assert ping.header_size == 256
    assert len(ping.header_bytes) == 256
    assert ping.tpu_sw_version is None
    assert ping.wing_angle_degrees is None
    assert ping.layback_fish_lat_radians is None
    assert ping.layback_fish_lat_degrees is None
    assert ping.num_samples_extra is None
    assert ping.center_frequency_khz is None
    assert [c.name for c in ping.channels] == \
        ["port_lf", "stbd_lf", "port_hf", "stbd_hf", "sbp"]


def test_unit_conversions_hand_computed():
    (ping,) = read_klein(ping_3001())
    assert ping.time_of_day == pytest.approx(13 * 3600 + 45 * 60 + 30.31)
    assert ping.speed_fish_mps == pytest.approx(2.5)      # 250 cm/s
    assert ping.speed_sound_mps == pytest.approx(1500.0)  # 150000 cm/s
    assert ping.ship_lat_degrees == pytest.approx(math.degrees(0.77))
    assert ping.ship_lon_degrees == pytest.approx(math.degrees(-1.11))
    assert ping.fish_lat_degrees == pytest.approx(math.degrees(0.775))
    assert ping.fish_lon_degrees == pytest.approx(math.degrees(-1.115))
    assert ping.layback_fish_lat_degrees == pytest.approx(math.degrees(0.78))
    assert ping.layback_fish_lon_degrees == pytest.approx(math.degrees(-1.12))


def test_configuration_masks_use_and_not_equality():
    """Spec Table 2 masks cover both channels of a band; a single
    lit bit still means the band is present."""
    (full,) = read_klein(ping_3001(configuration=0x1F))
    assert full.lf_side_scan_present
    assert full.hf_side_scan_present
    assert full.sbp_present
    (one,) = read_klein(ping_3001(configuration=0x01))
    assert one.lf_side_scan_present
    assert not one.hf_side_scan_present
    assert not one.sbp_present
    (hf,) = read_klein(ping_3001(configuration=0x04))
    assert not hf.lf_side_scan_present
    assert hf.hf_side_scan_present


# ---------------------------------------------------------------------------
# channel walking
# ---------------------------------------------------------------------------


def test_3000_v4_channels_hand_computed():
    (ping,) = read_klein(ping_3001())
    port_lf = ping.channel("port_lf")
    assert port_lf is not None
    assert port_lf.count == 4
    assert port_lf.sample_width == 2
    assert port_lf.signed is False
    assert port_lf.values() == (10, 20, 30, 40)
    assert port_lf.sample_bytes == struct.pack("<4H", 10, 20, 30, 40)
    assert ping.channel("stbd_hf").values() == (13, 23, 33, 43)
    sbp = ping.channel("sbp")
    assert sbp.count == 3
    assert sbp.sample_width == 4                   # 32-bit signed at v4
    assert sbp.signed is True
    assert sbp.values() == (100, -200, 300)
    assert ping.absent_channels == ()
    assert ping.leftover == b""


def test_3000_v3_sbp_is_signed_sixteen_bit():
    built = page(header(page_version=3000),
                 channel(1), channel(2), channel(3), channel(4),
                 channel(-5, 6, count_width=4, sample_width=2, signed=True))
    (ping,) = read_klein(built)
    sbp = ping.channel("sbp")
    assert sbp.sample_width == 2
    assert sbp.signed is True
    assert sbp.values() == (-5, 6)


def test_sixteen_bit_samples_decode_little_endian_unsigned():
    built = page(header(page_version=3001),
                 struct.pack("<H", 3) + b"\x34\x12\xff\xff\x00\x80")
    (ping,) = read_klein(built)
    assert ping.channel("port_lf").values() == (0x1234, 0xFFFF, 0x8000)


def test_5000_plan_matches_the_typedef():
    plan = CHANNEL_PLANS[5000]
    assert plan is CHANNEL_PLANS[5001]
    assert len(plan) == 84                         # 10 + 12 + 6 + 56 arrays
    names = [spec.name for spec in plan]
    assert names[:10] == [f"chan{i}" for i in range(1, 11)]
    assert names[10:16] == ["bathy_port1i", "bathy_port1q", "bathy_port2i",
                            "bathy_port2q", "bathy_port3i", "bathy_port3q"]
    assert names[16:22] == ["bathy_stbd1i", "bathy_stbd1q", "bathy_stbd2i",
                            "bathy_stbd2q", "bathy_stbd3i", "bathy_stbd3q"]
    assert names[22:28] == ["echo1", "echo2", "sub_bottom1", "sub_bottom2",
                            "roll_sensor", "yaw_rate"]
    assert names[28] == "rawdata_port1i"
    assert names[-1] == "rawdata_stbd14q"
    assert all(spec.count_width == 2 for spec in plan)
    assert all(spec.sample_width == 2 for spec in plan)
    assert [spec.signed for spec in plan[:10]] == [False] * 10
    assert all(spec.signed for spec in plan[10:])


def test_5000_page_with_beams_only():
    built = page(header(page_version=5001),
                 *(channel(100 + i, 200 + i) for i in range(10)))
    (ping,) = read_klein(built)
    assert ping.towfish == "System 5000"
    assert [c.name for c in ping.channels] == \
        [f"chan{i}" for i in range(1, 11)]
    assert ping.channel("chan1").values() == (100, 200)
    assert ping.channel("chan10").values() == (109, 209)
    assert len(ping.absent_channels) == 74
    assert ping.absent_channels[0] == "bathy_port1i"
    assert ping.absent_channels[-1] == "rawdata_stbd14q"


def test_3500_page_uses_u32_counts_and_samples():
    built = page(header(page_version=3501, frequency_khz=455),
                 channel(7, 8, 9, count_width=4, sample_width=4),
                 channel(70000, 80000, count_width=4, sample_width=4))
    (ping,) = read_klein(built)
    assert ping.towfish == "3500 series"
    assert ping.header_version == 4
    assert ping.center_frequency_khz == 455
    port, stbd = ping.channels
    assert (port.name, stbd.name) == ("port", "starboard")
    assert port.count == 3
    assert port.sample_width == 4
    assert port.signed is False
    assert port.values() == (7, 8, 9)
    assert stbd.values() == (70000, 80000)         # above the 16-bit range


def test_3500_frequency_sits_in_the_2008_reserved_region():
    assert FREQUENCY_3500_OFFSET == 404
    assert 336 <= FREQUENCY_3500_OFFSET < 512


def test_7000_page_keeps_data_verbatim():
    built = page(header(page_version=7001), b"\xde\xad\xbe\xef")
    (record,) = read_klein(built)
    assert isinstance(record, Klein7000Page)
    assert record.towfish == "System 7000"
    assert record.ping_number == 4242
    assert record.data_bytes == b"\xde\xad\xbe\xef"
    assert not hasattr(record, "channels")         # deliberately not decoded


def test_zero_count_channel_is_empty_not_absent():
    built = page(header(page_version=3501),
                 channel(count_width=4, sample_width=4),
                 channel(count_width=4, sample_width=4))
    (ping,) = read_klein(built)
    assert [c.count for c in ping.channels] == [0, 0]
    assert ping.channel("port").values() == ()
    assert ping.absent_channels == ()


def test_missing_channels_are_absent_not_guessed():
    built = page(header(page_version=3001))       # header only, no data
    (ping,) = read_klein(built)
    assert ping.channels == ()
    assert ping.absent_channels == \
        ("port_lf", "stbd_lf", "port_hf", "stbd_hf", "sbp")


def test_leftover_bytes_are_carried_verbatim():
    built = page(header(page_version=3501),
                 channel(1, count_width=4, sample_width=4),
                 channel(2, count_width=4, sample_width=4),
                 b"\x09\x08\x07")
    (ping,) = read_klein(built)
    assert len(ping.channels) == 2
    assert ping.leftover == b"\x09\x08\x07"


def test_overrunning_sample_count_degrades_to_malformed():
    built = page(header(page_version=3001),
                 channel(1, 2, count=500))
    (record,) = read_klein(built)
    assert isinstance(record, MalformedRecord)
    assert record.tag == "PING"
    assert "port_lf" in record.error


def test_header_size_outside_valid_range_is_malformed():
    small = page(ping_3001()[4:][:512], number_bytes=512)
    body = bytearray(small)
    put(body, 4 + 180, "<I", 128)                  # below the v4 layout size
    (record,) = read_klein(bytes(body))
    assert isinstance(record, MalformedRecord)
    huge = bytearray(small)
    put(huge, 4 + 180, "<I", 4096)                 # beyond the page
    (record,) = read_klein(bytes(huge))
    assert isinstance(record, MalformedRecord)


def test_short_page_for_known_version_is_malformed():
    built = page(header(page_version=3001)[:200], number_bytes=200)
    (record,) = read_klein(built)
    assert isinstance(record, MalformedRecord)
    assert "512" in record.error


# ---------------------------------------------------------------------------
# page walking
# ---------------------------------------------------------------------------


def test_walker_skips_garbage_and_resynchronizes():
    data = stream(ping_3001(), b"\x01\x02\x03\x04", ping_3001(ping_number=4243))
    survey = load_survey(data)
    assert [p.ping_number for p in survey.pings] == [4242, 4243]
    assert survey.counters.pages == 2
    assert survey.counters.bytes_skipped == 4


def test_walker_counts_truncated_final_page():
    cut = ping_3001()[:100]
    survey = load_survey(stream(ping_3001(), cut))
    assert len(survey.pings) == 1
    assert survey.counters.bytes_skipped == 100


def test_walker_survives_insane_declared_size():
    bad = bytearray(ping_3001())
    put(bad, 4, "<I", 0xFFFF0000)                  # size beyond the file
    data = stream(bytes(bad), ping_3001(ping_number=4243))
    survey = load_survey(data)
    assert [p.ping_number for p in survey.pings] == [4243]
    assert survey.counters.bytes_skipped == len(bad)


def test_file_without_markers_degrades_to_one_gap():
    naked = ping_3001()[4:]                        # a marker-less TPU capture
    survey = load_survey(naked)
    assert survey.pings == ()
    assert survey.counters.pages == 0
    assert survey.counters.bytes_skipped == len(naked)


def test_unknown_page_versions_are_counted_and_skipped():
    nav = page(header(page_version=2020), b"\x00" * 8)
    data = stream(nav, ping_3001(), nav)
    survey = load_survey(data)
    assert len(survey.pings) == 1
    assert survey.counters.pages == 3
    assert survey.counters.unknown_page_versions == ((2020, 2),)
    records = list(read_klein(data))
    assert len(records) == 1                       # unknown pages stay silent


def test_towfish_names():
    assert towfish_name(3000) == towfish_name(3001) == "System 3000"
    assert towfish_name(5000) == towfish_name(5001) == "System 5000"
    assert towfish_name(7000) == towfish_name(7001) == "System 7000"
    assert towfish_name(3501) == towfish_name(3502) == "3500 series"
    assert towfish_name(2020) is None


def test_iter_pages_reports_offsets():
    first = ping_3001()
    events = list(iter_pages(stream(first, b"\xee", ping_3001())))
    assert events[0].offset == 0
    assert events[0].number_bytes == len(first) - 4
    assert events[1].size == 1                     # the gap byte
    assert events[2].offset == len(first) + 1


# ---------------------------------------------------------------------------
# survey loading
# ---------------------------------------------------------------------------


def test_load_survey_bundles_series_and_counters():
    data = stream(
        ping_3001(),
        page(header(page_version=7000), b"\x01\x02"),
        page(header(page_version=2020), b"\x00" * 4),
        ping_3001(ping_number=4243),
    )
    survey = load_survey(data)
    assert [p.ping_number for p in survey.pings] == [4242, 4243]
    assert len(survey.system_7000) == 1
    assert survey.counters.pages == 4
    assert survey.counters.unknown_page_versions == ((2020, 1),)
    assert survey.counters.malformed == 0
    assert survey.counters.bytes_skipped == 0


def test_load_survey_counts_malformed_pages():
    bad = page(header(page_version=3001), channel(1, count=999))
    survey = load_survey(stream(ping_3001(), bad))
    assert len(survey.pings) == 1
    assert survey.counters.malformed == 1
    assert survey.counters.pages == 2


def test_channel_series_pairs_data_with_pings():
    data = stream(ping_3001(), ping_3001(ping_number=4243))
    survey = load_survey(data)
    series = survey.channel_series()
    assert [s.name for s in series] == \
        ["port_lf", "stbd_lf", "port_hf", "stbd_hf", "sbp"]
    port = series[0]
    assert [p.ping_number for p in port.pings] == [4242, 4243]
    assert [c.values() for c in port.data] == [(10, 20, 30, 40)] * 2


def test_read_klein_yields_records_in_file_order():
    data = stream(ping_3001(),
                  page(header(page_version=7001), b"\x00"),
                  ping_3001(ping_number=4243))
    kinds = [type(r).__name__ for r in read_klein(data)]
    assert kinds == ["KleinPing", "Klein7000Page", "KleinPing"]


def test_non_sdf_bytes_degrade_to_gaps():
    survey = load_survey(b"GSF-v03.09 definitely not sdf" * 40)
    assert survey.pings == ()
    assert survey.counters.pages == 0
    assert survey.counters.bytes_skipped == 29 * 40


# ---------------------------------------------------------------------------
# real sample validation
# ---------------------------------------------------------------------------

_SAMPLE = os.environ.get("KLEIN_SAMPLE", "")


@pytest.mark.skipif(not (_SAMPLE and os.path.exists(_SAMPLE)),
                    reason="KLEIN_SAMPLE not set or file missing")
def test_real_sample_statistics():
    """Statistics from a real, publicly archived SDF file; provenance
    in docs/FORMAT-SOURCES.md anchor S14. Pinned once a lawful sample
    is in hand."""
    survey = load_survey(_SAMPLE)
    counters = survey.counters
    assert counters.bytes_skipped == 0
    assert counters.malformed == 0
    assert len(survey.pings) > 0
