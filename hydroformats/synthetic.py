"""Synthetic survey file writers.

These generate structurally-faithful RAW and HSX files for testing parsers
and downstream pipelines without real survey data (which is rarely shareable).
They are a supported feature, not just a test fixture: point your ingest at
a synthetic file before you point it at a customer's.

Field layouts mirror the anchored specifications in docs/FORMAT-SOURCES.md;
values are plausible but fictional (a small survey pattern near a pier).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SyntheticSurvey:
    """Parameters for a synthetic survey; defaults make a tiny, readable file."""

    device_gps: int = 0
    device_sounder: int = 1
    device_mru: int = 2
    start_seconds: float = 49573.0
    easting: float = 454468.0
    northing: float = 4945274.0
    depth: float = 12.5
    fixes: int = 3


_DEFAULT_SURVEY = SyntheticSurvey()


def write_raw(path: str | Path, survey: SyntheticSurvey = _DEFAULT_SURVEY) -> Path:
    """Write a synthetic HYPACK RAW file (single-beam dialect)."""
    path = Path(path)
    s = survey
    lines: list[str] = [
        "FTP NEW 2",
        "VER 21.0.0.0",
        'INF "synthetic surveyor" "synthetic boat" "hydroformats" "test reach" 0.00 0.30 1500.00',
        "ELL WGS-84 6378137.000 298.257223563",
        "PRO UTM 18 N",
        "TND 13:46:16 12/01/2010 300",
        f'DEV {s.device_gps} 100 "Synthetic GNSS"',
        f"OFF {s.device_gps} 0.000 0.000 1.500 0.000 0.000 0.000 0.000",
        f'DEV {s.device_sounder} 16 "Synthetic Echosounder"',
        f"OFF {s.device_sounder} 0.200 1.100 0.450 0.000 0.000 0.000 0.050",
        f'DEV {s.device_mru} 512 "Synthetic MRU"',
        f"OFF {s.device_mru} 0.000 0.000 0.000 0.000 0.000 0.000 0.000",
        "LIN 2",
        f"PTS {s.easting:.2f} {s.northing:.2f}",
        f"PTS {s.easting + 120:.2f} {s.northing - 80:.2f}",
        f"LBP {s.easting:.2f} {s.northing:.2f}",
        "LNN 1",
        "EOL",
        "EOH",
    ]
    for i in range(s.fixes):
        t = s.start_seconds + i * 0.62
        x = s.easting + i * 2.4
        y = s.northing - i * 1.6
        depth = s.depth + 0.15 * math.sin(i)
        lines += [
            f"POS {s.device_gps} {t:.3f} {x:.3f} {y:.3f}",
            f"RAW {s.device_gps} {t:.3f} 4 42.3853910 -73.5528410 -32.834 1346{i:02d}",
            f"QUA {s.device_gps} {t:.3f} 6 8.9 1.1 11 4 0.012 0.014 0.018",
            f"EC1 {s.device_sounder} {t:.3f} {depth:.2f}",
            f"HCP {s.device_mru} {t:.3f} 0.04 {0.8 * math.sin(i):.2f} {0.5 * math.cos(i):.2f}",
            f"GYR {s.device_mru} {t:.3f} {118.0 + i:.1f}",
            f"TID {s.device_gps} {t:.3f} 0.78",
            f"KTC {s.device_gps} {t:.3f} 7 -32.834 -32.834 -34.100 1.000 1.500 0.300 0.766",
            f"MSG {s.device_gps} {t:.3f} "
            f"$GPGGA,134616.00,4238.5391,N,07355.2841,W,4,11,1.1,12.1,M,,M,,",
        ]
    lines.append(f"FIX 99 {s.start_seconds + 1.0:.3f} 1 {s.easting:.3f} {s.northing:.3f}")
    path.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    return path


def write_hsx(
    path: str | Path,
    survey: SyntheticSurvey = _DEFAULT_SURVEY,
    beams: int = 8,
    pings: int = 2,
    with_sidescan: bool = True,
) -> Path:
    """Write a synthetic HYSWEEP HSX file (multibeam dialect).

    The RMB bitmask used is 0x1809: beam ranges (0x0001), sounding depths
    (0x0008), intensities (0x0800), quality (0x1000) — one follow-on line
    each, in bitmask order, matching the MB-System writer's layout.
    """
    path = Path(path)
    s = survey
    available = 0x0001 | 0x0008 | 0x0800 | 0x1000
    lines: list[str] = [
        "FTP NEW 2",
        "HSX 8",
        "VER 21.0.0.0",
        "TND 13:46:16 12/01/2010",
        'INF "synthetic surveyor" "synthetic boat" "hydroformats" "test pier" 0.00 0.30 1500.00',
        "HSP 0.50 40.00 25.00 25.00 45 45 3 1 30.00 0.00 1 0",
        f'DEV {s.device_gps} 100 "Synthetic GNSS"',
        f"DV2 {s.device_gps} 4 0 1",
        f"OF2 {s.device_gps} 0 0.00 0.00 1.50 0.00 0.00 0.00 0.00",
        f'DEV {s.device_sounder} 32784 "Synthetic Multibeam"',
        f"DV2 {s.device_sounder} 1 0 1",
        f"OF2 {s.device_sounder} 0 0.20 1.10 0.45 0.00 0.00 0.00 0.05",
        f"MBI {s.device_sounder} 1 0 {available:x} {beams} 0 -60.000 15.000",
        f"SSI {s.device_sounder} 0 {beams} {beams}",
        "PRI 1",
        "EOH",
    ]
    for ping in range(1, pings + 1):
        t = s.start_seconds + ping * 1.05
        x = s.easting + ping * 2.0
        y = s.northing - ping * 1.4
        lines += [
            f"POS {s.device_gps} {t:.3f} {x:.2f} {y:.2f}",
            f"GYR {s.device_mru} {t:.3f} {118.0 + ping:.2f}",
            f"HCP {s.device_mru} {t:.3f} 0.03 0.40 -0.20",
            f"DFT {s.device_sounder} {t:.3f} 0.45",
            f"GPS {s.device_gps} {t:.3f} 117.5 2.1 1.1 4 11",
            f"PSA {s.device_sounder} {t:.3f} {ping} 0.10 -0.05",
            f"SNR {s.device_sounder} {t:.3f} {ping} 0 3 210 12.5 0.9",
        ]
        if with_sidescan:
            port = " ".join(str(40 + (i * 7 + ping) % 50) for i in range(beams))
            stbd = " ".join(str(38 + (i * 5 + ping) % 50) for i in range(beams))
            lines += [
                f"RSS {s.device_sounder} {t:.3f} 0 {beams} {beams} 1500.00 {ping} "
                f"{s.depth:.2f} 12000 0 255 0 455",
                port,
                stbd,
            ]
        ranges = " ".join(f"{s.depth / math.cos(math.radians(-60 + 15 * i)):.2f}"
                          for i in range(beams))
        depths = " ".join(f"{s.depth + 0.2 * math.sin(i + ping):.2f}" for i in range(beams))
        intens = " ".join(str(100 + (i * 3 + ping) % 40) for i in range(beams))
        quality = " ".join("3" for _ in range(beams))
        lines += [
            f"RMB {s.device_sounder} {t:.3f} 1 0 {available:x} {beams} 1500.00 {ping}",
            ranges,
            depths,
            intens,
            quality,
        ]
    path.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    return path
