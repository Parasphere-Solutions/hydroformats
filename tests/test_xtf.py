"""Triton XTF dialect: file header, packet walking, decoding, survey loading.

Fixtures are synthetic bytes assembled by hand from the tables of the
Triton XTF specification, revision 41 (builders in xtf_builders.py;
citation in hydroformats/xtf.py); all values are fictional. The real-
sample integration test at the bottom runs only when XTF_SAMPLE points
at a real XTF file (see docs/FORMAT-SOURCES.md anchor S10 for a public
source).
"""
import os
import struct

import pytest
from xtf_builders import (
    TWO_CHANNELS,
    attitude_packet,
    bathy_packet,
    chan_header,
    chan_info,
    file_header,
    notes_packet,
    ping_header,
    put,
    raw_serial_packet,
    snippet_packet,
    snp0,
    snp1,
    sonar_packet,
    stream,
    unknown_packet,
)

from hydroformats.records import MalformedRecord
from hydroformats.xtf import (
    XtfAttitude,
    XtfBathySnippet,
    XtfFileHeader,
    XtfNotes,
    XtfRawBathy,
    XtfRawSerial,
    XtfSonarPing,
    load_survey,
    read_xtf,
)

# ---------------------------------------------------------------------------
# file header
# ---------------------------------------------------------------------------


def test_file_header_magic_and_endianness_pinned():
    built = file_header(*TWO_CHANNELS)
    assert built[0] == 123                       # FileFormat, spec Table C
    assert built[164:170] == bytes((3, 0, 2, 0, 0, 0))  # u16 LE nav/counts
    packet = attitude_packet()
    assert packet[:2] == b"\xce\xfa"             # 0xFACE little endian
    assert packet[10:14] == bytes((64, 0, 0, 0))  # u32 LE NumBytesThisRecord


def test_file_header_roundtrip():
    records = list(read_xtf(file_header(*TWO_CHANNELS)))
    (header,) = records
    assert isinstance(header, XtfFileHeader)
    assert header.file_format == 123
    assert header.system_type == 1
    assert header.recording_program_name == "Isis"
    assert header.recording_program_version == "556"
    assert header.sonar_name == "C31_SERV"
    assert header.sonar_type == 57
    assert header.note_string == "synthetic reef line"
    assert header.this_file_name == "LINE12-B.XTF"
    assert header.nav_units == 3
    assert header.nav_units_name == "degrees"
    assert header.num_sonar_channels == 2
    assert header.num_bathymetry_channels == 0
    assert header.reference_point_height == pytest.approx(1.25)
    assert header.navigation_latency_ms == 120
    assert header.nav_offset_y == pytest.approx(-1.5)
    assert header.nav_offset_x == pytest.approx(2.5)
    assert header.mru_offset_pitch == pytest.approx(0.5)
    assert header.mru_offset_roll == pytest.approx(-0.5)
    assert header.header_size == 1024


def test_channel_info_roundtrip():
    (header,) = read_xtf(file_header(*TWO_CHANNELS))
    port, stbd = header.channels
    assert port.index == 0
    assert port.type_of_channel == 1
    assert port.type_name == "port"
    assert port.sub_channel_number == 0
    assert port.correction_flags == 1
    assert port.slant_range_corrected is True
    assert port.ground_range_corrected is False
    assert port.unipolar == 1
    assert port.bytes_per_sample == 1
    assert port.name == "Port 500"
    assert port.volt_scale == pytest.approx(5.0)
    assert port.frequency == pytest.approx(500.0)
    assert port.horizontal_beam_angle == pytest.approx(1.0)
    assert port.tilt_angle == pytest.approx(30.0)
    assert port.beam_width == pytest.approx(50.0)
    assert port.offset_x == pytest.approx(0.25)
    assert port.offset_y == pytest.approx(-1.5)
    assert port.offset_z == pytest.approx(0.75)
    assert port.offset_pitch == pytest.approx(-2.0)
    assert port.offset_roll == pytest.approx(1.0)
    assert port.sample_format == 0
    assert stbd.type_name == "starboard"
    assert stbd.sub_channel_number == 1


def test_file_header_grows_for_more_than_six_channels():
    infos = tuple(chan_info(sub_channel=i, name=b"Chan %d" % i)
                  for i in range(8))
    built = file_header(*infos)
    assert len(built) == 2048
    data = stream(built, attitude_packet())
    survey = load_survey(data)
    assert survey.header is not None
    assert survey.header.header_size == 2048
    assert len(survey.header.channels) == 8
    assert [c.name for c in survey.header.channels] == \
        [f"Chan {i}" for i in range(8)]
    assert len(survey.attitude) == 1          # packet found after 2048 bytes
    assert survey.counters.bytes_skipped == 0


def test_non_xtf_bytes_degrade_to_malformed():
    records = list(read_xtf(b"GSF-v03.09 not an xtf file"))
    assert len(records) == 1
    assert isinstance(records[0], MalformedRecord)
    survey = load_survey(b"\x00" * 4096)
    assert survey.header is None
    assert survey.counters.bytes_skipped == 4096


def test_truncated_file_header_degrades():
    survey = load_survey(file_header(*TWO_CHANNELS)[:512])
    assert survey.header is None
    assert survey.counters.bytes_skipped == 512


# ---------------------------------------------------------------------------
# packet walking
# ---------------------------------------------------------------------------


def test_walker_skips_garbage_and_resynchronizes():
    data = stream(file_header(*TWO_CHANNELS), attitude_packet(),
                  b"\x01\x02\x03\x04", notes_packet())
    survey = load_survey(data)
    assert len(survey.attitude) == 1
    assert len(survey.notes) == 1
    assert survey.counters.packets == 2
    assert survey.counters.bytes_skipped == 4


def test_walker_counts_truncated_final_packet():
    cut = attitude_packet()[:40]
    data = stream(file_header(*TWO_CHANNELS), notes_packet(), cut)
    survey = load_survey(data)
    assert len(survey.notes) == 1
    assert survey.counters.bytes_skipped == 40


def test_walker_survives_insane_declared_size():
    bad = bytearray(attitude_packet())
    put(bad, 10, "<I", 4)                      # size smaller than the prefix
    data = stream(file_header(*TWO_CHANNELS), bytes(bad), notes_packet())
    survey = load_survey(data)
    assert len(survey.notes) == 1
    assert survey.counters.bytes_skipped == len(bad)


def test_unknown_packet_types_are_counted_and_skipped():
    data = stream(file_header(*TWO_CHANNELS), unknown_packet(200),
                  unknown_packet(200), unknown_packet(42))
    survey = load_survey(data)
    assert survey.counters.packets == 3
    assert survey.counters.unknown_header_types == ((42, 1), (200, 2))
    records = list(read_xtf(data))
    assert len(records) == 1                   # just the file header


# ---------------------------------------------------------------------------
# sonar ping decoding
# ---------------------------------------------------------------------------


def _survey(*packets: bytes, channels: tuple = TWO_CHANNELS):
    return load_survey(stream(file_header(*channels), *packets))


def test_sonar_ping_header_roundtrip():
    survey = _survey(sonar_packet(chan_header(num_samples=4) + b"\x00" * 4))
    (ping,) = survey.pings
    assert isinstance(ping, XtfSonarPing)
    assert (ping.year, ping.month, ping.day) == (2016, 9, 16)
    assert (ping.hour, ping.minute, ping.second, ping.hseconds) == \
        (13, 45, 30, 25)
    assert ping.time_of_day == pytest.approx(13 * 3600 + 45 * 60 + 30.25)
    assert ping.julian_day == 260
    assert ping.event_number == 7
    assert ping.ping_number == 12345
    assert ping.sound_velocity_mps == pytest.approx(750.0)
    assert ping.ocean_tide_m == pytest.approx(0.4)
    assert ping.water_temperature_c == pytest.approx(12.5)
    assert ping.computed_sound_velocity_mps == pytest.approx(1481.0)
    assert ping.ship_speed_knots == pytest.approx(4.2)
    assert ping.ship_gyro_degrees == pytest.approx(87.5)
    assert ping.ship_altitude_m == pytest.approx(1.5)     # 15 decimeters
    assert ping.ship_depth_m == pytest.approx(3.2)        # 32 decimeters
    assert ping.sensor_speed_knots == pytest.approx(3.9)
    assert ping.range_to_fish_m == pytest.approx(15.0)    # 150 decimeters
    assert ping.bearing_to_fish_degrees == pytest.approx(180.5)
    assert ping.cable_out_m == pytest.approx(42.25)       # word + hundredths
    assert ping.sensor_depth_m == pytest.approx(2.5)
    assert ping.sensor_primary_altitude_m == pytest.approx(14.75)
    assert ping.sensor_aux_altitude_m == pytest.approx(15.0)
    assert ping.sensor_pitch_degrees == pytest.approx(1.5)
    assert ping.sensor_roll_degrees == pytest.approx(-0.75)
    assert ping.sensor_heading_degrees == pytest.approx(88.25)
    assert ping.heave_m == pytest.approx(0.12)
    assert ping.yaw_degrees == pytest.approx(0.5)
    assert ping.attitude_time_tag_ms == 123456
    assert ping.aux_values == pytest.approx((1.0, 2.0, 3.0, 4.0, 5.0, 6.0))
    assert ping.fish_position_delta_x_m == pytest.approx(-100.0)  # raw / 3
    assert ping.fish_position_delta_y_m == pytest.approx(50.0)


def test_nav_duplication_is_surfaced_not_resolved():
    """XTF carries ship and sensor positions plus a layback; which one to
    georeference against is the consumer's call, so all are surfaced."""
    survey = _survey(sonar_packet(chan_header(num_samples=0)))
    (ping,) = survey.pings
    assert ping.ship_y == pytest.approx(44.6512345)
    assert ping.ship_x == pytest.approx(-63.5734567)
    assert ping.sensor_y == pytest.approx(44.6512001)
    assert ping.sensor_x == pytest.approx(-63.5735002)
    assert ping.sensor_y != ping.ship_y
    assert ping.layback_m == pytest.approx(38.5)
    assert not hasattr(ping, "latitude")       # no blessed position field


def test_channel_header_roundtrip():
    survey = _survey(sonar_packet(chan_header(num_samples=4) + b"\x00" * 4))
    (channel,) = survey.pings[0].channels
    assert channel.channel_number == 0
    assert channel.downsample_method == 4
    assert channel.slant_range_m == pytest.approx(50.0)
    assert channel.ground_range_m == pytest.approx(48.0)
    assert channel.time_delay_s == pytest.approx(0.0)
    assert channel.time_duration_s == pytest.approx(0.0667)
    assert channel.seconds_per_ping_s == pytest.approx(0.0667)
    assert channel.frequency_khz == 500
    assert channel.initial_gain_code == 12
    assert channel.gain_code == 20
    assert channel.num_samples == 4
    assert channel.fixed_vsop_cm == pytest.approx(10.5)
    assert channel.weight_factor == 2
    assert channel.bytes_per_sample == 1
    assert channel.sample_format == 0


def test_eight_bit_samples_hand_computed():
    data = bytes((0, 1, 127, 128, 255))
    survey = _survey(sonar_packet(chan_header(num_samples=5) + data))
    (channel,) = survey.pings[0].channels
    assert channel.sample_bytes == data
    assert channel.values() == (0, 1, 127, 128, 255)      # unipolar: unsigned
    assert channel.values(signed=True) == (0, 1, 127, -128, -1)


def test_sixteen_bit_samples_hand_computed():
    channels = (chan_info(bytes_per_sample=2),)
    data = b"\x34\x12\xff\xff\x00\x80"
    survey = _survey(sonar_packet(chan_header(num_samples=3) + data),
                     channels=channels)
    (channel,) = survey.pings[0].channels
    assert channel.bytes_per_sample == 2
    assert channel.values() == (0x1234, 0xFFFF, 0x8000)   # little endian
    assert channel.values(signed=True) == (0x1234, -1, -32768)


def test_polar_channel_defaults_to_signed_decode():
    channels = (chan_info(unipolar=0),)
    survey = _survey(sonar_packet(chan_header(num_samples=2) + b"\xff\x7f"),
                     channels=channels)
    (channel,) = survey.pings[0].channels
    assert channel.unipolar == 0
    assert channel.values() == (-1, 127)


def test_thirty_two_bit_samples_via_sample_format():
    channels = (chan_info(bytes_per_sample=4, sample_format=2),)
    data = b"\x78\x56\x34\x12"
    survey = _survey(sonar_packet(chan_header(num_samples=1) + data),
                     channels=channels)
    (channel,) = survey.pings[0].channels
    assert channel.values() == (0x12345678,)


def test_ieee_float_samples_via_sample_format():
    channels = (chan_info(bytes_per_sample=4, sample_format=5),)
    data = struct.pack("<2f", 1.5, -0.25)
    survey = _survey(sonar_packet(chan_header(num_samples=2) + data),
                     channels=channels)
    (channel,) = survey.pings[0].channels
    assert channel.values() == pytest.approx((1.5, -0.25))


def test_ibm_float_samples_stay_raw():
    channels = (chan_info(bytes_per_sample=4, sample_format=1),)
    data = b"\x41\x10\x00\x00"
    survey = _survey(sonar_packet(chan_header(num_samples=1) + data),
                     channels=channels)
    (channel,) = survey.pings[0].channels
    assert channel.values() is None
    assert channel.sample_bytes == data


def test_multi_channel_ordering_and_mixed_widths():
    channels = (chan_info(type_of_channel=1, bytes_per_sample=1),
                chan_info(type_of_channel=2, sub_channel=1,
                          bytes_per_sample=2, name=b"Stbd 500"))
    packet = sonar_packet(
        chan_header(channel_number=0, num_samples=3) + bytes((10, 20, 30)),
        chan_header(channel_number=1, num_samples=2) + b"\x01\x00\x02\x00",
    )
    survey = _survey(packet, channels=channels)
    first, second = survey.pings[0].channels
    assert first.channel_number == 0
    assert first.values() == (10, 20, 30)
    assert second.channel_number == 1
    assert second.values() == (1, 2)


def test_trailing_pad_bytes_are_tolerated():
    survey = _survey(sonar_packet(chan_header(num_samples=2) + b"\x05\x06",
                                  pad=30))
    (channel,) = survey.pings[0].channels
    assert channel.values() == (5, 6)


def test_channel_without_info_is_skipped_and_reported():
    packet = sonar_packet(
        chan_header(channel_number=9, num_samples=2) + b"\x05\x06")
    survey = _survey(packet)
    (ping,) = survey.pings
    assert ping.channels == ()
    assert ping.skipped_channels == ((9, 2),)


def test_overrunning_sample_count_degrades_to_malformed():
    packet = sonar_packet(chan_header(num_samples=500) + b"\x05\x06")
    data = stream(file_header(*TWO_CHANNELS), packet)
    records = list(read_xtf(data))
    assert isinstance(records[1], MalformedRecord)
    assert records[1].tag == "PING"


def test_per_channel_series_pairs_data_with_metadata():
    packet_one = sonar_packet(
        chan_header(channel_number=0, num_samples=1) + b"\x11",
        chan_header(channel_number=1, num_samples=1) + b"\x22",
    )
    packet_two = sonar_packet(
        chan_header(channel_number=0, num_samples=1) + b"\x33",
        chan_header(channel_number=1, num_samples=1) + b"\x44",
        ping_number=12346,
    )
    survey = _survey(packet_one, packet_two)
    port, stbd = survey.channel_series()
    assert port.channel_number == 0
    assert port.info is not None
    assert port.info.name == "Port 500"
    assert [p.ping_number for p in port.pings] == [12345, 12346]
    assert [c.sample_bytes for c in port.data] == [b"\x11", b"\x33"]
    assert stbd.info.name == "Stbd 500"
    assert [c.sample_bytes for c in stbd.data] == [b"\x22", b"\x44"]


# ---------------------------------------------------------------------------
# attitude, notes, raw serial
# ---------------------------------------------------------------------------


def test_attitude_roundtrip():
    survey = _survey(attitude_packet())
    (att,) = survey.attitude
    assert isinstance(att, XtfAttitude)
    assert att.pitch_degrees == pytest.approx(1.25)
    assert att.roll_degrees == pytest.approx(-2.5)
    assert att.heave_m == pytest.approx(0.31)
    assert att.yaw_degrees == pytest.approx(0.75)
    assert att.heading_degrees == pytest.approx(91.5)
    assert att.time_tag_ms == 98765
    assert att.source_epoch == 1474033530
    assert att.epoch_microseconds == 250000
    assert att.source_time == pytest.approx(1474033530.25)
    assert (att.year, att.month, att.day) == (2016, 9, 16)
    assert att.milliseconds == 250
    assert att.time_of_day == pytest.approx(13 * 3600 + 45 * 60 + 30.25)


def test_attitude_without_epoch_has_no_source_time():
    survey = _survey(attitude_packet(source_epoch=0))
    (att,) = survey.attitude
    assert att.source_time is None


def test_notes_roundtrip():
    survey = _survey(notes_packet(b"vessel R/V Synthetic", sub_channel=1))
    (note,) = survey.notes
    assert isinstance(note, XtfNotes)
    assert note.text == "vessel R/V Synthetic"
    assert note.sub_channel == 1
    assert note.category == "vessel name"
    assert (note.year, note.month, note.day) == (2016, 9, 16)
    assert (note.hour, note.minute, note.second) == (13, 44, 55)


def test_raw_serial_roundtrip():
    survey = _survey(raw_serial_packet())
    (serial,) = survey.serial
    assert isinstance(serial, XtfRawSerial)
    assert serial.text == "$GPGGA,134530.00,4439.074,N*42"
    assert serial.serial_port == 2
    assert serial.julian_day == 260
    assert serial.time_tag_ms == 123450
    assert serial.time_of_day == pytest.approx(13 * 3600 + 45 * 60 + 30.25)


def test_raw_serial_overrunning_string_is_malformed():
    packet = bytearray(raw_serial_packet())
    put(packet, 28, "<H", 5000)
    records = list(read_xtf(stream(file_header(*TWO_CHANNELS),
                                   bytes(packet))))
    assert isinstance(records[1], MalformedRecord)
    assert records[1].tag == "SER"


# ---------------------------------------------------------------------------
# bathymetry passthrough and snippets
# ---------------------------------------------------------------------------


def test_bathy_payload_is_carried_raw():
    survey = _survey(bathy_packet(b"\xde\xad\xbe\xefvendor block"))
    (bathy,) = survey.bathy
    assert isinstance(bathy, XtfRawBathy)
    assert bathy.ping_number == 12345
    assert bathy.sensor_heading_degrees == pytest.approx(88.25)
    assert bathy.payload == b"\xde\xad\xbe\xefvendor block"


def test_snippet_roundtrip():
    fragment_a = b"\x01\x00\x02\x00"
    fragment_b = b"\x03\x00\x04\x00\x05\x00"
    survey = _survey(snippet_packet(snp1(0, fragment_a),
                                    snp1(1, fragment_b)))
    (snippet,) = survey.snippets
    assert isinstance(snippet, XtfBathySnippet)
    assert snippet.ping_number == 12345           # from the XTF ping header
    assert snippet.snp0.ping_number == 4242       # from the sonar's own SNP0
    assert snippet.snp0.sonar_model == 8101
    assert snippet.snp0.frequency_khz == 455
    assert snippet.snp0.sound_velocity_mps == 1500
    assert snippet.snp0.sample_rate_hz == 34482
    assert snippet.snp0.beam_count == 2
    assert snippet.snp0.head_temp_c == pytest.approx(25.1)
    first, second = snippet.beams
    assert first.beam == 0
    assert first.fragment_bytes == fragment_a
    assert first.gain_start == 500                # 0.01 dB steps per spec
    assert second.beam == 1
    assert second.fragment_bytes == fragment_b
    assert second.snippet_samples == 3


def test_snippet_with_bad_magic_is_malformed():
    body = b"\x00" * 74
    packet = sonar_packet(body, header_type=19, num_chans=0)
    records = list(read_xtf(stream(file_header(*TWO_CHANNELS), packet)))
    assert isinstance(records[1], MalformedRecord)
    assert records[1].tag == "SNIP"


def test_snippet_truncated_beam_list_keeps_leftover():
    packet = snippet_packet(snp1(0, b"\x01\x00"), b"\x99\x98", beam_count=2)
    survey = _survey(packet)
    (snippet,) = survey.snippets
    assert len(snippet.beams) == 1
    assert snippet.leftover == b"\x99\x98"


# ---------------------------------------------------------------------------
# survey loading
# ---------------------------------------------------------------------------


def test_load_survey_bundles_series_and_counters():
    data = stream(
        file_header(*TWO_CHANNELS),
        attitude_packet(),
        sonar_packet(chan_header(num_samples=2) + b"\x01\x02"),
        notes_packet(),
        raw_serial_packet(),
        sonar_packet(chan_header(num_samples=2) + b"\x03\x04",
                     ping_number=12346),
        unknown_packet(200),
        bathy_packet(),
        snippet_packet(snp1(0, b"\x01\x00"), snp1(1, b"\x02\x00")),
    )
    survey = load_survey(data)
    assert survey.header is not None
    assert [p.ping_number for p in survey.pings] == [12345, 12346]
    assert len(survey.attitude) == 1
    assert len(survey.notes) == 1
    assert len(survey.serial) == 1
    assert len(survey.bathy) == 1
    assert len(survey.snippets) == 1
    assert survey.counters.packets == 8
    assert survey.counters.unknown_header_types == ((200, 1),)
    assert survey.counters.malformed == 0
    assert survey.counters.bytes_skipped == 0


def test_load_survey_counts_malformed_records():
    packet = sonar_packet(chan_header(num_samples=500) + b"\x01")
    survey = _survey(packet)
    assert survey.pings == ()
    assert survey.counters.malformed == 1
    assert survey.counters.packets == 1


def test_read_xtf_yields_records_in_file_order():
    data = stream(
        file_header(*TWO_CHANNELS),
        attitude_packet(),
        sonar_packet(chan_header(num_samples=0)),
        notes_packet(),
    )
    kinds = [type(r).__name__ for r in read_xtf(data)]
    assert kinds == ["XtfFileHeader", "XtfAttitude", "XtfSonarPing",
                     "XtfNotes"]


def test_docstring_layout_sizes():
    """The fixed byte counts quoted in the record docstrings."""
    assert len(file_header(*TWO_CHANNELS)) == 1024
    assert len(chan_info()) == 128
    assert len(ping_header()) == 256
    assert len(chan_header()) == 64
    assert len(attitude_packet()) == 64
    assert len(notes_packet()) == 256
    assert len(snp0()) == 74
    assert len(snp1(0, b"")) == 24


# ---------------------------------------------------------------------------
# real sample validation
# ---------------------------------------------------------------------------

_SAMPLE = os.environ.get("XTF_SAMPLE", "")


@pytest.mark.skipif(not (_SAMPLE and os.path.exists(_SAMPLE)),
                    reason="XTF_SAMPLE not set or file missing")
def test_real_sample_statistics():
    """Statistics measured 2026-08-30 from Demoplane.xtf, the sidescan
    survey Triton distributed from its own public downloads area
    (25,587,648 bytes; an Isis recording converted with DAT2XTF; source
    URL and provenance in docs/FORMAT-SOURCES.md anchor S10).

    A three-channel 16-bit file: port, starboard and subbottom, 2048
    samples per channel per ping, 2,009 sonar pings and nothing else.
    Packet framing accounts for every byte of the file exactly (zero
    skipped, zero malformed, zero unknown types); ping numbers count
    1..2009 without a gap; the sensor track sits in one small
    latitude/longitude box while the ship position fields are all zero,
    which is the nav-duplication reality the reader surfaces rather than
    resolves.
    """
    survey = load_survey(_SAMPLE)
    counters = survey.counters
    assert counters.packets == 2009
    assert counters.bytes_skipped == 0
    assert counters.malformed == 0
    assert counters.unknown_header_types == ()

    header = survey.header
    assert header is not None
    assert header.recording_program_name == "DAT2XTF"
    assert header.recording_program_version == "153"
    assert header.sonar_name == "Isis Server"
    assert header.sonar_type == 0
    assert header.nav_units == 3
    assert header.nav_units_name == "degrees"
    assert header.num_sonar_channels == 3
    assert header.num_bathymetry_channels == 0
    assert header.header_size == 1024
    assert [c.type_name for c in header.channels] == \
        ["port", "starboard", "subbottom"]
    assert [c.bytes_per_sample for c in header.channels] == [2, 2, 2]
    assert [c.sample_format for c in header.channels] == [0, 0, 0]
    assert {c.unipolar for c in header.channels} == {0}
    assert {c.correction_flags for c in header.channels} == {1}

    assert len(survey.pings) == 2009
    assert survey.pings[0].ping_number == 1
    assert survey.pings[-1].ping_number == 2009
    assert all(b.ping_number == a.ping_number + 1
               for a, b in zip(survey.pings, survey.pings[1:], strict=False))
    first = survey.pings[0]
    assert (first.year, first.month, first.day) == (2000, 7, 7)
    assert first.julian_day == 189
    assert first.time_of_day == pytest.approx(52688.70)
    assert survey.pings[-1].time_of_day == pytest.approx(52928.73)

    series = survey.channel_series()
    assert [s.channel_number for s in series] == [0, 1, 2]
    for one in series:
        assert one.info is not None
        assert len(one.data) == 2009
        assert {c.num_samples for c in one.data} == {2048}
        assert {len(c.sample_bytes) for c in one.data} == {4096}
        assert {c.slant_range_m for c in one.data} == {90.0}

    for ping in survey.pings:
        assert 47.675 < ping.sensor_y < 47.678
        assert -122.242 < ping.sensor_x < -122.239
        assert ping.ship_y == 0.0 and ping.ship_x == 0.0

    # every byte of the file is inside the header or a framed packet
    accounted = 1024 + sum(
        len(p.header_bytes) + sum(64 + len(c.sample_bytes) for c in p.channels)
        for p in survey.pings
    )
    assert accounted == os.path.getsize(_SAMPLE) == 25_587_648

    values = survey.pings[0].channels[0].values()
    assert values is not None
    assert values[:4] == (0, 405, 316, 219)
