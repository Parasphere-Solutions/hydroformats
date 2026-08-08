# Format sources

Every record layout in this library is anchored to a source listed here.
The project rule: **no anchor, no parser** — unanchored record types
surface as `UnknownRecord` (text dialects) or `Hs2xOpaque` (binary), and
unanchored fields inside decoded records are carried verbatim in
`unassigned` tuples. Anchors are public documents wherever they exist; for
HS2X, where none does, the anchor is a reproducible empirical validation
(S5) and every unproven field is labelled as such.

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

**S5 — HS2X empirical cross-validation.** HYPACK publishes no byte-level
HS2X specification — their stated policy is that HS2 internals are
"complicated" and integrators should use the reader DLL shipped with
HYPACK ("HS2 Reader DLL", *Sounding Better!*, October 2009,
<https://www.xylem.com/siteassets/brand/hypack/resources/newsletter/2009/10-october/hs2readerdll.pdf>),
and no open-source reader exists in MB-System (HSX only, format 201) or
anywhere else we could find. The HS2X layouts in this library were
therefore derived by structural analysis of an HS2X file and anchored by
cross-validating every named field against the **paired HSX text log of
the same logging session** (HYPACK 22.1.5.0, format version string
`DATAGRAM VERSION 112`, a 400-beam interferometric multibeam, 616 pings):

- The file is a single doubly-linked TLV chain
  (`[prev size:u16][size:u16][type:u16][payload]`, bootstrapped by a
  4-byte `[size][type=26]` header). The walk covers the file end-to-end
  with **zero broken prev-links** across ~500,000 frames, and the record
  census matches the paired HSX census type for type (tide 408, gyro
  2,533 across three devices, attitude 2,125, position 408, pings 616,
  soundings 616×800).
- Ping (type 68) time and ping number equal the HSX `RMB` time tag and
  ping number **616/616 exactly**; device and sonar-type words equal the
  RMB device/sonar-type fields; the sound-velocity word is the RMB value
  ×100 (cm/s).
- Ping heading equals the navigation gyro's `GYR` series to ≤0.001°;
  roll and pitch equal the `HCP` series to ≤0.001°.
- Grid coordinates are **metric centimetres**: against the HSX `POS`
  series (a US-survey-foot State Plane grid), dividing the stored
  integers by 30.4800609601 leaves a constant sub-foot antenna/transducer
  lever arm with 0.01–0.02 ft scatter, while dividing by 30.48 leaves a
  ~25 ft error — the 2 ppm international-vs-survey-foot difference at
  State Plane magnitudes. Tide records (u16 centimetres) equal the HSX
  `TID` series under the same conversion, record for record.
- Position records (type 67) carry the grid pair (tracking `POS` with
  0.01 ft scatter), packed geographic latitude/longitude in the same
  `ddmmmm.mmmm` encoding as the RAW dialect (decoding to the survey
  site), an ellipsoidal height consistent with the region, and a UTC
  seconds-past-midnight double equal to the local time tag plus the
  session's UTC offset exactly.
- Sounding (type 69) elevations agree with the HSX single-beam `EC1`
  series at nadir (median +0.7 ft over 561 pings, the residual being
  footprint and motion differences) and with the independently surveyed
  bed elevations for the site. Unfilled swath slots ("no-detect")
  carry a fixed signature (zero linear scalar, ladder and quality words
  = 1) and park at the transducer position — verified on all 240,560
  such records in the capture.
- Sidescan sample counts in type-70 headers × 4 bytes equal the size of
  the type-72 payload that follows, for all 618 pairs.

Fields that this validation could not pin (config payloads, the
uncertainty-candidate scalars, intermediate solver words) are exposed
verbatim and labelled `unassigned` — see the class docstrings. The
validation capture is customer data and is **not** distributed with this
repository; the synthetic writer (`write_hs2x`) reproduces the structure
for tests. Derived 2026-08-08.

Context on what HS2X is, from HYPACK's public manuals (2023 HYPACK User
Manual,
<https://www.xylem.com/siteassets/brand/hypack/resources/manual/2023-hypack-user-manual.pdf>):
the 64-bit HYSWEEP EDITOR (MBMAX64) edit format. It can be written at any
stage from initial load ("AUTO SAVE HSX TO HS2X ON LOADING", MULTIBEAM
AUTO-PROCESSING) through full editing; deleted soundings "are not really
removed from the data file. Instead they are flagged as deleted", and the
format also stores TVU/THU. The capture behind S5 was written before any
editing, so this library's field table does not yet locate the deletion
flag; an edited/unedited pair of the same line would pin it (issue
welcome if you can contribute one).

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
| TLV framing, type 26 file header | HS2X | S5 | zero broken links file-wide |
| Type 68 ping header (time, ping, device, SV, grid, attitude) | HS2X | S5 | RMB/GYR/HCP/POS equality |
| Type 69 sounding (grid cm, elevation, beam angle) | HS2X | S5 | EC1/TID/POS agreement |
| Types 60/61/62/63/67 (tide, marks, gyro, attitude, position) | HS2X | S5 | record-for-record equality |
| Types 70/72 sidescan header + samples | HS2X | S5 | internal size consistency |
| Types 50–55 configuration block | HS2X | S5 (existence) | **payload undecoded → Hs2xOpaque** |

## Deliberately not implemented

- **EC2** field layout (attested by S3's list only).
- **HS2** (the 32-bit predecessor of HS2X): structure unverified against
  any capture we hold; per CARIS release notes it lacks even a date field.
- **HS2X unassigned words** (uncertainty-candidate scalars, intermediate
  solver offsets, config payloads): carried verbatim, never interpreted,
  until a vendor description or a decisive validation pins them.
- LOG catalogs, TGT targets, LNW planned-line files: out of scope for
  0.2; sources exist in the public HYPACK manuals if contributed with
  citations.
