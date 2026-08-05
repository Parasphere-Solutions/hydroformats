"""HSX dialect: header records and multi-line RMB/RSS assembly.

Field layouts anchored to MB-System's mb201 reader/writer.
"""
import io

from hydroformats.hsx import parse_hsx
from hydroformats.records import (
    DeviceCapability,
    DeviceOffsets,
    Draft,
    GpsMeasurement,
    HsxVersion,
    MalformedRecord,
    MultibeamInfo,
    PitchStabilization,
    Position,
    PrimaryNav,
    RawMultibeam,
    RawSidescan,
    SidescanInfo,
    SonarSettings,
    SurveyParameters,
)


def parse_all(text: str):
    return list(parse_hsx(io.StringIO(text)))


def parse_one(line: str):
    records = parse_all(line)
    assert len(records) == 1
    return records[0]


def test_hsx_version():
    assert parse_one("HSX 8") == HsxVersion(tag="HSX", version=8)


def test_dv2_hex_capability():
    record = parse_one("DV2 1 1f 0 1")
    assert record == DeviceCapability(tag="DV2", device=1, capability=0x1F,
                                      towfish=0, enabled=1)


def test_of2_offsets_with_type():
    record = parse_one("OF2 1 0 0.20 1.10 0.45 0.00 0.00 0.00 0.05")
    assert isinstance(record, DeviceOffsets)
    assert record.offset_type == 0 and record.starboard == 0.2 and record.latency == 0.05


def test_mbi_hex_fields_per_mbsystem_writer():
    record = parse_one("MBI 1 1 0 1809 8 0 -60.000 15.000")
    assert isinstance(record, MultibeamInfo)
    assert record.beam_data_available == 0x1809
    assert record.first_beam_angle == -60.0 and record.angle_increment == 15.0


def test_ssi_and_pri_and_hsp():
    assert parse_one("SSI 1 0 512 512") == SidescanInfo(
        tag="SSI", device=1, sonar_flags=0, port_num_samples=512,
        starboard_num_samples=512)
    assert parse_one("PRI 1") == PrimaryNav(tag="PRI", device=1)
    hsp = parse_one("HSP 0.50 40.00 25.00 25.00 45 45 3 1 30.00 0.00 1 0")
    assert isinstance(hsp, SurveyParameters)
    assert hsp.maximum_depth == 40.0 and hsp.units == 1


def test_dft_gps_psa_snr():
    assert parse_one("DFT 1 100.0 0.45") == Draft(tag="DFT", device=1, time=100.0,
                                                  draft=0.45)
    gps = parse_one("GPS 0 100.0 117.5 2.1 1.1 4 11")
    assert isinstance(gps, GpsMeasurement) and gps.satellites == 11
    psa = parse_one("PSA 1 100.0 7 0.10 -0.05")
    assert isinstance(psa, PitchStabilization) and psa.ping == 7
    snr = parse_one("SNR 1 100.0 7 0 3 210 12.5 0.9")
    assert isinstance(snr, SonarSettings)
    assert snr.settings == (210.0, 12.5, 0.9)


def test_rmb_assembles_arrays_in_bitmask_order():
    text = (
        "RMB 1 100.500 1 0 1809 4 1500.00 7\n"
        "12.50 12.61 12.72 12.83\n"      # 0x0001 beam ranges
        "12.40 12.45 12.50 12.55\n"      # 0x0008 depths
        "100 101 102 103\n"              # 0x0800 intensities
        "3 3 3 3\n"                      # 0x1000 quality
    )
    record = parse_one(text)
    assert isinstance(record, RawMultibeam)
    assert record.num_beams == 4 and record.ping == 7
    assert record.beam_ranges == (12.5, 12.61, 12.72, 12.83)
    assert record.depths == (12.4, 12.45, 12.5, 12.55)
    assert record.intensities == (100, 101, 102, 103)
    assert record.quality == (3, 3, 3, 3)
    assert record.eastings is None and record.uncertainties is None


def test_rmb_sounding_xy_takes_two_lines():
    text = (
        "RMB 1 100.500 1 0 4 2 1500.00 9\n"
        "454468.1 454470.2\n"
        "4945274.1 4945272.9\n"
    )
    record = parse_one(text)
    assert isinstance(record, RawMultibeam)
    assert record.eastings == (454468.1, 454470.2)
    assert record.northings == (4945274.1, 4945272.9)


def test_rmb_truncated_by_eof_is_malformed_not_crash():
    record = parse_one("RMB 1 100.5 1 0 1 4 1500.00 7\n")
    assert isinstance(record, MalformedRecord)
    assert "beam_ranges" in record.error


def test_rmb_interrupted_by_tagged_line_yields_both():
    text = (
        "RMB 1 100.5 1 0 1 4 1500.00 7\n"
        "POS 0 100.6 454468.1 4945274.1\n"
    )
    records = parse_all(text)
    assert isinstance(records[0], MalformedRecord)
    assert isinstance(records[1], Position)  # the tagged line is not lost


def test_rss_assembles_port_and_starboard():
    text = (
        "RSS 1 100.500 0 4 4 1500.00 7 12.50 12000 0 255 0 455\n"
        "40 41 42 43\n"
        "38 39 40 41\n"
    )
    record = parse_one(text)
    assert isinstance(record, RawSidescan)
    assert record.port == (40, 41, 42, 43)
    assert record.starboard == (38, 39, 40, 41)
    assert record.frequency == 455


def test_rss_truncated_is_malformed():
    record = parse_one("RSS 1 100.5 0 4 4 1500.00 7 12.5 12000 0 255 0 455\n40 41 42 43\n")
    assert isinstance(record, MalformedRecord)
    assert "starboard" in record.error
