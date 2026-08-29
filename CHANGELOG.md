# Changelog

## Unreleased

- Sound Metrics DDF read-only dialect, the library's first imaging
  sonar (acoustic camera) format: ARIS recordings (.aris, DDF v5) and
  original DIDSON recordings (.ddf, DDF v3), discriminated by the file's
  leading version magic. Typed file header and per-frame records
  (`read_aris`) carrying timestamps on both formats' clocks, the sonar
  settings that shape each image (ping mode and its beam count per the
  SDK table, samples per beam, sample period and start delay, gain,
  frequency, sound speed), compass/platform attitude and GPS fields
  where the headers define them, the raw frame image bytes with row and
  beam-profile access, and per-frame downrange geometry in meters
  (stored and derived window start/length, sample spacing; the v3 window
  code decode via the published delay-period table). `load_imaging`
  bundles the file header, frame series and stream counters (signature
  and geometry mismatch counts, skipped bytes). Truncation and garbage
  degrade to `MalformedRecord`, never exceptions. Anchor S8 in
  docs/FORMAT-SOURCES.md (translated from the manufacturer's
  MIT-licensed ARIS File SDK, with the DDF v3 particulars from
  Echoview's public description); validated end to end against the
  SDK's own sample.aris and ten CC0 raw DIDSON clips, which also pinned
  three errata (writer-default sound speed baked into stored ARIS
  window floats, an understated v3 window-code range, and the v3
  whole-second clock lagging its calendar fields).

- Generic Sensor Format (GSF) read-only dialect: record walker with
  checksum verification and graceful truncation handling
  (`iter_records`), typed records for the header, swath bathymetry pings
  (both the 42-byte pre-03.01 and 56-byte ping headers, the full
  scale-factor machinery including nonzero offsets and per-file
  persistence, and the standard beam arrays: depth, nominal depth,
  across/along track, travel time, beam angle, beam flags, quality,
  error and uncertainty arrays), sound velocity profiles, attitude
  series, comments, history, processing parameters and swath summaries
  (`read_gsf`), plus `load_swath` bundling the series with record and
  subrecord counters. Unknown records and sensor-specific subrecords are
  skipped tolerantly and counted by id. Spec anchor S7 in
  docs/FORMAT-SOURCES.md (clean-room from the Leidos specification, no
  gsflib); validated end to end against a real NCEI-archived survey
  file, which also pinned an attitude-record layout erratum against the
  spec table.

- Cerulean SVLog/SVLZ dialect (Surveyor 240-16): checksum-verifying
  frame scanner with forward resynchronization (`iter_frames`),
  transparent gzip handling (truncated and concatenated archives
  included), typed records for ATOF_POINT_DATA, YZ_POINT_DATA,
  ATTITUDE_REPORT, WATER_STATS, NMEA_WRAPPER, MAVLINK_WRAPPER,
  DEVICE_INFORMATION, and SET_PING_PARAMETERS (`read_svlog`), the
  format-defined swath projection `atof_to_yz`, and `load_survey`
  bundling ping/attitude/nav/water series with stream counters.
  Public-ICD anchor S6 in docs/FORMAT-SOURCES.md; framing and checksum
  validated end to end against the vendor's published sample survey.

## 0.2.0 — 2026-08-08

- HYSWEEP® HS2X binary dialect: TLV-chain walker with link verification
  (`iter_frames`), streaming record parser (`parse_hs2x`) for the file
  header, tide/time-mark/gyro/attitude/position series, ping headers,
  beam-solved soundings (grid centimetres, elevation, beam angle,
  no-detect sentinel), and sidescan header/sample pairs. Undecoded types
  surface as `Hs2xOpaque`; undecoded words ride along in `unassigned`.
- Empirical format anchor S5 in docs/FORMAT-SOURCES.md: every named HS2X
  field cross-validated against the paired HSX log of the same session
  (no public HS2X specification exists).
- Dialect sniffing recognizes the HS2X binary magic; sessions and the
  CLI work unchanged on binary files (JSONL renders payloads as hex).
- Synthetic HS2X writer (`write_hs2x`) with consistent prev-size links.

## 0.1.0 — 2026-08-05

Initial release.

- HYPACK® RAW dialect parser: POS, RAW, EC1, GYR, HCP, TID, QUA, KTC, MSG,
  FIX (3- and 5-field), OFF, DEV(+extras), FTP, VER, INF, TND(+extras),
  ELL/PRO/DTM/GEO/HVU/LTP (verbatim), LIN/LBP/LNN/PTS/EOL/EOH.
- HYSWEEP® HSX dialect parser: all of the shared records plus HSX, HSP,
  DV2, OF2, PRI, MBI, SSI, DFT, GPS, PSA, SNR, PRJ, COM, and multi-line
  RMB (15 optional per-beam arrays) and RSS (port/starboard samples).
- Session layer: dialect sniffing, header materialization, device
  registry, streaming records, summaries.
- Synthetic RAW/HSX writers for pipeline testing.
- CLI: `info`, `records`, `to-csv`, `to-jsonl`.
- Graceful degradation everywhere: `UnknownRecord` / `MalformedRecord`,
  never exceptions on file content.
