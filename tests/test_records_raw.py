"""RAW dialect: per-record parsing against source-anchored examples.

Line examples come from the Hydromagic sample file and USGS metadata
formats (docs/FORMAT-SOURCES.md) — parsing them exactly is the contract.
"""
import io

import pytest

from hydroformats.raw import parse_raw
from hydroformats.records import (
    Device,
    DeviceOffsets,
    Echosounding,
    FixMark,
    HeaderMisc,
    KinematicTide,
    MalformedRecord,
    Message,
    Position,
    Quality,
    RawPosition,
    SurveyInfo,
    Tide,
    TimeDate,
    UnknownRecord,
)


def parse_one(line: str):
    records = list(parse_raw(io.StringIO(line)))
    assert len(records) == 1
    return records[0]


def test_pos_from_hydromagic_example():
    record = parse_one("POS 0 49573.971 454468.123 4945274.185")
    assert record == Position(tag="POS", device=0, time=49573.971,
                              x=454468.123, y=4945274.185)


def test_ec1_from_hydromagic_example():
    record = parse_one("EC1 0 49573.971 62.800")
    assert isinstance(record, Echosounding)
    assert record.depth == 62.8


def test_tid_from_hydromagic_example():
    record = parse_one("TID 0 49573.654 0.776")
    assert isinstance(record, Tide) and record.correction == 0.776


def test_fix_five_field_variant_from_example_file():
    record = parse_one("FIX 99 49576.534 1 454479.129 4945253.999")
    assert record == FixMark(tag="FIX", device=99, time=49576.534, event=1,
                             x=454479.129, y=4945253.999)


def test_fix_three_field_variant_per_usgs():
    record = parse_one("FIX 99 100.5 7")
    assert record == FixMark(tag="FIX", device=99, time=100.5, event=7, x=None, y=None)


def test_raw_position_per_usgs_semantics():
    # Logged "ddmmmm.mmmm": ×100 -> NMEA 4238.53910 = 42° 38.5391' (USGS S2).
    record = parse_one("RAW 0 49573.971 4 42.3853910 -73.5528410 -32.834 1346")
    assert isinstance(record, RawPosition)
    assert record.count == 4 and record.utc == "1346"
    assert record.latitude_raw == 42.385391  # stored untouched
    assert record.latitude_degrees == pytest.approx(42 + 38.5391 / 60, abs=1e-9)
    assert record.longitude_degrees == pytest.approx(-(73 + 55.2841 / 60), abs=1e-9)


def test_qua_with_gst_extras_per_usgs():
    record = parse_one("QUA 0 100.0 6 8.9 1.1 11 4 0.012 0.014 0.018")
    assert isinstance(record, Quality)
    assert record.hdop == 1.1 and record.satellites == 11 and record.mode == 4
    assert record.extras == (0.012, 0.014, 0.018)


def test_ktc_per_usgs():
    record = parse_one("KTC 0 100.0 7 -32.8 -32.8 -34.1 1.0 1.5 0.3 0.766")
    assert isinstance(record, KinematicTide)
    assert record.final_tide == 0.766 and record.undulation == -34.1


def test_msg_preserves_full_text():
    record = parse_one("MSG 1 100.0 $ECDPT,12.3,0.4 more words")
    assert isinstance(record, Message)
    assert record.text == "$ECDPT,12.3,0.4 more words"


def test_off_per_usgs_offsets():
    record = parse_one("OFF 1 0.200 1.100 0.450 0.000 0.000 0.000 0.050")
    assert isinstance(record, DeviceOffsets)
    assert record.forward == 1.1 and record.latency == 0.05 and record.offset_type is None


def test_dev_with_raw_dialect_extras():
    record = parse_one('DEV 0 276 "Simulation" 1 C:\\HYPACK\\testdev.dll 9.0.1.1')
    assert isinstance(record, Device)
    assert record.name == "Simulation" and record.capability == 276
    assert record.extras == ("1", "C:\\HYPACK\\testdev.dll", "9.0.1.1")


def test_inf_with_quoted_blanks():
    record = parse_one('INF "" "" "" "" 0.983268 0.000000 0.000000')
    assert isinstance(record, SurveyInfo)
    assert record.surveyor == "" and record.tide_correction == 0.983268


def test_tnd_keeps_unanchored_extra_field():
    record = parse_one("TND 13:46:16 12/01/2010 300")
    assert isinstance(record, TimeDate)
    assert (record.year, record.month, record.day) == (2010, 12, 1)
    assert record.extras == ("300",)


def test_header_misc_records_carried_verbatim():
    record = parse_one("ELL WGS-84 6378137.000 298.257223563")
    assert isinstance(record, HeaderMisc)
    assert record.fields[0] == "WGS-84"


def test_ec2_is_unknown_not_guessed():
    record = parse_one("EC2 0 100.0 12.5 13.1")
    assert isinstance(record, UnknownRecord)


def test_malformed_known_record_degrades_with_error():
    record = parse_one("POS 0 not-a-number 1 2")
    assert isinstance(record, MalformedRecord)
    assert record.tag == "POS" and "could not convert" in record.error


def test_unknown_tag_is_preserved():
    record = parse_one("ZZZ 1 2 3")
    assert isinstance(record, UnknownRecord)
    assert record.fields == ("1", "2", "3")
