"""Functional tests: synthetic files through the full stack, plus fixtures.

The synthetic writers and the parsers are developed against the same
anchored specs but share no code path — a round-trip is a real check.
"""
from pathlib import Path

from hydroformats import Session, SyntheticSurvey, open_session, write_hsx, write_raw
from hydroformats.records import (
    Attitude,
    Echosounding,
    MalformedRecord,
    Position,
    RawMultibeam,
    RawSidescan,
    UnknownRecord,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_raw_roundtrip_full_fidelity(tmp_path):
    path = write_raw(tmp_path / "synthetic.raw", SyntheticSurvey(fixes=5))
    session = open_session(path)
    assert session.dialect == "raw"
    assert session.header.survey_info.project == "hydroformats"
    assert session.header.devices[1].name == "Synthetic Echosounder"
    records = session.load()
    assert not any(isinstance(r, (UnknownRecord, MalformedRecord)) for r in records)
    positions = [r for r in records if isinstance(r, Position)]
    soundings = [r for r in records if isinstance(r, Echosounding)]
    assert len(positions) == 5 and len(soundings) == 5
    assert positions[0].x == 454468.0
    attitudes = [r for r in records if isinstance(r, Attitude)]
    assert attitudes and all(a.device == 2 for a in attitudes)


def test_hsx_roundtrip_full_fidelity(tmp_path):
    path = write_hsx(tmp_path / "synthetic.hsx", beams=8, pings=3)
    session = open_session(path)
    assert session.dialect == "hsx"
    assert session.header.hsx_version == 8
    records = session.load()
    assert not any(isinstance(r, (UnknownRecord, MalformedRecord)) for r in records)
    pings = [r for r in records if isinstance(r, RawMultibeam)]
    assert len(pings) == 3
    for ping in pings:
        assert len(ping.beam_ranges) == 8
        assert len(ping.depths) == 8
        assert ping.eastings is None  # not in the synthetic bitmask
    sidescans = [r for r in records if isinstance(r, RawSidescan)]
    assert len(sidescans) == 3 and len(sidescans[0].port) == 8


def test_hsx_without_sidescan(tmp_path):
    path = write_hsx(tmp_path / "nosss.hsx", with_sidescan=False, pings=2)
    records = open_session(path).load()
    assert not any(isinstance(r, RawSidescan) for r in records)
    assert sum(isinstance(r, RawMultibeam) for r in records) == 2


def test_committed_fixtures_parse_clean():
    for name in ("mini.raw", "mini.hsx"):
        session = Session(FIXTURES / name)
        records = session.load()
        assert records, name
        bad = [r for r in records if isinstance(r, (UnknownRecord, MalformedRecord))]
        assert not bad, f"{name}: {bad[:3]}"


def test_summary_counts_are_stable(tmp_path):
    path = write_raw(tmp_path / "s.raw", SyntheticSurvey(fixes=2))
    summary = Session(path).summary()
    assert summary["dialect"] == "raw"
    assert summary["record_counts"]["POS"] == 2
    assert summary["record_counts"]["FIX"] == 1
    assert summary["survey_started"] == "2010-12-01 13:46:16"
    assert summary["devices"] == {
        0: "Synthetic GNSS", 1: "Synthetic Echosounder", 2: "Synthetic MRU",
    }


def test_streaming_matches_load(tmp_path):
    path = write_hsx(tmp_path / "s.hsx", pings=2)
    session = Session(path)
    streamed = list(session.records())
    assert tuple(streamed) == session.load()
