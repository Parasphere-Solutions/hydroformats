"""Regression tests built from REAL logged lines.

Every line below is quoted verbatim from USGS field activity 2014-009-FA
(public domain; file JD292/001_1050.292 in 2014-009-FA_hypack.zip,
cmgds.marine.usgs.gov). These caught two bugs the synthetic suite missed:
QUA logs its integer fields float-formatted, and the RAW packed-coordinate
decode divides by 100 (the metadata prose says multiply — the data proves
otherwise; see docs/FORMAT-SOURCES.md anchor errata).
"""
import io

import pytest

from hydroformats.raw import parse_raw
from hydroformats.records import (
    Echosounding,
    FixMark,
    KinematicTide,
    MalformedRecord,
    Position,
    Quality,
    RawPosition,
)

REAL_LINES = """\
QUA 1 39052.101 7 9.200 0.800 12.000 4.000 0.000 0.000 0.000
RAW 1 39052.101 4 410966.80360 -714331.75760 -26.57300 105052.00000
KTC 1 39052.101 7 -26.573 -26.573 -30.804 0.107 -4.420 0.000 -0.082
FIX 99 39053.848 592 775037.577 4561824.211
EC1 2 39052.528 17.720
POS 1 39052.101 775036.025 4561826.141 4.338
"""


def _parse_all():
    records = list(parse_raw(io.StringIO(REAL_LINES)))
    assert not [r for r in records if isinstance(r, MalformedRecord)], records
    return records


def test_no_real_line_is_malformed():
    _parse_all()


def test_qua_tolerates_float_formatted_integers():
    qua = next(r for r in _parse_all() if isinstance(r, Quality))
    assert qua.satellites == 12
    assert qua.mode == 4
    assert qua.count == 7
    assert qua.hdop == pytest.approx(0.800)
    assert qua.extras == (0.0, 0.0, 0.0)


def test_raw_position_decodes_by_division():
    raw = next(r for r in _parse_all() if isinstance(r, RawPosition))
    # 410966.8036 / 100 = 4109.668036 -> 41° + 09.668036'/60
    assert raw.latitude_degrees == pytest.approx(41.161134, abs=1e-6)
    assert raw.longitude_degrees == pytest.approx(-71.721960, abs=1e-6)
    assert raw.altitude == pytest.approx(-26.573)
    assert raw.utc == "105052.00000"


def test_raw_position_is_consistent_with_utm_position():
    """The decoded lat/lon must land in the same place as the UTM POS logged
    in the same second (zone 18N per the survey's PRO record). A pure-python
    sanity bound: 41.16°N is ~4,558 km of northing; the logged northing is
    4,561,826 m. Tolerance is generous — this guards against the ×100/÷100
    class of error (which is off by four orders of magnitude), not geodesy.
    """
    records = _parse_all()
    raw = next(r for r in records if isinstance(r, RawPosition))
    pos = next(r for r in records if isinstance(r, Position))
    approx_northing_km = raw.latitude_degrees * 110.9
    assert abs(approx_northing_km - pos.y / 1000.0) < 50


def test_pos_trailing_field_is_preserved():
    pos = next(r for r in _parse_all() if isinstance(r, Position))
    assert pos.x == pytest.approx(775036.025)
    assert pos.y == pytest.approx(4561826.141)
    assert pos.extras == ("4.338",)


def test_ktc_full_rtk_record():
    ktc = next(r for r in _parse_all() if isinstance(r, KinematicTide))
    assert ktc.ellipsoid_height == pytest.approx(-26.573)
    assert ktc.undulation == pytest.approx(-30.804)
    assert ktc.antenna_offset == pytest.approx(-4.420)
    assert ktc.final_tide == pytest.approx(-0.082)


def test_fix_five_field_event_mark():
    fix = next(r for r in _parse_all() if isinstance(r, FixMark))
    assert fix.device == 99
    assert fix.event == 592
    assert fix.x == pytest.approx(775037.577)


def test_ec1_depth():
    ec1 = next(r for r in _parse_all() if isinstance(r, Echosounding))
    assert ec1.depth == pytest.approx(17.720)
