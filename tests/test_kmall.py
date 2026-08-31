"""Kongsberg KMALL dialect: datagram walking, decoding, swath loading.

Fixtures are synthetic bytes assembled in-test from the Kongsberg
datagram specification (see hydroformats/kmall.py for the citation);
all values are fictional. The real-sample integration test at the
bottom runs only when KMALL_SAMPLE points at a real .kmall file (NOAA
NCEI's multibeam archive publishes them; see docs/FORMAT-SOURCES.md
anchor S11).
"""
import math
import os
import struct

import pytest

from hydroformats.kmall import (
    KmallFrame,
    KmallGap,
    iter_datagrams,
    load_swath,
    read_kmall,
)
from hydroformats.kmall_records import (
    KmallAttitude,
    KmallCompatibilityPosition,
    KmallHeave,
    KmallInstallation,
    KmallPing,
    KmallPosition,
    KmallRuntime,
    KmallSvp,
    KmallWaterColumn,
    sounding_usable,
)
from hydroformats.records import MalformedRecord

TS = 1_772_000_450
NS = 250_000_000

# ---------------------------------------------------------------------------
# byte builders (assembled by hand from the spec structs, not via the parser)
# ---------------------------------------------------------------------------


def datagram(dgm_type: bytes, body: bytes, version: int = 0,
             system_id: int = 1, echo_sounder_id: int = 304,
             time_sec: int = TS, time_nanosec: int = NS) -> bytes:
    """One framed datagram: the 20-byte general header (u32 length,
    4-char type code, version, system id, echo sounder id, time), the
    body, then the repeated u32 length. Both length words count the
    whole datagram, themselves included. Little endian throughout."""
    total = 20 + len(body) + 4
    head = struct.pack("<I4sBBHII", total, dgm_type, version, system_id,
                       echo_sounder_id, time_sec, time_nanosec)
    return head + body + struct.pack("<I", total)


def partition(num_of_dgms: int = 1, dgm_num: int = 1) -> bytes:
    return struct.pack("<HH", num_of_dgms, dgm_num)


def mbody(ping_cnt: int = 1234, rx_fans_per_ping: int = 1,
          rx_fan_index: int = 0, swaths_per_ping: int = 1,
          swath_along_position: int = 0, tx_transducer_ind: int = 0,
          rx_transducer_ind: int = 0, num_rx_transducers: int = 1,
          algorithm_type: int = 0) -> bytes:
    """The 12-byte common M-datagram body (EMdgmMbody_def)."""
    return struct.pack(
        "<HH8B", 12, ping_cnt, rx_fans_per_ping, rx_fan_index,
        swaths_per_ping, swath_along_position, tx_transducer_ind,
        rx_transducer_ind, num_rx_transducers, algorithm_type,
    )


def ping_info(num_tx_sectors: int = 1, num_bytes_per_tx_sector: int = 48,
              with_v1: bool = True, latitude: float = 59.5,
              longitude: float = 10.25) -> bytes:
    """The #MRZ ping info block: 152 bytes with the version 1 tail
    (bsCorrectionOffset_dB onward), 144 without."""
    size = 152 if with_v1 else 144
    fixed = struct.pack(
        "<HHf8Bf6f4f2h2BHI3f2Hf2H6f4B2df",
        size, 0,
        0.5,                     # pingRate_Hz
        2, 1, 0, 100,            # beamSpacing, depthMode, sub, distance
        0, 2, 0, 0,              # detectionMode, pulseForm, fixedGain, pad
        300_000.0,               # frequencyMode_Hz
        260_000.0, 320_000.0, 0.005, 0.004, 8_000.0, 60.5,
        -65.0, 65.0, -60.0, 60.0,
        -800, 800,               # coverage, meters
        0x03, 0x05, 0x0121,      # modeAndStabilisation, filters
        0,                       # pipeTrackingStatus
        1.0, 2.0, -10.0,         # array sizes, transmit power
        100, 0,                  # SLrampUpTimeRemaining, padding
        0.25,                    # yawAngle_deg
        num_tx_sectors, num_bytes_per_tx_sector,
        90.5, 1500.5, 5.5, -0.75, 1.5, -0.5,
        1, 0, 0, 0,              # latLongInfo, statuses, padding
        latitude, longitude,
        42.5,                    # ellipsoidHeightReRefPoint_m
    )
    if with_v1:
        fixed += struct.pack("<fBBH", 1.5, 1, 0, 1)
    return fixed


def tx_sector(numb: int = 0, with_v1: bool = True) -> bytes:
    """One #MRZ transmit sector block: 48 bytes with the version 1
    tail (highVoltageLevel_dB onward), 36 without."""
    fixed = struct.pack(
        "<4B7f2BH",
        numb, 0, 0, 0,
        0.0009765625,            # sectorTransmitDelay_sec (2^-10)
        1.5, 220.5, 0.0,         # tilt, nominal SL, focus
        315_000.0, 8_000.0, 0.001953125,
        30, 1, 0,                # pulseShading, signalWaveForm, padding
    )
    if with_v1:
        fixed += struct.pack("<3f", -1.5, 0.5, 0.001953125)
    return fixed


def rx_info(max_main: int = 2, valid_main: int = 2,
            bytes_per_sounding: int = 120, extra_detections: int = 0,
            extra_classes: int = 0, bytes_per_class: int = 4) -> bytes:
    """The 32-byte #MRZ receiver info block."""
    return struct.pack(
        "<4H4f4H",
        32, max_main, valid_main, bytes_per_sounding,
        6_250.0, 12_500.0, -20.5, -30.5,
        0, extra_detections, extra_classes, bytes_per_class,
    )


def sounding(index: int = 0, detection_type: int = 0,
             detection_method: int = 1, z: float = 750.5,
             twtt: float = 0.125, si_num_samples: int = 3) -> bytes:
    """One 120-byte #MRZ sounding block; values fictional but exact
    in float32 so equality survives the round-trip."""
    return struct.pack(
        "<H8BH6f2Hf7f4f6f4H",
        index, 0,
        detection_type, detection_method, 0, 0, 0, 0, 5,
        0,
        100.0, 0.5, 0.25, 0.5, 0.001953125, 0.0009765625,
        100 + index, 512, -45.5,
        60.5, -25.5, -27.25, 50.5, 220.5, 0.0, 80.5,
        -60.25, 0.125, twtt, -0.0009765625,
        -0.0001220703125, 0.000244140625, z, -900.25, 12.5, 5.5,
        0,
        500, 2, si_num_samples,
    )


SI_SAMPLES = (-205, -210, -215, -220, -225)


def mrz_body(info: bytes | None = None, soundings: bytes | None = None,
             si: tuple[int, ...] = SI_SAMPLES, body: bytes | None = None,
             rx: bytes | None = None) -> bytes:
    parts = [
        partition(),
        body if body is not None else mbody(),
        info if info is not None else ping_info(),
        tx_sector(),
        rx if rx is not None else rx_info(),
        soundings if soundings is not None else (
            sounding(0, si_num_samples=3) + sounding(
                1, detection_type=2, detection_method=0, z=751.5,
                si_num_samples=2)
        ),
    ]
    parts.append(struct.pack(f"<{len(si)}h", *si))
    return b"".join(parts)


def mrz_record(**kwargs) -> bytes:
    return datagram(b"#MRZ", mrz_body(**kwargs), version=3)


def spo_record(latitude: float = 59.5, text: bytes = b"$INGGA,fictional*00",
               dgm_type: bytes = b"#SPO") -> bytes:
    body = struct.pack("<4H", 8, 0, 0x0001, 0) + struct.pack(
        "<2If2d3f", TS, NS, 1.5, latitude, 10.25, 5.25, 91.5, 42.5) + text
    return datagram(dgm_type, body)


def skm_sample(time_sec: int = TS, heave: float = -0.25,
               delayed_heave: float = -0.125) -> bytes:
    kmb = struct.pack(
        "<4sHH3I2df4f3f3f7f3f",
        b"#KMB", 120, 1, time_sec, NS, 0,
        59.5, 10.25, 42.5,
        1.25, -0.5, 90.5, heave,
        0.125, 0.25, -0.125,
        2.5, 0.5, -0.25,
        0.5, 0.5, 0.75, 0.03125, 0.03125, 0.0625, 0.125,
        0.25, -0.25, 0.0625,
    )
    return kmb + struct.pack("<2If", time_sec, NS, delayed_heave)


def skm_record(samples: tuple[bytes, ...] | None = None,
               bytes_per_sample: int = 132) -> bytes:
    if samples is None:
        samples = (skm_sample(), skm_sample(time_sec=TS + 1, heave=-0.5))
    info = struct.pack("<H2B4H", 12, 0, 0x01, 1, len(samples),
                       bytes_per_sample, 0x7F)
    return datagram(b"#SKM", info + b"".join(samples), version=1)


def svp_record(points: tuple[tuple[float, float, float, float], ...] = (
        (1.5, 1480.5, 8.5, 35.0), (150.0, 1478.25, 4.5, 35.25))) -> bytes:
    common = struct.pack("<HH4sI2d", 28, len(points), b"S01\x00",
                         TS - 3600, 59.5, 10.25)
    body = common + b"".join(
        struct.pack("<2fI2f", depth, sv, 0, temp, sal)
        for depth, sv, temp, sal in points
    )
    return datagram(b"#SVP", body, version=1)


def iip_record(text: bytes = b"OSCV:Empty,EMXV:EM304,\nSN=12345,",
               dgm_type: bytes = b"#IIP") -> bytes:
    return datagram(dgm_type, struct.pack("<3H", 6 + len(text), 0, 0) + text)


def che_record(heave: float = -0.25) -> bytes:
    return datagram(b"#CHE", mbody() + struct.pack("<f", heave))


def mwc_record(sample_bytes: int = 64) -> bytes:
    body = (
        partition() + mbody(ping_cnt=1235)
        + struct.pack("<3Hhf", 12, 1, 16, 0, -0.25)      # txInfo
        + struct.pack("<3fHh", 1.5, 315_000.0, 1.0, 0, 0)  # one tx sector
        + struct.pack("<2H3Bb2f", 16, 4, 10, 0, 20, -30, 12_500.0, 1500.5)
        + b"\x01" * sample_bytes
    )
    return datagram(b"#MWC", body, version=2)


def stream(*datagrams: bytes) -> bytes:
    return b"".join(datagrams)


# ---------------------------------------------------------------------------
# datagram walking
# ---------------------------------------------------------------------------


def test_header_bytes_are_little_endian_length_then_hash_code():
    built = iip_record(text=b"AB")
    assert built[:4] == struct.pack("<I", len(built))
    assert built[4:8] == b"#IIP"
    assert built[-4:] == struct.pack("<I", len(built))


def test_walker_yields_frames_in_file_order():
    data = stream(iip_record(), svp_record())
    frames = list(iter_datagrams(data))
    assert [f.dgm_type for f in frames] == ["#IIP", "#SVP"]
    assert all(isinstance(f, KmallFrame) for f in frames)
    assert frames[0].offset == 0
    assert frames[1].offset == len(iip_record())
    assert frames[0].echo_sounder_id == 304
    assert frames[0].time_sec == TS
    assert frames[0].time_nanosec == NS
    assert all(f.end_length_ok for f in frames)


def test_walker_flags_end_length_mismatch_without_raising():
    data = bytearray(iip_record())
    data[-4:] = struct.pack("<I", len(data) + 8)
    (frame,) = iter_datagrams(bytes(data))
    assert frame.end_length_ok is False


def test_walker_degrades_on_truncated_final_datagram():
    good = iip_record()
    cut = svp_record()[:-7]
    events = list(iter_datagrams(stream(good, cut)))
    assert isinstance(events[0], KmallFrame)
    assert isinstance(events[1], KmallGap)
    assert events[1].offset == len(good)
    assert events[1].size == len(cut)


def test_walker_resynchronizes_on_the_hash_code_after_garbage():
    data = stream(iip_record(), b"\xde\xad\xbe\xef" * 3, svp_record())
    events = list(iter_datagrams(data))
    assert [type(e).__name__ for e in events] == \
        ["KmallFrame", "KmallGap", "KmallFrame"]
    assert events[1].size == 12
    assert events[2].dgm_type == "#SVP"


def test_walker_rejects_non_kmall_bytes():
    assert list(iter_datagrams(b"")) == []
    events = list(iter_datagrams(b"not a kmall file at all"))
    assert all(isinstance(e, KmallGap) for e in events)


def test_walker_treats_insane_declared_size_as_gap_then_resyncs():
    bad = struct.pack("<I4s", 0x7FFFFFFF, b"#MRZ") + b"\x00" * 24
    data = stream(bad, iip_record())
    events = list(iter_datagrams(data))
    assert isinstance(events[0], KmallGap)
    assert events[0].size == len(bad)
    assert isinstance(events[1], KmallFrame)
    assert events[1].dgm_type == "#IIP"


# ---------------------------------------------------------------------------
# record decoding (round-trip: build bytes, parse, compare)
# ---------------------------------------------------------------------------


def _records(data: bytes):
    return list(read_kmall(data))


def test_iip_text_roundtrip():
    (iip,) = _records(iip_record())
    assert isinstance(iip, KmallInstallation)
    assert iip.time == pytest.approx(TS + 0.25)
    assert iip.dgm_version == 0
    assert iip.system_id == 1
    assert iip.echo_sounder_id == 304
    assert iip.text == "OSCV:Empty,EMXV:EM304,\nSN=12345,"
    assert "EMXV:EM304" in iip.lines


def test_iop_text_roundtrip():
    (iop,) = _records(iip_record(dgm_type=b"#IOP"))
    assert isinstance(iop, KmallRuntime)
    assert iop.text == "OSCV:Empty,EMXV:EM304,\nSN=12345,"


def test_spo_roundtrip():
    (spo,) = _records(spo_record())
    assert isinstance(spo, KmallPosition)
    assert spo.sensor_system == 0
    assert spo.sensor_status == 0x0001
    assert spo.active is True
    assert spo.time_from_sensor_sec == TS
    assert spo.time_from_sensor_nanosec == NS
    assert spo.pos_fix_quality_m == pytest.approx(1.5)
    assert spo.corrected_lat_deg == pytest.approx(59.5)
    assert spo.corrected_long_deg == pytest.approx(10.25)
    assert spo.speed_over_ground_mps == pytest.approx(5.25)
    assert spo.course_over_ground_deg == pytest.approx(91.5)
    assert spo.ellipsoid_height_re_ref_point_m == pytest.approx(42.5)
    assert spo.raw_text == "$INGGA,fictional*00"
    assert spo.position_available is True


def test_spo_unavailable_position_sentinel():
    # UNAVAILABLE_LATITUDE is the exact float 200.0 per the spec defines
    (spo,) = _records(spo_record(latitude=200.0))
    assert spo.position_available is False


def test_cpo_roundtrip():
    (cpo,) = _records(spo_record(dgm_type=b"#CPO"))
    assert isinstance(cpo, KmallCompatibilityPosition)
    assert cpo.tag == "CPO"
    assert cpo.corrected_lat_deg == pytest.approx(59.5)


def test_skm_roundtrip():
    (skm,) = _records(skm_record())
    assert isinstance(skm, KmallAttitude)
    assert skm.sensor_system == 0
    assert skm.sensor_input_format == 1
    assert skm.sensor_data_contents == 0x7F
    assert skm.num_samples == 2
    first, second = skm.samples
    assert first.time == pytest.approx(TS + 0.25)
    assert second.time == pytest.approx(TS + 1.25)
    assert first.status == 0
    assert first.latitude_deg == pytest.approx(59.5)
    assert first.longitude_deg == pytest.approx(10.25)
    assert first.ellipsoid_height_m == pytest.approx(42.5)
    assert first.roll_deg == pytest.approx(1.25)
    assert first.pitch_deg == pytest.approx(-0.5)
    assert first.heading_deg == pytest.approx(90.5)
    assert first.heave_m == pytest.approx(-0.25)
    assert second.heave_m == pytest.approx(-0.5)
    assert first.roll_rate == pytest.approx(0.125)
    assert first.vel_down == pytest.approx(-0.25)
    assert first.heave_error_m == pytest.approx(0.125)
    assert first.down_acceleration == pytest.approx(0.0625)
    assert first.delayed_heave_m == pytest.approx(-0.125)
    assert first.delayed_heave_time_sec == TS


def test_skm_without_delayed_heave():
    # numBytesPerSample of 120 leaves no room for the delayed heave block
    samples = (skm_sample()[:120],)
    (skm,) = _records(skm_record(samples=samples, bytes_per_sample=120))
    (sample,) = skm.samples
    assert sample.heave_m == pytest.approx(-0.25)
    assert sample.delayed_heave_m is None
    assert sample.delayed_heave_time_sec is None


def test_svp_roundtrip():
    (svp,) = _records(svp_record())
    assert isinstance(svp, KmallSvp)
    assert svp.sensor_format == "S01"
    assert svp.profile_time_sec == TS - 3600
    assert svp.latitude_deg == pytest.approx(59.5)
    assert svp.longitude_deg == pytest.approx(10.25)
    assert svp.num_points == 2
    assert svp.depths_m == pytest.approx((1.5, 150.0))
    assert svp.sound_speeds_mps == pytest.approx((1480.5, 1478.25))
    assert svp.temperatures_c == pytest.approx((8.5, 4.5))
    assert svp.salinities == pytest.approx((35.0, 35.25))


def test_che_roundtrip():
    (che,) = _records(che_record())
    assert isinstance(che, KmallHeave)
    assert che.ping_cnt == 1234
    assert che.heave_m == pytest.approx(-0.25)


def test_mwc_is_decoded_header_only_with_count():
    (mwc,) = _records(mwc_record(sample_bytes=64))
    assert isinstance(mwc, KmallWaterColumn)
    assert mwc.ping_cnt == 1235
    assert mwc.rx_fans_per_ping == 1
    assert mwc.num_bytes == len(mwc_record(sample_bytes=64))
    assert not hasattr(mwc, "samples")


def test_mrz_ping_info_roundtrip():
    (ping,) = _records(mrz_record())
    assert isinstance(ping, KmallPing)
    assert ping.time == pytest.approx(TS + 0.25)
    assert ping.dgm_version == 3
    assert ping.num_partitions == 1
    assert ping.ping_cnt == 1234
    assert ping.rx_fans_per_ping == 1
    assert ping.rx_fan_index == 0
    assert ping.swaths_per_ping == 1
    assert ping.ping_rate_hz == pytest.approx(0.5)
    assert ping.beam_spacing == 2
    assert ping.depth_mode == 1
    assert ping.detection_mode == 0
    assert ping.pulse_form == 2
    assert ping.frequency_mode_hz == pytest.approx(300_000.0)
    assert ping.freq_range_low_lim_hz == pytest.approx(260_000.0)
    assert ping.freq_range_high_lim_hz == pytest.approx(320_000.0)
    assert ping.abs_coeff_db_per_km == pytest.approx(60.5)
    assert ping.port_sector_edge_deg == pytest.approx(-65.0)
    assert ping.starb_sector_edge_deg == pytest.approx(65.0)
    assert ping.port_mean_cov_m == -800
    assert ping.starb_mean_cov_m == 800
    assert ping.mode_and_stabilisation == 0x03
    assert ping.runtime_filter1 == 0x05
    assert ping.runtime_filter2 == 0x0121
    assert ping.transmit_power_db == pytest.approx(-10.0)
    assert ping.yaw_angle_deg == pytest.approx(0.25)
    assert ping.heading_vessel_deg == pytest.approx(90.5)
    assert ping.sound_speed_at_tx_depth_mps == pytest.approx(1500.5)
    assert ping.tx_transducer_depth_m == pytest.approx(5.5)
    assert ping.z_water_level_re_ref_point_m == pytest.approx(-0.75)
    assert ping.lat_long_info == 1
    assert ping.latitude_deg == pytest.approx(59.5)
    assert ping.longitude_deg == pytest.approx(10.25)
    assert ping.ellipsoid_height_re_ref_point_m == pytest.approx(42.5)
    assert ping.bs_correction_offset_db == pytest.approx(1.5)
    assert ping.lamberts_law_applied == 1
    assert ping.ice_window == 0
    assert ping.active_modes == 1
    assert ping.position_available is True


def test_mrz_sector_and_rx_info_roundtrip():
    (ping,) = _records(mrz_record())
    (sector,) = ping.tx_sectors
    assert sector.tx_sector_numb == 0
    assert sector.sector_transmit_delay_sec == pytest.approx(0.0009765625)
    assert sector.tilt_angle_re_tx_deg == pytest.approx(1.5)
    assert sector.tx_nominal_source_level_db == pytest.approx(220.5)
    assert sector.centre_freq_hz == pytest.approx(315_000.0)
    assert sector.signal_bandwidth_hz == pytest.approx(8_000.0)
    assert sector.total_signal_length_sec == pytest.approx(0.001953125)
    assert sector.pulse_shading == 30
    assert sector.signal_wave_form == 1
    assert sector.high_voltage_level_db == pytest.approx(-1.5)
    assert sector.sector_tracking_corr_db == pytest.approx(0.5)
    assert sector.effective_signal_length_sec == pytest.approx(0.001953125)
    assert ping.num_soundings_max_main == 2
    assert ping.num_soundings_valid_main == 2
    assert ping.wc_sample_rate_hz == pytest.approx(6_250.0)
    assert ping.seabed_image_sample_rate_hz == pytest.approx(12_500.0)
    assert ping.bs_normal_db == pytest.approx(-20.5)
    assert ping.bs_oblique_db == pytest.approx(-30.5)
    assert ping.num_extra_detections == 0
    assert ping.extra_detection_classes == ()


def test_mrz_soundings_decode_hand_computed():
    (ping,) = _records(mrz_record())
    assert len(ping.soundings) == 2
    first, second = ping.soundings
    assert first.sounding_index == 0
    assert first.tx_sector_numb == 0
    assert first.detection_type == 0
    assert first.detection_method == 1
    assert first.detection_confidence_level == 5
    assert first.range_factor == pytest.approx(100.0)
    assert first.quality_factor == pytest.approx(0.5)
    assert first.detection_uncertainty_ver_m == pytest.approx(0.25)
    assert first.detection_uncertainty_hor_m == pytest.approx(0.5)
    assert first.echo_length_sec == pytest.approx(0.0009765625)
    assert first.wc_beam_numb == 100
    assert second.wc_beam_numb == 101
    assert first.wc_range_samples == 512
    assert first.wc_nom_beam_angle_across_deg == pytest.approx(-45.5)
    assert first.mean_abs_coeff_db_per_km == pytest.approx(60.5)
    assert first.reflectivity1_db == pytest.approx(-25.5)
    assert first.reflectivity2_db == pytest.approx(-27.25)
    assert first.receiver_sensitivity_applied_db == pytest.approx(50.5)
    assert first.source_level_applied_db == pytest.approx(220.5)
    assert first.bs_calibration_db == pytest.approx(0.0)
    assert first.tvg_db == pytest.approx(80.5)
    assert first.beam_angle_re_rx_deg == pytest.approx(-60.25)
    assert first.beam_angle_correction_deg == pytest.approx(0.125)
    assert first.two_way_travel_time_sec == pytest.approx(0.125)
    assert first.two_way_travel_time_correction_sec == \
        pytest.approx(-0.0009765625)
    assert first.delta_latitude_deg == pytest.approx(-0.0001220703125)
    assert first.delta_longitude_deg == pytest.approx(0.000244140625)
    assert first.z_re_ref_point_m == pytest.approx(750.5)
    assert first.y_re_ref_point_m == pytest.approx(-900.25)
    assert first.x_re_ref_point_m == pytest.approx(12.5)
    assert first.beam_inc_angle_adj_deg == pytest.approx(5.5)
    # per-beam array views preserve the raw observables in beam order
    assert ping.z_re_ref_point_m == pytest.approx((750.5, 751.5))
    assert ping.two_way_travel_times_sec == pytest.approx((0.125, 0.125))
    assert ping.beam_angles_re_rx_deg == pytest.approx((-60.25, -60.25))
    assert ping.reflectivity1_db == pytest.approx((-25.5, -25.5))
    assert ping.reflectivity2_db == pytest.approx((-27.25, -27.25))
    # z is positive down in the surface coordinate system; the waterline
    # depth subtracts z_waterLevelReRefPoint_m per the spec's reference
    # points chapter
    assert ping.depths_re_waterline_m == pytest.approx((751.25, 752.25))


def test_sounding_usable_flags():
    (ping,) = _records(mrz_record())
    first, second = ping.soundings
    assert sounding_usable(first) is True
    assert sounding_usable(second) is False  # rejected, no valid detection
    assert second.detection_type == 2
    assert second.detection_method == 0


def test_mrz_seabed_image_split_per_beam():
    (ping,) = _records(mrz_record())
    assert ping.si_samples == SI_SAMPLES
    assert ping.seabed_image_db == pytest.approx(
        (-20.5, -21.0, -21.5, -22.0, -22.5))
    per_beam = ping.seabed_image_per_beam()
    assert per_beam == ((-205, -210, -215), (-220, -225))
    assert ping.soundings[0].si_num_samples == 3
    assert ping.soundings[1].si_num_samples == 2


def test_mrz_version0_ping_info_omits_v1_fields():
    data = datagram(b"#MRZ", mrz_body(
        info=ping_info(with_v1=False, num_bytes_per_tx_sector=48)))
    (ping,) = _records(data)
    assert ping.bs_correction_offset_db is None
    assert ping.lamberts_law_applied is None
    assert ping.ice_window is None
    assert ping.active_modes is None
    assert ping.latitude_deg == pytest.approx(59.5)
    assert len(ping.soundings) == 2


def test_mrz_version0_tx_sector_omits_v1_fields():
    body = b"".join((
        partition(), mbody(),
        ping_info(num_bytes_per_tx_sector=36),
        tx_sector(with_v1=False),
        rx_info(),
        sounding(0, si_num_samples=3),
        sounding(1, detection_type=2, detection_method=0, z=751.5,
                 si_num_samples=2),
        struct.pack(f"<{len(SI_SAMPLES)}h", *SI_SAMPLES),
    ))
    (ping,) = _records(datagram(b"#MRZ", body))
    (sector,) = ping.tx_sectors
    assert sector.high_voltage_level_db is None
    assert sector.sector_tracking_corr_db is None
    assert sector.effective_signal_length_sec is None
    assert sector.centre_freq_hz == pytest.approx(315_000.0)


def test_short_mrz_payload_is_malformed_not_fatal():
    data = datagram(b"#MRZ", mrz_body()[:40])
    (rec,) = _records(data)
    assert isinstance(rec, MalformedRecord)
    assert rec.tag == "MRZ"


def test_mrz_partition_reassembly_from_two_parts():
    """A partitioned #MRZ per the spec's multibeam data logging chapter:
    every partition repeats the general header, the partition struct and
    (from MRZ version 3) the common body; the trailing length word closes
    each part. Rejoining strips the repeats and yields one ping."""
    whole = mrz_body()
    content = whole[4 + 12:]  # after partition + Mbody
    half = len(content) // 2
    part1 = datagram(b"#MRZ", partition(2, 1) + mbody() + content[:half],
                     version=3)
    part2 = datagram(b"#MRZ", partition(2, 2) + mbody() + content[half:],
                     version=3)
    (ping,) = _records(stream(part1, part2))
    assert isinstance(ping, KmallPing)
    assert ping.num_partitions == 2
    (unsplit,) = _records(mrz_record())
    assert ping.soundings == unsplit.soundings
    assert ping.si_samples == unsplit.si_samples
    assert ping.latitude_deg == unsplit.latitude_deg


def test_mrz_partition_reassembly_before_version3_has_no_repeated_body():
    """Before MRZ version 3 only the first partition carried the common
    body (spec: multibeam data logging, 'Before Rev. I' column)."""
    whole = mrz_body()
    content = whole[4 + 12:]
    half = len(content) // 2
    part1 = datagram(b"#MRZ", partition(2, 1) + mbody() + content[:half],
                     version=2)
    part2 = datagram(b"#MRZ", partition(2, 2) + content[half:], version=2)
    (ping,) = _records(stream(part1, part2))
    assert isinstance(ping, KmallPing)
    assert ping.soundings == _records(mrz_record())[0].soundings


def test_incomplete_partition_set_degrades_to_malformed():
    whole = mrz_body()
    content = whole[4 + 12:]
    part1 = datagram(b"#MRZ", partition(2, 1) + mbody() + content[:50],
                     version=3)
    (rec,) = _records(part1)
    assert isinstance(rec, MalformedRecord)
    assert rec.tag == "MRZ"
    assert "partition" in rec.error


def test_unknown_type_is_skipped_by_read_kmall():
    data = stream(iip_record(), datagram(b"#SVT", b"\x00" * 32))
    records = _records(data)
    assert len(records) == 1
    assert isinstance(records[0], KmallInstallation)


def test_docstring_layout_sizes():
    """The struct byte counts quoted in the record docstrings."""
    assert len(datagram(b"#IIP", b"")) == 24
    assert len(partition()) == 4
    assert len(mbody()) == 12
    assert len(ping_info()) == 152
    assert len(ping_info(with_v1=False)) == 144
    assert len(tx_sector()) == 48
    assert len(tx_sector(with_v1=False)) == 36
    assert len(rx_info()) == 32
    assert len(sounding()) == 120
    assert len(skm_sample()) == 132
    assert len(svp_record(points=())) == 24 + 28
    assert len(spo_record(text=b"")) == 24 + 8 + 40


# ---------------------------------------------------------------------------
# load_swath
# ---------------------------------------------------------------------------


def _swath_stream() -> bytes:
    return stream(
        iip_record(),
        iip_record(dgm_type=b"#IOP"),
        svp_record(),
        spo_record(),
        skm_record(),
        mrz_record(),
        che_record(),
        mwc_record(),
        spo_record(dgm_type=b"#CPO"),
        datagram(b"#SVT", b"\x00" * 32),
        datagram(b"#SCL", b"\x00" * 24),
        datagram(b"#SVT", b"\x00" * 32),
    )


def test_load_swath_bundles_series_and_counters():
    swath = load_swath(_swath_stream())
    assert swath.installation is not None
    assert "EMXV:EM304" in swath.installation.text
    assert len(swath.runtime) == 1
    assert len(swath.svps) == 1
    assert len(swath.positions) == 1
    assert len(swath.compatibility_positions) == 1
    assert len(swath.attitude) == 1
    assert len(swath.pings) == 1
    assert len(swath.heave) == 1
    assert len(swath.water_column) == 1
    assert swath.pings[0].ping_cnt == 1234
    assert swath.counters.datagrams == 12
    assert dict(swath.counters.unknown_dgm_types) == {"#SCL": 1, "#SVT": 2}
    assert swath.counters.end_length_mismatches == 0
    assert swath.counters.bytes_skipped == 0
    assert swath.counters.mrz_parts_joined == 0
    assert swath.counters.mrz_parts_dropped == 0


def test_load_swath_counts_end_length_mismatches():
    bad = bytearray(iip_record())
    bad[-4:] = struct.pack("<I", 7)
    swath = load_swath(stream(bytes(bad), svp_record()))
    assert swath.counters.end_length_mismatches == 1
    assert swath.counters.datagrams == 2


def test_load_swath_counts_partition_reassembly():
    whole = mrz_body()
    content = whole[4 + 12:]
    half = len(content) // 2
    part1 = datagram(b"#MRZ", partition(2, 1) + mbody() + content[:half],
                     version=3)
    part2 = datagram(b"#MRZ", partition(2, 2) + mbody() + content[half:],
                     version=3)
    swath = load_swath(stream(part1, part2))
    assert len(swath.pings) == 1
    assert swath.counters.mrz_parts_joined == 2
    orphan = load_swath(part1)
    assert orphan.pings == ()
    assert orphan.counters.mrz_parts_dropped == 1


def test_load_swath_counts_truncated_tail_bytes():
    data = stream(iip_record(), mrz_record(), mrz_record()[:-9])
    swath = load_swath(data)
    assert len(swath.pings) == 1
    assert swath.counters.bytes_skipped == len(mrz_record()) - 9


def test_load_swath_never_raises_on_garbage():
    swath = load_swath(b"\x00\x01" * 40)
    assert swath.pings == ()
    assert swath.counters.datagrams == 0
    assert swath.counters.bytes_skipped == 80


# ---------------------------------------------------------------------------
# real sample validation (NOAA NCEI multibeam archive)
# ---------------------------------------------------------------------------

_SAMPLE = os.environ.get("KMALL_SAMPLE", "")


@pytest.mark.skipif(not (_SAMPLE and os.path.exists(_SAMPLE)),
                    reason="KMALL_SAMPLE not set or file missing")
def test_real_sample_statistics():
    """Statistics measured 2026-08-31 from NCEI file
    0002_20230906_100130_EX2306_MB.kmall (NOAA Ship Okeanos Explorer
    cruise EX2306, Kongsberg EM 304 serial 10016, Gulf of Alaska,
    2023-09-06; 106,231,030 bytes decompressed; source URL in
    docs/FORMAT-SOURCES.md anchor S11).

    Every byte frames (nothing skipped, nothing malformed) and every
    trailing length word verifies. The census is 494 #MRZ version 3
    pings (dual swath: 247 ping counts times two fans of 800 soundings
    at 8 transmit sectors), 5,801 each of #SPO and #CPO, 3,799 #SKM
    blocks of KM binary attitude (386,784 samples, every one carrying
    delayed heave), one extended-to-full-ocean-depth SVP cast, one
    #IIP, two #IOP, and 21,271 datagrams of types outside this
    reader's scope (#SVT, #SCL, #FCF), skipped and counted. Water
    column was logged to a separate .kmwcd file NCEI does not archive,
    so the #MWC path rests on the synthetic fixtures. Two cross-pins:
    the per-sounding usable criterion reproduces the datagrams' own
    numSoundingsValidMain sum exactly, and the geometric slant range
    from each x, y, z agrees with 0.5 x sound speed x travel time to
    within one percent (the residual being refraction), which pins the
    xyz, travel time and sound speed decodes against each other.
    """
    swath = load_swath(_SAMPLE)
    counters = swath.counters
    assert counters.datagrams == 37_170
    assert counters.bytes_skipped == 0
    assert counters.end_length_mismatches == 0
    assert counters.mrz_parts_joined == 0
    assert counters.mrz_parts_dropped == 0
    assert counters.mwc_continuations == 0
    assert dict(counters.unknown_dgm_types) == {
        "#FCF": 1, "#SCL": 1933, "#SVT": 19_337}
    assert not [r for r in read_kmall(_SAMPLE)
                if isinstance(r, MalformedRecord)]

    assert swath.installation is not None
    assert "EMXV:EM304" in swath.installation.text
    assert "SN=10016" in swath.installation.text
    assert len(swath.runtime) == 2
    assert len(swath.pings) == 494
    assert len(swath.water_column) == 0
    assert len(swath.heave) == 0
    assert len(swath.positions) == 5801
    assert len(swath.compatibility_positions) == 5801
    assert len(swath.attitude) == 3799
    assert len(swath.svps) == 1

    assert {p.echo_sounder_id for p in swath.pings} == {304}
    assert {p.dgm_version for p in swath.pings} == {3}
    assert {p.num_partitions for p in swath.pings} == {1}
    assert {len(p.soundings) for p in swath.pings} == {800}
    assert {p.num_extra_detections for p in swath.pings} == {0}
    assert {p.num_tx_sectors for p in swath.pings} == {8}
    assert {(p.rx_fans_per_ping, p.swaths_per_ping)
            for p in swath.pings} == {(2, 2)}
    assert swath.pings[0].ping_cnt == 34_819
    assert swath.pings[-1].ping_cnt == 35_065

    lats = [p.latitude_deg for p in swath.pings]
    lons = [p.longitude_deg for p in swath.pings]
    assert min(lats) > 55.82 and max(lats) < 55.90
    assert min(lons) > -136.45 and max(lons) < -136.43
    for ping in swath.pings:
        assert ping.position_available
        assert 1490.0 < ping.sound_speed_at_tx_depth_mps < 1510.0

    usable = [
        s
        for p in swath.pings for s in p.soundings
        if sounding_usable(s) and s.detection_type == 0
    ]
    assert len(usable) == 394_665
    assert len(usable) == sum(p.num_soundings_valid_main for p in swath.pings)
    depths = [s.z_re_ref_point_m for s in usable]
    assert min(depths) == pytest.approx(2654.83, abs=0.01)
    assert max(depths) == pytest.approx(2800.13, abs=0.01)

    for ping in swath.pings[:20]:
        for s in ping.soundings:
            if not (sounding_usable(s) and s.detection_type == 0):
                continue
            geometric = math.sqrt(
                s.x_re_ref_point_m ** 2 + s.y_re_ref_point_m ** 2
                + (s.z_re_ref_point_m + ping.tx_transducer_depth_m) ** 2)
            acoustic = (0.5 * ping.sound_speed_at_tx_depth_mps
                        * s.two_way_travel_time_sec)
            assert 0.98 < geometric / acoustic < 1.005

    for ping in swath.pings[:20]:
        per_beam = ping.seabed_image_per_beam()
        assert sum(len(piece) for piece in per_beam) == len(ping.si_samples)

    svp = swath.svps[0]
    assert svp.sensor_format == "S00"
    assert svp.num_points == 488
    assert max(svp.depths_m) == pytest.approx(12_000.0)
    assert all(1400.0 < v < 1700.0 for v in svp.sound_speeds_mps)

    samples = sum(a.num_samples for a in swath.attitude)
    assert samples == 386_784
    assert {a.sensor_input_format for a in swath.attitude} == {1}
    assert all(
        sample.delayed_heave_m is not None
        for a in swath.attitude for sample in a.samples
    )

    assert swath.positions[0].raw_text.startswith("$GPGGA")
    assert all(p.position_available for p in swath.positions)
