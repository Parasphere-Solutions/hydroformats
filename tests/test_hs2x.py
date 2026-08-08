"""HS2X binary dialect: TLV framing, record decoding, session and CLI.

Fixtures are synthetic bytes built in-test (or by ``write_hs2x``); no real
survey data is used or required. Layouts follow the empirical anchor S5 in
docs/FORMAT-SOURCES.md.
"""
import json
import struct

import pytest

from hydroformats import open_session, sniff_dialect
from hydroformats.cli import main
from hydroformats.hs2x import iter_frames, parse_hs2x
from hydroformats.records import (
    EndOfHeader,
    Hs2xAttitude,
    Hs2xFileHeader,
    Hs2xHeading,
    Hs2xOpaque,
    Hs2xPing,
    Hs2xPosition,
    Hs2xSidescanData,
    Hs2xSidescanHeader,
    Hs2xSounding,
    Hs2xTide,
    Hs2xTimeMark,
    MalformedRecord,
)
from hydroformats.synthetic import write_hs2x

# ---------------------------------------------------------------------------
# byte builders
# ---------------------------------------------------------------------------

VERSION_PAYLOAD = (
    b"DATAGRAM VERSION 112" + b"\x00" * 4 + b"03-FEB-2022" + b"\x00" * 5
    + struct.pack("<I", 2)
)


def bootstrap() -> bytes:
    return struct.pack("<HH", len(VERSION_PAYLOAD), 26) + VERSION_PAYLOAD


def frame(prev_size: int, record_type: int, payload: bytes) -> bytes:
    return struct.pack("<HHH", prev_size, len(payload), record_type) + payload


def sounding_payload(
    easting_cm: int = 45_446_923,
    northing_cm: int = 494_527_456,
    elevation_cm: int = -1250,
    beam_angle_cdeg: int = -2345,
    u: tuple[int, ...] = (0, -70650, 2400, 0, 917, 65536, 0, 27, -12000, 0, 0),
) -> bytes:
    return struct.pack(
        "<7i2h2i2h2i",
        easting_cm, northing_cm, elevation_cm, beam_angle_cdeg,
        u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7], u[8], u[9], u[10],
    )


NO_DETECT_U = (0, -7000, 0, 0, 1, 0, 0, 41, 1, 0, 0)


def ping_payload(
    time_ms: int = 49_573_000,
    device: int = 1,
    sonar_type: int = 516,
    beam_count: int = 2,
    sound_velocity_cm_s: int = 151_234,
    ping_number: int = 1_000,
    easting_cm: int = 45_446_800,
    northing_cm: int = 494_527_400,
    heading_millideg: int = 118_000,
    roll_millideg: int = 400,
    pitch_millideg: int = -200,
) -> bytes:
    head = struct.pack(
        "<iI4H2i2i2i2i2i2i2H",
        time_ms, 0xCA5CD27E,
        device, sonar_type, beam_count, 0,
        sound_velocity_cm_s, ping_number,
        easting_cm, northing_cm,
        0, heading_millideg,
        0, -5,
        roll_millideg, pitch_millideg,
        134, 118_150,
        205, 2304,
    )
    tail = bytearray(76)
    tail[28:38] = b"\x01\x03\x00\x00\x00\x01\x01\x00\x00\x00"
    assert len(head) + len(tail) == 144
    return head + bytes(tail)


def gyr_payload(time_ms: int = 49_573_042, device: int = 1,
                heading_millideg: int = 118_020) -> bytes:
    return struct.pack("<i2Hi", time_ms, device, 0, heading_millideg)


def hcp_payload(time_ms: int = 49_573_042, device: int = 1,
                roll_millideg: int = 400, pitch_millideg: int = -200) -> bytes:
    return struct.pack("<i2H6i", time_ms, device, 4, 0, 0, roll_millideg, 0,
                       pitch_millideg, 0)


def pos_payload(time_ms: int = 49_573_100, easting_cm: int = 45_446_800,
                northing_cm: int = 494_527_400) -> bytes:
    return struct.pack(
        "<2i4i2i4H4d",
        time_ms, 0,
        easting_cm, northing_cm, easting_cm, northing_cm,
        134, 118_300,
        200, 20, 9, 256,
        423_853.9100, -735_528.4100, -32.834, 63_973.0,
    )


def tid_payload(time_ms: int = 49_573_060, device: int = 0,
                tide_cm: int = 78) -> bytes:
    return struct.pack("<i2H2i2H", time_ms, device, 0, -5, -6, tide_cm, 9)


def timemark_payload(time_ms: int = 49_573_000) -> bytes:
    return struct.pack("<3i", time_ms, 0, 0)


def ss_header_payload(time_ms: int = 49_573_000, device: int = 1,
                      port_samples: int = 4, starboard_samples: int = 4,
                      sound_velocity_cm_s: int = 151_234,
                      ping_number: int = 1_000,
                      easting_cm: int = 45_446_800,
                      northing_cm: int = 494_527_400,
                      heading_millideg: int = 118_400) -> bytes:
    return struct.pack(
        "<i4H8i",
        time_ms, device, port_samples, starboard_samples, 0,
        sound_velocity_cm_s, ping_number, 0, 61_447, 0x7FFFFFFF, 0x01000000,
        easting_cm, northing_cm,
    ) + struct.pack("<i", heading_millideg)


def chain(*records: tuple[int, bytes]) -> bytes:
    """Assemble bootstrap + records, maintaining the prev-size links."""
    out = bootstrap()
    prev = len(VERSION_PAYLOAD)
    for record_type, payload in records:
        out += frame(prev, record_type, payload)
        prev = len(payload)
    return out


# ---------------------------------------------------------------------------
# framing
# ---------------------------------------------------------------------------


def test_frames_walk_bootstrap_and_chain():
    data = chain((50, b"\x00" * 16), (69, sounding_payload()))
    frames = list(iter_frames(data))
    assert [f.record_type for f in frames] == [26, 50, 69]
    assert [f.size for f in frames] == [44, 16, 52]
    assert frames[0].offset == 0
    assert frames[1].offset == 4 + 44
    assert all(f.link_ok for f in frames)
    assert frames[2].payload == sounding_payload()


def test_frames_tolerate_trailing_prev_size_echo():
    data = chain((50, b"\x00" * 16)) + struct.pack("<H", 16)
    frames = list(iter_frames(data))
    assert [f.record_type for f in frames] == [26, 50]


def test_frames_flag_broken_prev_link():
    good = chain((50, b"\x00" * 16))
    bad = good + frame(9999, 51, b"\x00" * 8)  # wrong prev (should be 16)
    frames = list(iter_frames(bad))
    assert [f.link_ok for f in frames] == [True, True, False]


def test_frames_short_payload_at_eof_is_yielded_short():
    data = chain() + struct.pack("<HHH", 44, 52, 69) + b"\x00" * 10
    frames = list(iter_frames(data))
    assert frames[-1].size == 52
    assert len(frames[-1].payload) == 10


def test_frames_reject_non_hs2x_bytes():
    assert list(iter_frames(b"")) == []
    assert list(iter_frames(b"\x01")) == []


# ---------------------------------------------------------------------------
# record decoding
# ---------------------------------------------------------------------------


def _records(data: bytes):
    return list(parse_hs2x(data))


def test_file_header_decodes_version_and_build_date():
    records = _records(chain())
    header = records[0]
    assert isinstance(header, Hs2xFileHeader)
    assert header.version == 112
    assert header.text == "DATAGRAM VERSION 112"
    assert header.build_date == "03-FEB-2022"


def test_sounding_decodes_solved_fields_and_unassigned_words():
    records = _records(chain((69, sounding_payload())))
    snd = next(r for r in records if isinstance(r, Hs2xSounding))
    assert snd.easting_cm == 45_446_923
    assert snd.northing_cm == 494_527_456
    assert snd.elevation_cm == -1250
    assert snd.beam_angle_cdeg == -2345
    assert snd.unassigned == (0, -70650, 2400, 0, 917, 65536, 0, 27, -12000, 0, 0)
    assert not snd.is_no_detect
    # metric centimetre integers convert exactly to metres
    assert snd.easting_m == pytest.approx(454_469.23)
    assert snd.northing_m == pytest.approx(4_945_274.56)
    assert snd.elevation_m == pytest.approx(-12.50)
    assert snd.beam_angle_degrees == pytest.approx(-23.45)


def test_sounding_no_detect_sentinel():
    payload = sounding_payload(
        easting_cm=45_446_800, northing_cm=494_527_400, elevation_cm=-90,
        beam_angle_cdeg=138, u=NO_DETECT_U,
    )
    snd = next(r for r in _records(chain((69, payload)))
               if isinstance(r, Hs2xSounding))
    assert snd.is_no_detect


def test_ping_decodes_navigation_and_attitude():
    records = _records(chain((68, ping_payload())))
    ping = next(r for r in records if isinstance(r, Hs2xPing))
    assert ping.time_ms == 49_573_000
    assert ping.device == 1
    assert ping.sonar_type == 516
    assert ping.beam_count == 2
    assert ping.sound_velocity_cm_s == 151_234
    assert ping.ping_number == 1_000
    assert ping.easting_cm == 45_446_800
    assert ping.northing_cm == 494_527_400
    assert ping.heading_millideg == 118_000
    assert ping.roll_millideg == 400
    assert ping.pitch_millideg == -200
    assert ping.time == pytest.approx(49_573.0)


def test_heading_attitude_tide_and_timemark_decode():
    records = _records(chain(
        (61, timemark_payload(49_573_000)),
        (62, gyr_payload(49_573_042, 1, 118_020)),
        (63, hcp_payload(49_573_042, 1, 400, -200)),
        (60, tid_payload(49_573_060, 0, 78)),
    ))
    mark = next(r for r in records if isinstance(r, Hs2xTimeMark))
    assert mark.time_ms == 49_573_000
    gyr = next(r for r in records if isinstance(r, Hs2xHeading))
    assert (gyr.device, gyr.heading_millideg) == (1, 118_020)
    assert gyr.heading_degrees == pytest.approx(118.020)
    hcp = next(r for r in records if isinstance(r, Hs2xAttitude))
    assert (hcp.device, hcp.roll_millideg, hcp.pitch_millideg) == (1, 400, -200)
    tid = next(r for r in records if isinstance(r, Hs2xTide))
    assert (tid.device, tid.tide_cm) == (0, 78)


def test_position_decodes_grid_and_packed_geographic():
    records = _records(chain((67, pos_payload())))
    pos = next(r for r in records if isinstance(r, Hs2xPosition))
    assert pos.easting_cm == 45_446_800
    assert pos.northing_cm == 494_527_400
    assert pos.latitude_packed == pytest.approx(423_853.9100)
    assert pos.longitude_packed == pytest.approx(-735_528.4100)
    # ddmmmm.mmmm / 100 -> 42 deg 38.539100 min -> 42 + 38.539100/60
    assert pos.latitude_degrees == pytest.approx(42.6423183, abs=1e-6)
    assert pos.longitude_degrees == pytest.approx(-73.9214017, abs=1e-6)
    assert pos.ellipsoid_height == pytest.approx(-32.834)
    assert pos.utc_seconds == pytest.approx(63_973.0)


def test_sidescan_header_and_data_decode():
    samples = struct.pack("<8I", 3, 7, 11, 15, 2, 4, 6, 8)
    records = _records(chain((70, ss_header_payload()), (72, samples)))
    ssh = next(r for r in records if isinstance(r, Hs2xSidescanHeader))
    assert (ssh.port_samples, ssh.starboard_samples) == (4, 4)
    assert ssh.ping_number == 1_000
    assert ssh.sound_velocity_cm_s == 151_234
    assert ssh.easting_cm == 45_446_800
    assert ssh.heading_millideg == 118_400
    ssd = next(r for r in records if isinstance(r, Hs2xSidescanData))
    assert ssd.values() == (3, 7, 11, 15, 2, 4, 6, 8)


def test_unknown_record_type_is_opaque_not_guessed():
    records = _records(chain((57, b"\xde\xad\xbe\xef")))
    opaque = next(r for r in records if isinstance(r, Hs2xOpaque))
    assert opaque.record_type == 57
    assert opaque.payload == b"\xde\xad\xbe\xef"
    assert opaque.tag == "T57"


def test_short_payload_is_malformed_not_fatal():
    data = chain((50, b"\x00" * 8)) + struct.pack("<HHH", 8, 52, 69) + b"\x00" * 12
    records = _records(data)
    bad = [r for r in records if isinstance(r, MalformedRecord)]
    assert len(bad) == 1
    assert "52" in bad[0].error or "truncated" in bad[0].error


def test_end_of_header_is_emitted_before_first_data_record():
    records = _records(chain((50, b"\x00" * 8), (61, timemark_payload())))
    kinds = [type(r).__name__ for r in records]
    assert kinds.index("EndOfHeader") == kinds.index("Hs2xTimeMark") - 1


# ---------------------------------------------------------------------------
# synthetic writer round-trip
# ---------------------------------------------------------------------------


def test_synthetic_hs2x_roundtrip(tmp_path):
    path = write_hs2x(tmp_path / "line.HS2x", beams=6, pings=2)
    records = list(parse_hs2x(path))
    pings = [r for r in records if isinstance(r, Hs2xPing)]
    soundings = [r for r in records if isinstance(r, Hs2xSounding)]
    assert len(pings) == 2
    assert len(soundings) == 12
    assert all(p.beam_count == 6 for p in pings)
    assert pings[1].ping_number == pings[0].ping_number + 1
    # every frame link is intact, including after variable-size records
    assert all(f.link_ok for f in iter_frames(path))
    # sentinel beams appear and are detectable
    assert any(s.is_no_detect for s in soundings)
    assert any(not s.is_no_detect for s in soundings)


def test_synthetic_hs2x_without_sidescan(tmp_path):
    path = write_hs2x(tmp_path / "line.HS2x", beams=4, pings=1, with_sidescan=False)
    records = list(parse_hs2x(path))
    assert not [r for r in records if isinstance(r, Hs2xSidescanHeader)]
    assert not [r for r in records if isinstance(r, MalformedRecord)]


# ---------------------------------------------------------------------------
# session + CLI
# ---------------------------------------------------------------------------


def test_sniff_and_session_summary(tmp_path):
    path = write_hs2x(tmp_path / "line.HS2x", beams=3, pings=2)
    assert sniff_dialect(path) == "hs2x"
    session = open_session(path)
    assert session.dialect == "hs2x"
    summary = session.summary()
    assert summary["record_counts"]["SND"] == 6
    assert summary["record_counts"]["PING"] == 2
    header_tags = [r.tag for r in session.header.records]
    assert "HS2X" in header_tags
    assert isinstance(session.header.records[-1], EndOfHeader)


def test_cli_info_and_csv_export(tmp_path, capsys):
    path = write_hs2x(tmp_path / "line.HS2x", beams=3, pings=1)
    assert main(["info", str(path)]) == 0
    out = capsys.readouterr().out
    assert "hs2x" in out
    csv_path = tmp_path / "soundings.csv"
    assert main(["to-csv", str(path), "--type", "SND", "-o", str(csv_path)]) == 0
    lines = csv_path.read_text().strip().splitlines()
    assert lines[0].startswith("tag,easting_cm,northing_cm,elevation_cm")
    assert len(lines) == 4  # header + 3 beams


def test_cli_jsonl_renders_binary_payloads_as_hex(tmp_path, capsys):
    path = write_hs2x(tmp_path / "line.HS2x", beams=2, pings=1)
    assert main(["to-jsonl", str(path)]) == 0
    out = capsys.readouterr().out
    ssd = [json.loads(line) for line in out.splitlines()
           if json.loads(line)["tag"] == "SSD"]
    assert ssd, "sidescan data record missing from jsonl export"
    bytes.fromhex(ssd[0]["samples"])  # hex round-trips
