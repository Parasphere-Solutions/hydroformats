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
  State Plane magnitudes.
- Water-level records (type 60, u16 centimetres) align with the HSX
  `TID` series one-to-one on time. A flags word splits two sub-series:
  flag 0 tracks the HSX tide at centimetre level (median 1 cm agreement,
  occasional 3–7 cm excursions — a differently staged value, not a byte
  copy); flag 0x0300 carries a value ~5.1 m above the tide (uncorrected
  water-level candidate).
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

**S6: Cerulean Ping Protocol and Surveyor 240-16 public ICD** (Cerulean
Sonar documentation). The universal packet format page pins the SVLog
framing: sync bytes `'B'`,`'R'`, u16 payload length, u16 packet id, u8
source device, u8 destination device, payload, u16 checksum defined as
the 16-bit truncated sum of every preceding byte in the packet.
<https://docs.ceruleansonar.com/c/cerulean-ping-protocol/universal-packet-format.md>.
Per-packet field tables come from the Surveyor 240-16 API pages
(ATOF_POINT_DATA 3012, YZ_POINT_DATA 3011, ATTITUDE_REPORT 504,
WATER_STATS 118, SET_PING_PARAMETERS 3023)
<https://docs.ceruleansonar.com/c/surveyor-240-16/application-programming-interface.md>
and the general packet definitions (DEVICE_INFORMATION 4, NMEA_WRAPPER
109, MAVLINK_WRAPPER 150)
<https://docs.ceruleansonar.com/c/cerulean-ping-protocol/general-packet-definitions.md>.
Log container semantics (`.svlog` raw packet stream, `.svlz` gzipped,
sessions auto-split, files concatenable):
<https://docs.ceruleansonar.com/c/sonarview/log-files>. The framing page
leaves byte order unstated; little-endian is anchored by the documented
byte-identical framing with the Blue Robotics Ping Protocol and proven
on the vendor's published sample survey (the 737-reef `.svlz` on the
Surveyor 240-16 sample-data page), which parses end to end little-endian
with zero checksum failures and zero unframed bytes across 121,832
packets. That sample predates the current API: its point payloads use
ids 3009 and 3010 (END_PING_INFO in the vendor's packet index), which
have no published field tables and therefore stay undecoded. The
machine-readable `surveyor240.json` in the ping-protocol repository
carries no license and is deliberately neither vendored nor consulted;
every layout here is hand-built from the cited pages. Consulted
2026-08-28.

**S7: Generic Sensor Format specification and NCEI sample.** The GSF
reader is anchored to the format's own open specification:

- *Generic Sensor Format Specification*, version 03.09, Leidos doc
  98-16v, 26 April 2019 (prepared for the Naval Oceanographic Office).
  Public copy: <https://www3.mbari.org/data/mbsystem/formatdoc/GSF/gsf_spec_03.09.pdf>
  (Leidos also distributes the specification from its Ocean & Marine
  pages). Consulted 2026-08-28.

Clean-room note: the reference C library (gsflib) is LGPL and was
**deliberately not consulted**; every layout in `hydroformats/gsf.py` is
hand-built from the specification document, and every reading the
document leaves open is labelled as a judgment in the module docstring
(checksum framing, beam-array field width derived from subrecord size,
attitude offset units, summary depth units).

Validation against real archived data: NOAA NCEI's multibeam archive
publishes GSF files (MB-System format 121). The file used here is
`ahmba03214.d05.mb121.gz` from survey AHI-03-06 (NOAA survey launch AHI,
Reson SeaBat 8101, Midway Atoll, August 2003; 13,233,784 bytes
decompressed, GSF-v02.02):
<https://data.ngdc.noaa.gov/platforms/ocean/ships/ahi/AHI-03-06/multibeam/data/version1/MB/ahmba03214.d05.mb121.gz>.
The library frames **every byte of the file with zero skipped bytes and
zero malformed records** (580 records: 516 pings of 101 beams, 51
attitude, 7 history, one each of header/SVP/processing-parameter/
sensor-parameter/comment/summary), the ping positions sit inside the
summary record's bounding box at Midway, the maximum usable beam depth
equals the summary maximum exactly (pinning the centimeter reading of
the summary extremes), scale factors with a nonzero offset decode
depths onto the 65-73 m bank the summary declares, and the beam angle,
travel time and sound-speed observables are physically consistent
(0.5 x 1536 m/s x travel time x cos(angle) reproduces the reported
depths to within refraction). The file is fetched from NCEI directly
and is **not** distributed with this repository; the statistics are
pinned in `tests/test_gsf.py::test_real_sample_statistics` (run with
`GSF_SAMPLE=<path to decompressed file>`). Consulted 2026-08-28.

**S8: Sound Metrics ARIS File SDK and public DIDSON description.** The
DDF reader (`hydroformats/aris.py`) is anchored to the sonar
manufacturer's own MIT-licensed SDK, whose reference type definitions
may be read and translated with attribution (carried in the module
docstring):

- ARIS File SDK, Sound Metrics Corp., MIT license,
  <https://github.com/SoundMetrics/aris-file-sdk> (commit `5329f18`,
  2026-05-11). Files used: `type-definitions/C/FileHeader.h` and
  `type-definitions/C/FrameHeader.h` (every field name, type and byte
  offset for the DDF v5 file and frame headers, including the offset
  enums the parser's field tables mirror),
  `common-code/FrameFuncs.c` (`get_beams_from_pingmode`, translated as
  `beam_count_for_ping_mode`: modes 1-2 give 48 beams, 3-5 give 96,
  6-8 give 64, 9-12 give 128, anything else 0), and
  `docs/understanding-aris-data.md` (container layout: one file header
  then uniform frames of header plus beams x samples bytes; sample
  ordering with beam 0 rightmost; the window start/length/sample
  spacing formulas; the guidance to trust frame headers over the
  writer-populated file header). The repository's `sample-code/sample.aris`
  is the v5 validation file below. Consulted 2026-08-29.
- Echoview's public DIDSON data file description,
  <https://support.echoview.com/WebHelp/Reference/File_Formats/DIDSON_data_files.htm>:
  the DDF v3 header sizes (512-byte master header, 256-byte frame
  headers, against 1024/1024 from v4 on) and the v3 range decode (start
  range = window start code x delay period x sound speed / 2; the
  four-cell delay period table by HighResolution and serial number:
  0.001024 / 0.001144 / 0.000512 / 0.000572 seconds). Consulted
  2026-08-29.

The v3 field layout inside those header sizes is not published
byte-by-byte anywhere public. The reading used here: the SDK states the
ARIS headers preserved the legacy DIDSON parameters unchanged for
backward compatibility, so the v3 headers are read with the v5 layout
truncated to the v3 sizes, with three deviations proven on the real
clips below: the window start/length words are u32 codes (the SDK's own
field comments say "code [0..31]" / "code [0..3]" for DIDSON) where v5
stores f32 meters; the eight words from offset 20 are a full calendar
clock (year, month, day, hour, minute, second, hundredths; v5 overlays
year and month with its u64 PC timestamp, which is why the v5 `TS_`
fields begin at day); and the 64-bit sonar time counts whole seconds.
Under this reading the clips' calendar clocks match their filenames,
compass heading/pitch/roll decode to plausible attitudes, receiver gain
and window codes equal the file header's, and the frame cycle word
matches the declared frame rate (143 ms at 7 fps).

Validation against real data (statistics pinned in
`tests/test_aris.py`, run with `ARIS_SAMPLE=<path to sample.aris>` and
`DDF_SAMPLE_DIR=<path to the .ddf directory>`; neither dataset is
distributed with this repository):

- **DDF v5**: the SDK's own `sample.aris` (583,168 bytes). Six frames
  of 48 beams x 2000 samples walk with zero skipped bytes, every frame
  signature intact; ping mode 1 on an ARIS 1200 (system type 2, serial
  1098, telephoto lens); microsecond frame times strictly increasing at
  152.6 ms spacing matching the recorded 6.548 Hz frame rate; strong
  image energy in every frame.
- **DDF v3**: ten raw DIDSON sturgeon-monitoring clips recorded
  2007-10-31 to 2007-11-02 (CC0, USACE/ERDC "DIDSON data collected at
  dams" release). All ten: 96 beams x 512 samples, high frequency,
  serial 189, receiver gain 40, sound speed word 1457 m/s. The declared
  frame counts equal the counts implied by file size exactly (1,569
  frames total, zero leftover bytes), every frame signature is the v3
  magic, frame indices count from zero, and the calendar clocks are
  non-decreasing within every file.

**S9: EdgeTech JSF interface control document.** The JSF reader
(`hydroformats/jsf.py`, records in `hydroformats/jsf_records.py`) is
anchored to the format owner's own public description:

- *JSF File and Message Descriptions*, EdgeTech document 0023492
  Rev. R, December 22, 2025.
  <https://www.edgetech.com/wp-content/uploads/2023/04/0023492_Rev_R.pdf>
  (the document's License Statement permits redistribution without
  modification). Consulted 2026-08-30.

Clean-room note: no third-party JSF parser code was consulted; every
layout is hand-built from the document's tables (message header
Table 2-1; sonar data Tables 2-2 through 2-10 with Equation 2-2-1 for
block floating point expansion; system information Table 2-17; NMEA,
pitch roll and pressure sensor Tables 2-19 through 2-21; bathymetric
data Tables 2-28 and 2-29 with Equations 2-2 through 2-8; attitude,
pressure, altitude and position Tables 2-30 through 2-33). Byte order
is little endian throughout per the document's section 1.2, pinned
byte-for-byte in `tests/test_jsf.py`. Readings the document leaves open
are labelled as judgments in the module docstrings:

- The type 3000 angle scale factor is read as a 4-byte float where the
  table prints UINT32: its unit is degrees, Equation 2-5 multiplies it
  directly onto a signed 16-bit count (which no whole number of degrees
  could scale to sub-degree angles), and every neighboring scale and
  accuracy word in the same table is a float.
- The type 3002 validity bits are read in field order (bit 0 pressure
  through bit 5 depth); the document defers to the 3001 description
  without listing bits, and field order is the rule both of its fully
  enumerated validity tables follow.
- The type 80 sample interval fraction byte (LSB1 bits 0-7) is carried
  verbatim, never interpreted: the document names the field without
  stating its encoding, unlike the course, speed and sweep fractions,
  whose decimal-digit encodings it does state.

Validation against real archived data: the MGDS/IEDA marine geoscience
archive publishes raw JSF. The file used here is
`galv2017_line07.000.jsf` from the Trinity River Paleovalley project
(EdgeTech SB-512i chirp sub-bottom with a 3200-XS topside, offshore
Galveston TX, 2017-05-23; 100,005,102 bytes; Goff & Gulick 2020,
doi:10.1594/IEDA/326817, dataset
<https://www.marine-geo.org/tools/search/Files.php?data_set_uid=26817>,
fetched via the archive's terms-accept POST endpoint,
`api.marine-geo.org/services/download/download_accept.php` with
`data_uids=1348405`). The archive licenses it CC BY-NC-SA 3.0 US, so it
is a local test fixture only and is **not** distributed with this
repository. The library frames **every byte with zero skipped bytes
and zero malformed records** (13,136 messages: 8,421 type 80 sonar
messages, 3,368 NMEA strings, 1,347 type 2040 messages that Rev R does
not document, skipped and counted). Consecutive ping numbers at 5 Hz
across the 28-minute line; the sample interval and sample frequency
words reproduce each other; the 0.7-12 kHz sweep matches the sonar;
the CPU calendar block equals the seconds-since-1970 word on all 8,421
pings (pinning both time decodes; the recorder's clock was unset at
2003, and the reader surfaces both it and the NMEA fix riders'
true 2017 date faithfully); the position riders decode from
ten-thousandths of minutes of arc into the Galveston box the
interleaved GPRMC sentences independently pin, with fix times agreeing
to the second; and the per-ping block floating point exponents span
3-7 with raw mantissas normalized into the 16-bit range. Statistics
pinned in `tests/test_jsf.py::test_real_sample_statistics` (run with
`JSF_SAMPLE=<path to the .jsf>`). No publicly downloadable EdgeTech
6205 file (dual-frequency side scan plus type 3000 bathymetry)
surfaced anywhere in NCEI, USGS ScienceBase/CMGDS, R2R or MGDS, so the
type 3000 decode and the port/starboard and dual-frequency channel
mapping rest on the ICD tables and synthetic fixtures until the first
partner 6205 files arrive; those would pin the remaining statistics
immediately. Consulted 2026-08-30.
**S10: Triton XTF specification.** The XTF reader
(`hydroformats/xtf.py`) is anchored to the format owner's own
specification document:

- Triton Imaging, Inc., *eXtended Triton Format (XTF) Rev. 41*,
  September 2016 (revision history X1 2002-01-15 through X41
  2016-09-16). Distributed by Triton from
  `tritonimaginginc.com/site/content/public/downloads/FileFormatInfo/`;
  that download area is offline today (the domain is now an ECA Group
  landing page), so the copy used is the Internet Archive capture:
  <https://web.archive.org/web/20170418082139/http://www.tritonimaginginc.com/site/content/public/downloads/FileFormatInfo/Xtf%20File%20Format_X41.pdf>.
  Rev. 41 is the newest revision the Archive holds; the MBARI
  MB-System format-document mirror carries the older Rev. 26
  (<https://www3.mbari.org/data/mbsystem/formatdoc/XtfFileFormat_X26.pdf>)
  for cross-checking. Consulted 2026-08-30.

Clean-room note: several open-source XTF parsers exist and were
**deliberately not consulted**; every layout in `hydroformats/xtf.py`
is hand-built from the specification document, and every reading the
document leaves open is documented in the module docstring (the
attitude table's misprinted prefix offsets, the ping header's
overlapping tail offsets, integer sample signedness via the UniPolar
word, the undecoded IBM float sample format).

Validation against real data: `Demoplane.xtf`, the demo sidescan
survey Triton itself distributed from the same public downloads area
(`.../downloads/DemoFiles/Demoplane.xtf`), retrieved from the Internet
Archive capture of 2005-11-09:
<https://web.archive.org/web/20051109135308/http://www.tritonimaginginc.com/site/content/public/downloads/DemoFiles/Demoplane.xtf>
(25,587,648 bytes; an Isis Server recording of a sunken aircraft
survey, Puget Sound, 2000-07-07, converted with DAT2XTF 153; vendor
demo data published without a license text, so it is fetched from the
Archive for local validation and **not** redistributed with this
repository). The reader frames **every byte of the file exactly**:
1,024 header bytes plus 2,009 sonar packets of 256 + 3 x (64 + 4,096)
bytes, with zero skipped bytes, zero malformed packets and zero
unknown packet types. Ping numbers count 1..2009 without a gap, all
three declared 16-bit channels (port, starboard, subbottom; 2,048
samples at 90 m slant range) appear on every ping, and the sensor
track decodes to a small box at 47.676 N 122.240 W consistent with the
survey story. The statistics are pinned in
`tests/test_xtf.py::test_real_sample_statistics` (run with
`XTF_SAMPLE=<path to Demoplane.xtf>`). Consulted 2026-08-30.

**S12: Teledyne RESON 7k Data Format Definition.** The s7k reader
(`hydroformats/s7k.py`, records in `hydroformats/s7k_records.py`) is
anchored to the format owner's own protocol description:

- *7k Data Format*, Teledyne RESON Data Format Definition,
  Version 3.10, April 3, 2019.
  Public copy, provided by Teledyne to the MB-System documentation
  archive:
  <https://www3.mbari.org/data/mbsystem/formatdoc/Teledyne7k/7k_DFD_3.10_package/DFD_7k_Version_3.10.pdf>
  (Teledyne Marine distributes the DFD from teledynemarine.com; the
  MBARI format-documentation page hosts vendor-permitted copies,
  Version 3.10 the newest). Consulted 2026-08-31.

Clean-room note: no third-party s7k parser code was consulted; every
layout is hand-built from the document's tables (Data Record Frame
Table 5 with the 7KTIME structure of Table 3 and the sign and unit
conventions of Table 2; position Table 14; CTD Tables 24-25; geodesy
Table 26; roll/pitch/heave and heading Tables 27-28; sonar settings
Table 39; beam geometry Tables 44-45; bathymetric data Tables 46-47;
beamformed data header Table 63; raw detection data Tables 71-73 with
Appendix F's sample-number-to-travel-time rule; snippet data
Tables 74-75; compressed water column header Table 82; snippet
backscattering strength Tables 100-101; remote control sonar settings
Table 113; sound velocity Table 117). Byte order is little endian
throughout per the document's section 2.4, pinned byte-for-byte in
`tests/test_s7k.py`. Readings the document leaves open are labelled as
judgments in the module docstring:

- The trailing checksum word is read as always present (the size field
  is defined "to the end of the checksum field" unconditionally) and
  verified only when DRF flags bit 0 is set; see the errata below on
  the document's own bit numbering.
- Frames whose header offset field is below 60 degrade to gaps: the
  version 5 DRF (the only protocol version in use per Table 4) needs
  its 64-byte fixed part.
- Records are decoded through the record size per the DFD's
  backwards-compatibility rule (new fields append at the end): longer
  payloads ride along undecoded, shorter vintages surface the older
  layout with the absent trailing fields as None. The rule is proven
  on real data twice over: the 2009 sample below writes 22-byte 7027
  detection blocks (no intensity yet) and 36-byte 1003 records
  (without the one field Table 14 itself marks optional), the 2017
  sample 26-byte blocks (intensity, no gate limits).
- The zero-length snippet window convention (begin sample greater than
  end sample, length = end - begin + 1), stated by the DFD for record
  7058, is applied to record 7028's identical descriptors as well.

Validation against real archived data: NOAA NCEI's multibeam archive
publishes raw .s7k (MB-System format 88). The file pinned in
`tests/test_s7k.py::test_real_sample_statistics` (run with
`S7K_SAMPLE=<path to the decompressed .s7k>`) is `20170522_181322.s7k`
from survey SP1701 (R/V Scott Petty, Reson SeaBat T50-P, Galveston Bay
approaches, 2017-05-22; 425,864,523 bytes decompressed):
<https://data.ngdc.noaa.gov/platforms/ocean/ships/scott_petty/SP1701/multibeam/data/version1/MB/t50-p/20170522_181322.s7k.mb88.gz>.
The library frames **every byte with zero skipped bytes and zero
malformed records** (120,287 records; a natively logged line: 7,957
pings each carrying 7000 settings, 7004 beam geometry, 7027 raw
detections, 7028 snippets and a 7503 settings snapshot, plus
1003/1012/1013 navigation and motion series and 15,917 7610 surface
sound velocity records). The 512-beam geometry spans -75 to +75
degrees; 3,986,864 detections; every ping carries snippet windows
(37.4 million 16-bit samples file-wide); the position series decodes
into the Galveston Bay approaches; and reducing each ping's most
vertical raw detection (two-way time x applied sound velocity, cosine
of the receive angle) puts the bed 8.6-15.7 m down, a dredged
shipping channel. Two 2009 SeaBat 7125 lines from NCEI survey
BermudaCaves2009 (Endurance, PDS-logged) cross-check the older
vintages: both frame end to end with zero gaps, zero checksum
failures and zero malformed records, positions decode to Bermuda, and
a 532-point 1010 profile carries a plausible sound speed cast.
Neither dataset is distributed with this repository. Consulted
2026-08-31.

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
- **S7's attitude record table draws five parallel arrays** (all time
  offsets, then all pitch, roll, heave, heading values). Real GSF data
  proves the measurements are **interleaved**, one (time, pitch, roll,
  heave, heading) group per measurement: read interleaved, the S7
  sample's offsets climb monotonically in 20 ms steps (a 50 Hz motion
  sensor), the spans equal the base-time gaps between consecutive
  attitude records exactly (which also pins the offsets as
  milliseconds, a unit the table never states), and pitch/roll/heave/
  heading track the ping-header values; read as parallel arrays the
  same bytes decode to physically impossible motion. The library
  decodes interleaved.
- **S8's stored ARIS window floats carry a writer default sound speed.**
  The SDK teaches deriving window start and length from
  SampleStartDelay, SamplePeriod, SamplesPerBeam and the frame's
  SoundSpeed, and the frame header also stores WindowStart/WindowLength
  floats. In `sample.aris` the two disagree: the stored floats (3.3299 m
  and 20.30 m) back-solve to a nominal 1450 m/s, while the frame's own
  calculated SoundSpeed is 1435.93 m/s (formula values 3.2976 m and
  20.103 m). The library surfaces the stored floats verbatim and derives
  the self-consistent values as properties; consumers who care about
  range accuracy should use the derived ones.
- **Echoview's v3 WindowStart range is understated.** Its DDF_03 page
  says WindowStart "is 0, 1, 2, or 3"; the S8 clips carry start codes
  1, 4, 6 and 12, and the SDK's own file header comment says the DIDSON
  start code spans [0..31] (0..3 matches the length code instead). The
  decode formula itself checks out: at the header's 1457 m/s the first
  clip's code 4 gives a 1.667 m start and a 10.003 m window, consistent
  with a short-range HF fish-counting deployment.
- **S12's checksum flag prose contradicts its own bit table.** The
  DRF Flags field of Table 5 enumerates "Bit 0: Checksum, 0 invalid /
  1 valid", but the Checksum field's description in the same table
  says its use "depends on bit 1 of the Flags field". The library
  reads bit 0, the reading the enumeration supports; on all three
  real S12 samples the flags word is exactly 0x0001 and the byte sums
  verify under it (7610 aside, next erratum).
- **The S12 sample's 7610 records carry stale checksums.** In the
  SP1701 T50-P line, 15,857 of the 15,917 record-7610 frames fail the
  checksum: the stored word exceeds the byte sum by a small amount
  that drifts smoothly along the 10 Hz series, as if the writer
  checksummed the record before restamping a field. Every other
  record type in the file (104,370 frames) verifies exactly, as does
  every frame of the two 2009 PDS-logged samples, so the framing and
  sum rule are right and this is a writer quirk of that vintage. The
  reader reports it (`checksum_ok`, `counters.checksum_failures`) and
  decodes the records anyway; the 7610 values themselves are a
  coherent 1522-1526 m/s surface sound velocity series.
- **S9's NMEA source byte enumeration is incomplete.** The ICD lists
  the type 2002 source values as 1 = Sonar, 2 = Discover, 3 = ETSI;
  every one of the 3,368 NMEA messages in the S9 sample (written by
  Discover 15.1) carries source 0. The value is surfaced raw, never
  mapped to a name.
- **The v3 whole-second sonar time lags its own calendar clock.** In
  the S8 clips the 64-bit sonar time word can still hold the previous
  second after the calendar fields (which carry the hundredths) have
  rolled over, so combining the two produces a non-monotonic clock.
  The calendar fields are internally consistent and non-decreasing
  file-wide; the library treats them as the frame clock.
- **S10's XTFATTITUDEDATA table misprints its own prefix.** It lists
  HeaderType at byte offset 1 and SubChannelNumber at 2, but the magic
  word at offset 0 is a WORD spanning bytes 0-1 and every other packet
  table in the same document puts them at 2 and 3. The library reads
  the uniform 14-byte prefix everywhere; the real S10 sample's packets
  frame end to end under that reading.
- **S10's XTFPINGHEADER tail overlaps itself.** The table lists
  ReservedSpace2[6] at offset 245 while also defining OptionalOffset
  (a 4-byte word at 245) and CableOutHundredths (249). Reserved space
  is read as bytes 250-255, the only reading that fills the stated
  256-byte structure exactly.
- **S10's UniPolar flag does not match its own demo data.** The spec
  says UniPolar 0 means polar (signed) samples; Demoplane.xtf declares
  0 on all three channels, yet the 16-bit samples are almost entirely
  non-negative amplitudes with a scattering of 0x8001-magnitude words
  (clip sentinels under the signed reading). The library decodes per
  the declared flag and takes a ``signed`` override, and the raw
  sample bytes always ride along so consumers can re-read.
- **S10's nav duplication is real.** The ping header carries ship and
  sensor positions side by side; in the S10 sample only the sensor
  pair is populated (ship stays 0.0) even though layback is zero.
  Which position (and whether to swing layback) georeferences the
  imagery is left to the consumer, per the module docstring.

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
| `'B'``'R'` framing, truncated-sum checksum, svlz gzip | SVLog | S6 | zero failures on the vendor sample |
| ATOF_POINT_DATA 3012, YZ_POINT_DATA 3011 | SVLog | S6 | reserved words carried verbatim |
| ATTITUDE_REPORT 504, WATER_STATS 118, SET_PING_PARAMETERS 3023 | SVLog | S6 | pitch/roll formulas per vendor page |
| DEVICE_INFORMATION 4, NMEA_WRAPPER 109, MAVLINK_WRAPPER 150 | SVLog | S6 | wrappers kept as text |
| ids 10, 12, 3009, 3010 | SVLog | S6 (index only) | **no public layout: skipped, counted** |
| Record framing (u32 size, u32 identifier, optional checksum) | GSF | S7 | big endian per spec 3.6.2/4.3.1 |
| HEADER, ping header (42- and 56-byte forms) | GSF | S7 | Tables 4-2, 4-3 |
| Scale factors (id 100: divide, subtract offset) | GSF | S7 | nonzero offset proven on the S7 sample |
| Depth/nominal/across/along/travel-time/angle arrays (1-5, 14) | GSF | S7 | width from subrecord size |
| Beam flags 16, quality factor 9, error arrays 11-13, 19-20, 27-28 | GSF | S7 | flag bits per Appendix C |
| SVP 3, COMMENT 6, HISTORY 7, PROCESSING_PARAMETERS 4, SUMMARY 9 | GSF | S7 | Tables 4-6 to 4-11 |
| ATTITUDE 12 (interleaved measurements, ms offsets) | GSF | S7 | see anchor errata |
| SENSOR_PARAMETERS 5, ids 8/10/11, sensor subrecords 102+, intensity 21 | GSF | S7 (existence) | **skipped, counted by id** |
| DDF magic words (v5 0x05464444, v3 0x03464444), container lattice | DDF | S8 | signature doubles as version |
| v5 file header (1024 B) and frame header (1024 B), all named fields | DDF | S8 | SDK offset enums, mirrored |
| Ping mode to beam count table (48/96/64/128) | DDF | S8 | FrameFuncs.c, translated |
| Window start/length/sample spacing formulas (v5) | DDF | S8 | see anchor errata on stored floats |
| v3 header sizes (512 B / 256 B), delay period table, range decode | DDF | S8 (Echoview) | see anchor errata on the code range |
| v3 field layout (shared legacy layout, 3 deviations) | DDF | S8 | proven on the CC0 clips |
| Sample ordering (range row major, beam 0 rightmost) | DDF | S8 | uniform frames per the SDK |
| 16-byte message header (0x1601 marker, subsystem/channel) | JSF | S9 | Table 2-1, little endian |
| Sonar data 80: 240-byte header, block floating point trace | JSF | S9 | Tables 2-2 to 2-10, Equation 2-2-1 |
| MSB/LSB extensions (samples, frequencies, mark, course, speed, sweep) | JSF | S9 | Table 2-2; interval fraction verbatim |
| System information 182 | JSF | S9 | Table 2-17, growable tail tolerated |
| NMEA 2002, pitch roll 2020, pressure sensor 2060 | JSF | S9 | Tables 2-19 to 2-21 |
| Bathymetric data 3000: header + revision 4/5 sample sets | JSF | S9 | Tables 2-28, 2-29; Equations 2-2 to 2-8 |
| Attitude 3001, pressure 3002, altitude 3003, position 3004 | JSF | S9 | Tables 2-30 to 2-33 |
| JSF types 40, 181, 1260, 2071, 2080, 2091, 2100, 2101, 2111, 3005, 3041, 4000, 4034 | JSF | S9 (existence) | **skipped, counted by type** |
| XTFFILEHEADER (1024 B + growth), CHANINFO (128 B) | XTF | S10 | Tables C, D |
| Packet prefix (magic 0xFACE, type, size), resync on magic | XTF | S10 | Table H; section 2.2 |
| XTFPINGHEADER 256 B (nav, attitude, tow, CTD fields) | XTF | S10 | Table H; tail offsets per errata |
| XTFPINGCHANHEADER 64 B + samples (8/16/32-bit, IEEE float) | XTF | S10 | Tables D, I; signedness per errata |
| XTFATTITUDEDATA 3, XTFNOTESHEADER 1, XTFRAWSERIALHEADER 6 | XTF | S10 | Tables E, F, G; prefix per errata |
| XTFBATHHEADER 2 (vendor payload verbatim) | XTF | S10 | Figure 3: "logged raw" |
| BATHY_SNIPPET 19: SNP0/SNP1 headers, raw fragments | XTF | S10 | Tables N, O |
| Header types 4-18, 20-108, 199-200 | XTF | S10 (list only) | **skipped, counted by type** |
| Data Record Frame (sync 0x0000FFFF, 7KTIME, byte-sum checksum) | s7k | S12 | Tables 3 and 5; resync on the sync pattern |
| Position 1003 (geographic or grid, optional satellite count) | s7k | S12 | Table 14; short form proven on 2009 data |
| CTD 1010, geodesy 1011, roll/pitch/heave 1012, heading 1013 | s7k | S12 | Tables 24-28 |
| Sonar settings 7000, remote settings 7503 (append-tolerant tail) | s7k | S12 | Tables 39, 113 |
| Beam geometry 7004 (size-detected transmit delays) | s7k | S12 | Tables 44-45 |
| Bathymetric data 7006 (deprecated; optional gates) | s7k | S12 | Tables 46-47 |
| Raw detections 7027 (block-size-gated fields, Appendix F reduction) | s7k | S12 | Tables 71-73; 22- and 26-byte vintages proven |
| Snippets 7028, snippet backscatter 7058 (empty-window rule) | s7k | S12 | Tables 74-75, 100-101 |
| Water column 7018/7042 | s7k | S12 | Tables 63, 82: **headers decoded, sample payloads skipped** |
| Sound velocity 7610 (size-detected temperature and pressure) | s7k | S12 | Table 117 |
| s7k 1005-1017 sensor family (incl. PDS 1015/1016), 7001-7022, 7030-7059, 7200, 7300, 7500-7511, 7504 | s7k | S12 (existence) | **skipped, counted by type** |

## Deliberately not implemented

- **SVLog ids 3009, 3010, 10, 12** (the packet family in the vendor's
  published sample): the vendor index names 3010 END_PING_INFO but
  publishes no field table for any of them, so they are skipped
  tolerantly and counted, never guessed. Same for the unlicensed
  `surveyor240.json` (reference only, not vendored).
- **EC2** field layout (attested by S3's list only).
- **HS2** (the 32-bit predecessor of HS2X): structure unverified against
  any capture we hold; per CARIS release notes it lacks even a date field.
- **HS2X unassigned words** (uncertainty-candidate scalars, intermediate
  solver offsets, config payloads): carried verbatim, never interpreted,
  until a vendor description or a decisive validation pins them.
- LOG catalogs, TGT targets, LNW planned-line files: out of scope for
  0.2; sources exist in the public HYPACK manuals if contributed with
  citations.
- **GSF sensor-specific subrecords** (Appendix B: fifty-plus per-sonar
  layouts) and the **intensity time series** (id 21, whose imagery block
  is itself sensor-specific): skipped tolerantly and counted by id. The
  spec defines them, but they are per-sonar surface area far beyond the
  core swath observables; contributions welcome with spec table
  citations.
- **GSF single-beam sounding record** (id 10, discouraged since v2.03),
  the obsolete **NAVIGATION_ERROR** (id 8), **HV_NAVIGATION_ERROR**
  (id 11) and **SENSOR_PARAMETERS** (id 5): skipped tolerantly and
  counted, not decoded.
- **GSF writing**: this library reads GSF only.
- **DDF v4** (magic 0x04464444, the DIDSON format between the two
  supported versions): 1024-byte headers per Echoview, but no v4 capture
  is in hand to validate a layout against, so the magic is not claimed
  and such files degrade to a MalformedRecord naming the version word.
  Contributions welcome with a real v4 file.
- **ARIS beam spacing tables** (the SDK's `beam-width-metrics` headers):
  cross-range pixel geometry needs the per-lens beam angle tables; they
  are deliberately not vendored here, and the reader stops at downrange
  geometry (window bounds, sample spacing) plus raw frames. A consumer
  building metric imagery should load the SDK's tables directly.
- **Live-stream sample reordering** (`ReorderedSamples` zero): recorded
  files carry ordered samples per the SDK; the raw-multiplexed order of
  live ARIS streams is out of scope for a file reader. The flag is
  surfaced so callers can refuse unordered frames.
- **DDF v3 low-frequency and long-range variants**: the delay period
  table implements all four Echoview cells, but every S8 clip is a
  high-frequency serial-189 file, so the other three cells are anchored
  by the table alone, not by data.
- **DDF writing**: this library reads DDF only.
- **JSF message types without a decoding need here** (sonar status 40,
  navigation offsets 181, target file data 1260 with its embedded
  JPEGs, reflection coefficient 2071, DVL 2080, situation 2091, cable
  counter 2100, kilometer of pipe 2101, container timestamp 2111, GPS
  status 3005, bathymetric parameters 3041, and the eBOSS beamformed
  4000/4034 family): defined by S9 but outside the side scan, bathymetry
  and navigation observables this library targets; skipped tolerantly
  and counted by type, contributions welcome with table citations.
- **JSF type 3000 format revisions 0 through 3**: the ICD details only
  the revision 4+ sample layout and calls the interferometric output
  rarely used; older revisions decode the header and leave the sample
  arrays None.
- **JSF type 80 proprietary data formats** (format word above 255): the
  header decodes, the trace stays None; the ICD says only that such
  data is in an EdgeTech proprietary format.
- **JSF writing**: this library reads JSF only.
- **XTF packet types beyond the core six** (forward-look 4, Elac 5,
  the Klein high-speed sensor and data pages, GPS/gyro/navigation
  types 20-24 and 42/84/107, the QPS and Reson/R2Sonic vendor records,
  custom type 199): the S10 tables define several of them, but none
  appears in the validation sample, so they are skipped tolerantly and
  counted by header type rather than shipped undecoded-but-guessed.
  Contributions welcome with spec table citations and a real file.
- **XTF sample format 1 (4-byte IBM float)**: named by S10 without a
  bit layout, so those channels keep raw bytes only.
- **XTF vendor bathymetry payloads** (types 2 and 19 fragments): the
  spec's own instruction is to consult the sonar manufacturer; the
  payloads are carried verbatim.
- **XTF writing**: this library reads XTF only.
- **s7k water column payloads** (7018 beamformed and 7042 compressed
  sample matrices): the headers decode, the matrices are skipped by
  design; water column data dwarfs everything else in a file and this
  library targets the detection, snippet and navigation observables.
- **s7k record types without a decoding need here**: the 7001
  configuration XML, 7002 match filter, 7003 hardware pages, 7007
  side scan, the deprecated 7008/7041 water column forms, 7010 TVG,
  7012 ping motion, the 7021/7022 BITE family, 7030 installation
  parameters, the 7200 file header and 7300 catalog, the 7500-7511
  remote control records, and the Teledyne PDS 1015/1016
  navigation/attitude pair (whose content the 1003/1012/1013 series
  this library decodes also carries in natively logged files): all
  defined by S12, skipped tolerantly and counted by type,
  contributions welcome with table citations.
- **s7k fragmented data record sets**: the DRF's fragmentation words
  are defined "always zero" by S12 and are zero in every sample; a
  nonzero fragment would frame but is not reassembled.
- **s7k writing**: this library reads s7k only.
