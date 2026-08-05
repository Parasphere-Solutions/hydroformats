#!/usr/bin/env python3
"""Fetch public real-world data to exercise hydroformats against.

Two public sources are worth knowing:

1. USGS field-activity archives — genuine HYPACK-logged navigation files
   from federal surveys (public domain). The catalog pages reject
   non-browser user agents, so this script sends a browser-like UA and, if
   still blocked, prints the URLs for a manual browser download.

2. USACE eHydro — the Corps' national archive of channel hydrosurveys
   (processed XYZ/GDB/KMZ per survey, not raw logger files):
   https://navigation.usace.army.mil/Survey/Hydro
   https://geospatial-usace.opendata.arcgis.com

Downloads land in ./data/ (gitignored). Nothing here is redistributed by
this repository; you fetch from the agencies directly.
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

# Direct download, verified working with a browser UA (≈42 MB zip of real
# HYPACK RAW logs from federal survey 2014-009-FA, public domain):
USGS_DIRECT = (
    "https://cmgds.marine.usgs.gov/data/field-activity-data/2014-009-FA/"
    "data/navigation/2014-009-FA_hypack.zip"
)

USGS_PAGES = [
    # Metadata/catalog pages describing HYPACK navigation data; follow the
    # download links they contain in a browser if automated fetch is blocked.
    "https://cmgds.marine.usgs.gov/data/field-activity-data/2014-009-FA/data/navigation/2014-009-FA_hypack_meta.html",
    "https://cmgds.marine.usgs.gov/catalog/whcmsc/open_file_report/ofr2010-1091/RB_Nav.hypack.faq.html",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


def fetch(url: str, destination: Path) -> bool:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            destination.write_bytes(response.read())
        print(f"  saved {destination} ({destination.stat().st_size:,} bytes)")
        return True
    except urllib.error.HTTPError as error:
        print(f"  blocked ({error.code}) — open in a browser instead: {url}")
        return False
    except OSError as error:
        print(f"  failed ({error}) — open in a browser instead: {url}")
        return False


def main() -> int:
    data = Path(__file__).parent.parent / "data"
    data.mkdir(exist_ok=True)
    print("USGS 2014-009-FA real HYPACK logs (public domain, ~42 MB):")
    fetch(USGS_DIRECT, data / USGS_DIRECT.rsplit("/", 1)[-1])
    print()
    print("USGS field-activity pages (HYPACK-logged navigation):")
    for url in USGS_PAGES:
        fetch(url, data / url.rsplit("/", 1)[-1])
    print()
    print("USACE eHydro (processed channel surveys, many under bridges):")
    print("  browse: https://navigation.usace.army.mil/Survey/Hydro")
    print("  open data: https://geospatial-usace.opendata.arcgis.com")
    print()
    print("Once you have a .RAW/.HSX file:  hydroformats info <file>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
