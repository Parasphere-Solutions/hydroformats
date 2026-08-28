# Changelog

## Unreleased

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
