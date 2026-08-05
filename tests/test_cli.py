"""CLI tests: every subcommand against synthetic files, plus failure paths."""
import csv
import json

import pytest

from hydroformats.cli import main
from hydroformats.synthetic import SyntheticSurvey, write_hsx, write_raw


@pytest.fixture
def raw_file(tmp_path):
    return write_raw(tmp_path / "survey.raw", SyntheticSurvey(fixes=3))


@pytest.fixture
def hsx_file(tmp_path):
    return write_hsx(tmp_path / "survey.hsx", beams=4, pings=2)


def test_info_reports_dialect_devices_counts(raw_file, capsys):
    assert main(["info", str(raw_file)]) == 0
    out = capsys.readouterr().out
    assert "dialect:   raw" in out
    assert "Synthetic Echosounder" in out
    assert "POS: 3" in out
    assert "started:   2010-12-01 13:46:16" in out


def test_info_on_hsx(hsx_file, capsys):
    assert main(["info", str(hsx_file)]) == 0
    out = capsys.readouterr().out
    assert "dialect:   hsx" in out
    assert "RMB: 2" in out


def test_records_filter_and_limit(raw_file, capsys):
    assert main(["records", str(raw_file), "--type", "POS", "--limit", "2"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    assert all("Position" in line for line in out)


def test_records_json_lines_are_valid(hsx_file, capsys):
    assert main(["records", str(hsx_file), "--type", "RMB", "--json"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    payloads = [json.loads(line) for line in lines]
    assert len(payloads) == 2
    assert payloads[0]["tag"] == "RMB"
    assert len(payloads[0]["beam_ranges"]) == 4


def test_records_no_match_warns_on_stderr(raw_file, capsys):
    assert main(["records", str(raw_file), "--type", "RMB"]) == 0
    assert "no records matched" in capsys.readouterr().err


def test_to_csv_writes_file(raw_file, tmp_path):
    out = tmp_path / "depths.csv"
    assert main(["to-csv", str(raw_file), "--type", "EC1", "-o", str(out)]) == 0
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 3
    assert set(rows[0]) == {"tag", "device", "time", "depth"}
    assert rows[0]["tag"] == "EC1"


def test_to_csv_without_matches_fails(raw_file, tmp_path, capsys):
    out = tmp_path / "none.csv"
    assert main(["to-csv", str(raw_file), "--type", "RMB", "-o", str(out)]) == 1
    assert "no parseable" in capsys.readouterr().err


def test_to_jsonl_covers_every_record(hsx_file, tmp_path):
    out = tmp_path / "all.jsonl"
    assert main(["to-jsonl", str(hsx_file), "-o", str(out)]) == 0
    tags = [json.loads(line)["tag"] for line in out.read_text().splitlines()]
    assert "RMB" in tags and "POS" in tags and "RSS" in tags


def test_missing_file_is_exit_1(capsys):
    assert main(["info", "no/such/file.raw"]) == 1
    assert "no such file" in capsys.readouterr().err


def test_usage_error_is_exit_2():
    with pytest.raises(SystemExit) as excinfo:
        main(["not-a-command"])
    assert excinfo.value.code == 2
