# hydroformats

**Pure-Python parsers for hydrographic survey logs: HYPACK® RAW and HYSWEEP® HSX.**

[![CI](https://github.com/Parasphere-Solutions/hydroformats/actions/workflows/ci.yml/badge.svg)](https://github.com/Parasphere-Solutions/hydroformats/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](pyproject.toml)

Hydrographic survey loggers write plain ASCII that outlives every software
package that made it: decades of `.RAW` and `.HSX` files sit in agency and
contractor archives. The formats are documented across scattered manuals,
federal metadata pages, and one venerable C codebase, but there has been no
small, typed, dependency-free way to read them from Python. This is that
library.

- **Zero dependencies.** Standard library only. `pip install hydroformats`
  and go.
- **Typed, immutable records.** Every line becomes a frozen dataclass with
  documented fields, not a dict of strings.
- **Streaming-first.** Multi-gigabyte survey files parse as generators.
- **Never crashes on a bad line.** Real files contain truncated pings and
  odd trailers; you get `MalformedRecord`/`UnknownRecord` values, not
  exceptions.
- **Source-anchored.** Every field layout traces to a public source, cited
  in [docs/FORMAT-SOURCES.md](docs/FORMAT-SOURCES.md). Where no public
  anchor exists, the record parses as `UnknownRecord` on purpose — nothing
  in this library is guessed.

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
| POS | Grid position (easting/northing) | ✅ | ✅ | USGS · MB-System |
| RAW | Raw GNSS lat/lon/alt | ✅ | — | USGS |
| EC1 | Echosounder depth | ✅ | ✅ | USGS · MB-System |
| GYR | Heading | ✅ | ✅ | USGS · MB-System |
| HCP | Heave/roll/pitch | ✅ | ✅ | USGS · MB-System |
| TID | Tide correction | ✅ | ✅ | USGS · MB-System |
| QUA | GNSS quality (+GST extras) | ✅ | — | USGS |
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

Everything else parses as `UnknownRecord` with fields preserved. If you have
documentation for a record we don't cover, please open an issue with a
source we can cite.

## Testing your pipeline without real data

Real survey data is rarely shareable. The synthetic writers produce
structurally-faithful files for exercising downstream code:

```python
from hydroformats import write_raw, write_hsx, SyntheticSurvey

write_raw("test.raw", SyntheticSurvey(fixes=100))
write_hsx("test.hsx", beams=64, pings=50)
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
- **`RAW` lat/lon are packed** as `ddmmmm.mmmm` (divide by 100 to get
  NMEA-style `ddmm.mmmmm`); the record class exposes decoded
  `latitude_degrees`/`longitude_degrees`.
- **HCP sign conventions:** roll positive = port up; pitch positive = bow
  up (per USGS metadata; MB-System negates roll into its own convention,
  which confirms the logged order).
- **RMB is a multi-line record.** The header's `beam_data_available`
  bitmask declares which per-beam arrays follow, one line each in ascending
  bitmask order — except sounding-XY (0x0004), which contributes two lines
  (eastings, then northings).

## Development

```console
$ uv sync --extra dev
$ uv run pytest              # 60 tests
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
