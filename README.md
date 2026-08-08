# hydroformats

**Pure-Python parsers for hydrographic survey logs: HYPACK® RAW, HYSWEEP®
HSX, and the HS2X binary edit format.**

[![CI](https://github.com/Parasphere-Solutions/hydroformats/actions/workflows/ci.yml/badge.svg)](https://github.com/Parasphere-Solutions/hydroformats/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](pyproject.toml)

Hydrographic survey loggers write files that outlive every software
package that made them: decades of `.RAW`, `.HSX`, and `.HS2X` files sit
in agency and contractor archives. The text formats are documented across
scattered manuals, federal metadata pages, and one venerable C codebase;
the binary edit format is documented nowhere at all. There has been no
small, typed, dependency-free way to read any of them from Python. This is
that library.

- **Zero dependencies.** Standard library only. `pip install hydroformats`
  and go.
- **Typed, immutable records.** Every line becomes a frozen dataclass with
  documented fields, not a dict of strings.
- **Streaming-first.** Multi-gigabyte survey files parse as generators.
- **Never crashes on a bad line.** Real files contain truncated pings and
  odd trailers; you get `MalformedRecord`/`UnknownRecord` values, not
  exceptions.
- **Source-anchored.** Every field layout traces to a cited source in
  [docs/FORMAT-SOURCES.md](docs/FORMAT-SOURCES.md) — public documents for
  the text dialects, a documented empirical cross-validation for the
  binary HS2X (no public spec exists). Unanchored records parse as
  `UnknownRecord`/`Hs2xOpaque`, unanchored fields ride along verbatim as
  `unassigned` — nothing in this library is guessed.
- **Validated on real survey data.** Parses genuine USGS-logged HYPACK
  files (federal survey 2014-009-FA) with zero malformed and zero unknown
  data records; verbatim excerpts from those public-domain files form part
  of the test suite. The HS2X decoder is validated field-by-field against
  a paired HSX log of the same session (see FORMAT-SOURCES, S5).

## 30 seconds

```python
from hydroformats import open_session

session = open_session("0000_1346.RAW")   # dialect is sniffed (RAW vs HSX)
print(session.dialect)                     # "raw"
print(session.header.devices)              # {0: Device(name='GPS', ...), ...}

for record in session.records():           # streams; files can be huge
    if record.tag == "EC1":
        print(record.time, record.depth)   # seconds past midnight, meters
```

Multibeam pings arrive assembled — header line plus per-beam arrays:

```python
from hydroformats import open_session
from hydroformats.records import RawMultibeam

for record in open_session("survey.HSX").records():
    if isinstance(record, RawMultibeam):
        print(record.ping, record.num_beams, record.depths[:4])
```

Binary HS2X files stream beam-solved soundings (grid position + elevation
per beam, metric centimetres), with unfilled swath slots flagged:

```python
from hydroformats import open_session
from hydroformats.records import Hs2xSounding

for record in open_session("survey.HS2x").records():
    if isinstance(record, Hs2xSounding) and not record.is_no_detect:
        print(record.easting_m, record.northing_m, record.elevation_m)
```

## Command line

```console
$ hydroformats info survey.raw
file:      survey.raw
dialect:   raw
started:   2010-12-01 13:46:16
devices:
  0: Synthetic GNSS
  1: Synthetic Echosounder
  2: Synthetic MRU
records:
  EC1: 5
  GYR: 5
  HCP: 5
  ...

$ hydroformats records survey.hsx --type RMB --limit 1 --json
{"tag": "RMB", "device": 1, "time": 49574.05, "ping": 1, "num_beams": 8, ...}

$ hydroformats to-csv survey.raw --type EC1 -o depths.csv
$ hydroformats to-jsonl survey.hsx -o everything.jsonl
```

## Format support

| Tag | Meaning | RAW | HSX | Anchor |
|-----|---------|:---:|:---:|--------|
| POS | Grid position (easting/northing, + observed trailing field) | ✅ | ✅ | USGS · MB-System · real data |
| RAW | Raw GNSS lat/lon/alt | ✅ | — | USGS |
| EC1 | Echosounder depth | ✅ | ✅ | USGS · MB-System |
| GYR | Heading | ✅ | ✅ | USGS · MB-System |
| HCP | Heave/roll/pitch | ✅ | ✅ | USGS · MB-System |
| TID | Tide correction | ✅ | ✅ | USGS · MB-System |
| QUA | GNSS quality (+GST extras; float-formatted ints tolerated) | ✅ | — | USGS · real data |
| KTC | RTK water level | ✅ | — | USGS |
| MSG | Device message (NMEA…) | ✅ | ✅ | USGS |
| FIX | Event mark (3- and 5-field) | ✅ | ✅ | USGS · example file |
| DFT | Dynamic draft | — | ✅ | MB-System |
| GPS | COG/SOG/HDOP/mode/sats | — | ✅ | MB-System |
| PSA | Pitch stabilization | — | ✅ | MB-System |
| SNR | Sonar runtime settings | — | ✅ | MB-System |
| RMB | Multibeam ping (multi-line, 15 optional arrays) | — | ✅ | MB-System |
| RSS | Sidescan ping (multi-line) | — | ✅ | MB-System |
| DEV/DV2/OFF/OF2/PRI/MBI/SSI/HSP | Device registry & geometry | ✅/— | ✅ | USGS · MB-System |
| FTP/VER/HSX/TND/INF/LIN/LBP/LNN/PTS/EOL/EOH/PRJ | File & survey header | ✅ | ✅ | all three |
| ELL/PRO/DTM/GEO/HVU/LTP | RAW geodesy header (verbatim fields) | ✅ | — | example file |
| EC2 | Dual-frequency depth | ⚠ `UnknownRecord` | — | attested, layout unanchored |

**HS2X** (binary, `.HS2x`) is a separate dialect with numeric record
types rather than tags:

| Type | Meaning | Decoded | Anchor |
|------|---------|:-------:|--------|
| 26 | File header (`DATAGRAM VERSION …`) | ✅ | S5 |
| 68 | Ping header: time, ping number, device, SV, grid position, heading/roll/pitch | ✅ | S5 (equal to the paired HSX RMB/GYR/HCP series) |
| 69 | Beam-solved sounding: grid cm, elevation cm, beam angle; no-detect sentinel | ✅ | S5 (EC1/TID/POS agreement) |
| 60/61/62/63/67 | Tide, time marks, gyro, attitude, position (+ packed lat/lon, UTC) | ✅ | S5 |
| 70/72 | Sidescan header + u32 samples | ✅ | S5 |
| 50–55 | Configuration block | ⚠ `Hs2xOpaque` | payloads undecoded |

Everything else parses as `UnknownRecord`/`Hs2xOpaque` with content
preserved. If you have documentation for a record we don't cover, please
open an issue with a source we can cite.

## Testing your pipeline without real data

Real survey data is rarely shareable. The synthetic writers produce
structurally-faithful files for exercising downstream code:

```python
from hydroformats import write_raw, write_hsx, write_hs2x, SyntheticSurvey

write_raw("test.raw", SyntheticSurvey(fixes=100))
write_hsx("test.hsx", beams=64, pings=50)
write_hs2x("test.HS2x", beams=64, pings=50)
```

Public real-world data to graduate to: USGS field-activity archives log
genuine HYPACK navigation files, and USACE **eHydro** publishes processed
channel surveys nationally — see
[examples/fetch_public_data.py](examples/fetch_public_data.py).

## Field notes

Things the formats don't tell you loudly:

- **Time tags are seconds past midnight** of the `TND` header date, as a
  float. Cross-midnight surveys wrap; correlate with `RAW`'s `utc` field
  when it matters.
- **Positions are projected grid coordinates.** The file names its
  ellipsoid/projection (`ELL`/`PRO`, or MB-System's `PRJ` extension) but
  carries no EPSG code. Know your project's CRS before trusting eastings.
- **`RAW` lat/lon are packed** as `ddmmmm.mmmm` — divide by 100 to get
  NMEA-style `ddmm.mmmmm`. (One widely-indexed federal metadata page says
  "multiply"; the actual data files from that same survey prove division —
  a real line `RAW 1 39052.101 4 410966.80360 -714331.75760 …` decodes to
  41.1611°N, 71.7220°W, matching its companion UTM `POS` record. See the
  anchor errata in FORMAT-SOURCES.) The record class exposes decoded
  `latitude_degrees`/`longitude_degrees`; hemisphere sign follows the
  logged value's sign (all public examples we hold are N/W — treat S/E
  data as unverified and check yours).
- **HCP sign conventions:** roll positive = port up; pitch positive = bow
  up (per USGS metadata; MB-System negates roll into its own convention,
  which confirms the logged order).
- **RMB is a multi-line record.** The header's `beam_data_available`
  bitmask declares which per-beam arrays follow, one line each in ascending
  bitmask order — except sounding-XY (0x0004), which contributes two lines
  (eastings, then northings).
- **HS2X integers are metric centimetres** (grid coordinates, elevations)
  and milli/centidegrees (angles), regardless of the survey's grid units.
  On a US-survey-foot grid divide by **30.4800609601**, not 30.48 — the
  2 ppm international-foot shortcut moves State Plane coordinates by tens
  of feet. Elevations are negative below datum. Unfilled swath slots are
  stored as soundings parked at the transducer position with zero return
  (`is_no_detect`) — filter them before gridding; on the session we
  validated against they were 49% of all type-69 records.
- **An HS2X file is not necessarily edited data.** HYPACK's editor writes
  HS2X from the moment raw data is loaded ("auto save HSX to HS2X");
  deletion flags exist in the format per HYPACK's manuals but are not yet
  located by this library (our validation capture predates any editing).

## Development

```console
$ uv sync --extra dev
$ uv run pytest              # 79 tests, 98% coverage
$ uv run ruff check .
```

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Format
claims require a citable public source; that rule is the project.

## License & trademarks

Apache-2.0. HYPACK® and HYSWEEP® are registered trademarks of Xylem Inc.
or its subsidiaries; this is an independent interoperability project, not
affiliated with or endorsed by Xylem. See [NOTICE](NOTICE).

---

Maintained by [Parasphere Solutions](https://paraspheresolutions.com) — a
service-disabled-veteran-owned company building inspection intelligence
software for infrastructure above and below the waterline.
