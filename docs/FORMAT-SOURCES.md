# Format sources

Every record layout in this library is anchored to a public source listed
here. The project rule: **no anchor, no parser** — unanchored record types
surface as `UnknownRecord`.

## Sources

**S1 — MB-System** (Caress & Chayes et al., open source, GPL):
`src/mbio/mbsys_hysweep.h` and `src/mbio/mbr_hysweep1.c` at
<https://github.com/dwcaress/MB-System>. The mb201 HSX reader/writer; the
header embeds text from the 2010 HSX format specification, and the writer's
output statements pin exact field order for every HSX record this library
parses. Consulted 2026-08-05 (master branch).

**S2 — USGS field-activity metadata** (public domain): the navigation
metadata for USGS cruise 2014-009-FA documents RAW record semantics
field-by-field (POS, RAW, QUA incl. GST extras, MSG, TID, KTC, EC1, GYR,
HCP incl. sign conventions, FIX, OFF, FTP/VER/INF header notes).
<https://cmgds.marine.usgs.gov/data/field-activity-data/2014-009-FA/data/navigation/2014-009-FA_hypack_meta.html>.
Additional USGS activities (Red Brook Harbor OFR 2010-1091, Cape Cod Bay
OFR 2010-1006) corroborate. Consulted 2026-08-05.

**S3 — Hydromagic documentation** (Eye4Software): the HYPACK import page
includes a complete example RAW file (header record inventory: FTP, VER,
INF, ELL, PRO, DTM, GEO, HVU, TND, DEV with driver extras, OFF, LIN, PTS,
LBP, LNN, LTP, EOL, EOH; data: TID, EC1, POS, FIX 5-field form) and the
export page lists the RAW record set (EC1, EC2, POS, GYR, RAW, QUA, TID,
MSG, HCP).
<https://www.eye4software.com/hydromagic/documentation/manual/user-interface-features/import-raw-data/import-hypack/>.
Consulted 2026-08-05.

**S4 — Real logged data** (public domain): the actual survey files behind
S2 — `2014-009-FA_hypack.zip` (≈42 MB, HYPACK 14.0.9.47, Julian-day
directories of `.292` RAW logs)
<https://cmgds.marine.usgs.gov/data/field-activity-data/2014-009-FA/data/navigation/2014-009-FA_hypack.zip>.
The library parses these files with **zero malformed and zero unknown data
records** (7,915 records across two sampled files); short verbatim excerpts
form the regression suite in `tests/test_real_data.py`. Consulted
2026-08-05.

## Anchor errata

- **S2's RAW conversion prose is wrong.** It reads "lat=raw latitude in the
  format ddmmmm.mmmm. To convert to ddmm.mmmmm multiply by 100." The real
  S4 data proves the conversion is **division** by 100: logged
  `410966.80360 / -714331.75760` decodes by division to 41.1611°N
  71.7220°W, consistent with the UTM-18N `POS` easting/northing logged in
  the same second; multiplication produces impossible coordinates. The
  library divides.
- **S1/S2 describe `POS` as four fields**; real S4 files log a fifth
  numeric value. Semantics unanchored → preserved verbatim in
  `Position.extras`.
- **S2 shows QUA's integer fields as integers**; real S4 files log them
  float-formatted (`12.000`). The parser accepts both.

## Record-by-record anchoring

| Record | Dialect | Anchor | Note |
|--------|---------|--------|------|
| POS, EC1, GYR, HCP, TID, FIX, MSG | RAW | S2 (+S3 examples) | HCP sign conventions from S2 |
| RAW, QUA, KTC, OFF | RAW | S2 | QUA GST extras; KTC RTK fields |
| ELL, PRO, DTM, GEO, HVU, LTP | RAW | S3 | attested; carried verbatim (`HeaderMisc`) |
| TND trailing field | RAW | S3 | value observed; semantics unanchored → `extras` |
| DEV driver extras | RAW | S3 | preserved verbatim in `extras` |
| EC2 | RAW | S3 (existence only) | **layout unanchored → UnknownRecord** |
| FTP, VER, INF, TND, DEV, LIN, LBP, LNN, PTS, EOL, EOH | both | S1 + S2 + S3 | |
| HSX, HSP, DV2, OF2, PRI, MBI, SSI, PRJ, COM | HSX | S1 | hex fields per S1 scanf/printf |
| POS, GYR, HCP, EC1, TID, FIX, DFT, GPS, PSA, SNR | HSX | S1 | |
| RMB (+15 array types, bitmask order, XY = 2 lines) | HSX | S1 | writer output pins order |
| RSS (header + port/starboard sample lines) | HSX | S1 | |

## Deliberately not implemented

- **EC2** field layout (attested by S3's list only).
- HS2/HS2X (HYSWEEP *edited* binary/derived data), LOG catalogs, TGT
  targets, LNW planned-line files: out of scope for 0.1; sources exist in
  the public HYPACK manuals if contributed with citations.
