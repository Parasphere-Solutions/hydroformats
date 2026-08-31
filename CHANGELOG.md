# Changelog

## Unreleased

- Blueprint Oculus read-only dialect (the ViewPoint `.oculus` V1 log
  container and the raw SimplePingResult stream of the M370s/M750d/
  M1200d/M3000d multibeam imaging sonars): magic-verified item walker
  honoring the headers' own declared sizes, with forward
  resynchronization on the item magic (`iter_items`), and typed
  records (`read_oculus`; `read_oculus_raw` for bare message streams
  such as the liboculus captures) for simple ping results in both
  message versions, keyed on "msgVersion 2 or not" because real
  version 1 hardware stamps 0. Each `OculusPing` carries the fire
  settings (mode, flags with decoded bit properties, range demand
  with its meters-or-percent flag, gain, sound speed, salinity), the
  sonar's report (frequency, temperature, pressure, applied sound
  speed, version 2 heading/pitch/roll, both versions' clocks: the
  epoch-seconds item timestamp, the version 2 seconds-since-power-up
  double, and the version 1 start word verbatim), the image geometry
  (range lines, beams, sample size from the DataSizeType word, range
  resolution and the derived imaged range), the per-ping bearing
  table in hundredths of a degree with degree and aperture accessors
  (aperture is data, never a model table), per-row gain words split
  out when flags bit 2 sent them, and the raw samples with row and
  beam accessors. The image is always sliced at the message's own
  imageOffset (real files pad nonzero filler after the bearing
  table). `hydroformats.oculus.load_imaging` bundles the file header,
  ping series and stream counters (skipped item types and unknown
  message ids counted by value, compressed items, malformed count,
  skipped bytes); it is exported at the package level as
  `load_oculus` because DDF holds the package-level `load_imaging`
  name. ViewPoint V2 SQLite logs and encrypted logs are refused
  loudly by name; compressed items and non-sonar item types skip
  tolerantly; truncation degrades to `MalformedRecord`, never
  exceptions. Clean-room anchor S13 in docs/FORMAT-SOURCES.md
  (license-verified permissive sources only: BSD liboculus, the BSD
  files of ENSTA's oculus_driver, MIT ESP3, Apache oculus-python;
  Blueprint's GPL Oculus.h deliberately not consulted, no public ICD
  exists); byte-verified end to end against a CC0 ViewPoint survey
  (151 version 2 pings framing with zero gaps, both clocks pinned
  against each other, the range demand reproduced from resolution
  times range lines) and liboculus's BSD version 1 captures, which
  together surfaced seven errata (msgVersion 0 on real version 1
  hardware, a float-seconds misreading of the version 1 start word,
  garbage temperature/pressure doubles, fill patterns in unset fire
  bytes, nonzero filler before imageOffset, the 36-versus-40-byte
  item header, and bearing tables that ignore datasheet apertures).
- Klein SDF read-only dialect (the native SonarPro recording of the
  Klein side scan families: System 3000 and the 3900/NGS series,
  System 5000, System 7000 and the 3500 series): marker-verified page
  walker with forward resynchronization on the 0xFFFFFFFF ping marker
  (`iter_pages`), typed records (`read_klein`) decoding the shared
  page header for every family (time with float fractional seconds,
  towfish and ship navigation in radians with degree properties,
  heading/pitch/roll, depth, altitude, temperature, range, the three
  differently-united speed fields, TVG, sample frequency, tow
  geometry, and the version 4 extension: SBP settings, wing angle,
  layback position, TPU version and capability words, with absent
  fields None on version 3 pages) plus the count-prefixed channel
  arrays in typedef order (System 3000 port/stbd LF/HF side scan and
  the sub-bottom channel with its 4-byte count exception; the full
  84-array System 5000 structure; 3500-series port/starboard 32-bit
  pages with the center frequency word), decoded lazily with raw
  sample bytes carried verbatim. System 7000 pages decode header-only
  with the data region verbatim (the spec's own wording: "tentatively
  defined"). `hydroformats.klein.load_survey` bundles pings, System
  7000 pages and stream counters (unknown page versions counted,
  malformed pages counted, bytes skipped), with per-channel regrouping
  via `channel_series()`; it is exported at the package level as
  `load_klein` because SVLog holds the package-level `load_survey`
  name. Unknown page versions skip tolerantly; truncation and
  overrunning counts degrade to `MalformedRecord`, never exceptions.
  Clean-room anchor S13 in docs/FORMAT-SOURCES.md (the L-3 Klein SDF
  data page specification Rev 2.05 and the SonarPro UDP companion
  spec, with 3500-series specifics from OceanScan's MIT-licensed
  reference reader; GPL and copyleft parsers never opened), including
  three anchor errata found inside the vendor documents (the version 3
  SBP signedness contradiction, the wingAngle float/U32 conflict, and
  the UDP network-byte-order trap).
- Teledyne RESON s7k read-only dialect (the native logging of the
  SeaBat 7k sonar generation and a major survey-industry interchange
  format): sync-verified Data Record Frame walker with forward
  resynchronization on the 0x0000FFFF pattern, 7KTIME timestamps,
  optional-data splitting and reported-never-raised byte-sum checksum
  verification (`iter_records`), typed records (`read_s7k`) for
  position (1003, geographic or grid, the optional satellite count
  size-detected), CTD/SVP casts (1010), geodesy (1011),
  roll/pitch/heave and heading (1012/1013), per-ping sonar settings
  (7000) and the remote-control settings snapshot (7503, with an
  append-tolerant tail), receive beam geometry (7004, transmit delays
  size-detected), the deprecated bathymetry record (7006), raw
  detection data (7027: per-detection beam number, fractional sample
  number, receive steering angle, flags, quality, and the
  block-size-gated uncertainty/intensity/gate fields, plus the
  Appendix F sample-to-travel-time reduction as a property), snippet
  imagery (7028, raw 16/32-bit intensity windows with the empty-window
  rule) and snippet backscattering strength (7058, dB samples with
  optional footprint areas), surface sound velocity (7610,
  temperature and pressure size-detected), and the 7018/7042 water
  column records as decoded headers with their sample matrices
  deliberately skipped. `hydroformats.s7k.load_swath` matches each
  ping's settings, detections, snippets and backscatter together
  (keyed by device, enumerator, ping and multi-ping sequence) beside
  the navigation, motion, sound velocity and geometry series, with
  stream counters (unknown types counted by id, checksum failures,
  skipped bytes); it is exported at the package level as `load_s7k`
  because GSF holds the package-level `load_swath` name. Unknown
  record types skip tolerantly; truncation degrades to
  `MalformedRecord`, never exceptions. Clean-room DFD anchor S12 in
  docs/FORMAT-SOURCES.md (Teledyne 7k Data Format Version 3.10; no
  third-party parser consulted); validated end to end against a real
  NCEI-archived SeaBat T50-P line (every byte framed, zero malformed,
  512-beam pings with snippets, navigation into Galveston Bay, raw
  observables reducing to channel depths) and two 2009 PDS-logged
  SeaBat 7125 lines exercising the older record vintages, which also
  surfaced a stale-checksum writer quirk on 7610 records and a
  checksum-flag bit numbering contradiction inside the DFD itself
  (both recorded as anchor errata).
- Kongsberg KMALL read-only dialect (the current-generation datagram
  format of the EM series multibeam echosounders, successor of .all):
  datagram walker with trailing-length-word verification and forward
  resynchronization on the '#' type code (`iter_datagrams`), typed
  records (`read_kmall`) for #MRZ multibeam pings (the full ping info
  block including position, sound speed and mode words, per-sector
  transmit blocks, receiver info, and per-beam soundings preserving
  the raw observables: two way travel time and beam angle beside the
  processed x/y/z, detection type/method/quality, both reflectivity
  values with the applied source level, receiver sensitivity,
  calibration and TVG, and the seabed image samples split per beam),
  #SKM attitude blocks (full KM binary samples with delayed heave),
  #SPO/#CPO positions (corrected values plus the raw sensor telegram),
  #SVP sound velocity or CTD casts, #IIP/#IOP installation and runtime
  parameter text blobs, and #CHE compatibility heave. #MWC water
  column datagrams decode header-only by design (identified, timed,
  tied to their ping, byte size recorded; the sample payload is
  deliberately skipped). Partitioned #MRZ datagrams (raw UDP logging;
  SIS merges them) are rejoined per the spec's multibeam data logging
  chapter, honoring the revision I change that put the common body in
  every partition, with incomplete sets degrading to
  `MalformedRecord`. Every variable block is walked by its own
  declared byte size, so newer-revision extensions skip faithfully.
  `hydroformats.kmall.load_swath` bundles pings, positions, attitude,
  casts, parameter text and stream counters (unknown types counted by
  code, end-length mismatches, skipped bytes, partition accounting);
  it is exported at the package level as `load_kmall` because GSF
  holds the package-level `load_swath` name. Clean-room spec anchor
  S11 in docs/FORMAT-SOURCES.md (Kongsberg document 410224 revision J;
  no third-party parser consulted); validated end to end against a
  real NCEI-archived EM 304 line from NOAA Ship Okeanos Explorer
  (every byte framed, zero malformed, every trailing length word
  verified, the usable-sounding criterion reproducing the datagrams'
  own valid counts exactly, and slant ranges recomputed from the raw
  observables agreeing with the stored xyz to within refraction).

- EdgeTech JSF read-only dialect (the native recording format of the
  Discover/JStar topsides, including the 6205 dual-frequency
  bathymetric side scan): marker-verified message walker with forward
  resynchronization on the 0x1601 header marker (`iter_messages`),
  typed records (`read_jsf`) for sonar data messages (type 80: the full
  240-byte header, raw 16-bit block floating point trace integers plus
  a `scaled()` accessor applying the ICD's 2^-N weighting factor rule,
  the 20-bit MSB field extensions and fractional LSB digits, channel
  and frequency identification from the subsystem/channel words),
  bathymetric data messages (type 3000: per-sounding time delay and
  angle counts with both scale factors, 0.5 dB amplitudes, angle
  uncertainties, SNR/quality/cleaning flags, the logged TVG, and
  hand-checkable accessors for echo time, angle from nadir, slant range
  and raw x/z soundings), system information (182), NMEA strings
  (2002), pitch roll (2020), pressure sensor readings (2060), and the
  3001/3002/3003/3004 attitude, pressure, altitude and position
  messages. `hydroformats.jsf.load_survey` bundles per-channel side
  scan series, bathymetric pings, navigation/attitude/sensor series and
  stream counters (unknown types counted by id, skipped bytes); it is
  exported at the package level as `load_jsf` because SVLog holds the
  package-level `load_survey` name. Unknown message types skip
  tolerantly; truncation degrades to `MalformedRecord`, never
  exceptions. Clean-room ICD anchor S9 in docs/FORMAT-SOURCES.md
  (EdgeTech document 0023492 Rev. R; no third-party parser consulted);
  validated end to end against a real archived SB-512i chirp line
  (every byte framed, zero malformed, both time tracks and the
  navigation riders cross-pinned against the interleaved NMEA stream),
  which also surfaced an NMEA source-byte erratum. The type 3000
  bathymetric decode rests on the ICD tables and synthetic fixtures
  until a real 6205 file is available (none is publicly downloadable).
- Triton XTF read-only dialect, the industry's broadest sidescan
  interchange format (Klein, EdgeTech, Kongsberg, Benthos, CMAX and
  many more chains export it). File header with the full CHANINFO
  channel blocks (channel type, sub-channel number, correction flags,
  polarity, bytes per sample and sample format, mounting offsets),
  0xFACE packet framing with resynchronization on the magic word, and
  typed records (`read_xtf`) for sonar pings (256-byte ping header
  with per-ping nav/attitude/tow fields plus per-channel headers and
  raw sample bytes with on-demand 8/16/32-bit and IEEE-float decoding),
  attitude packets, notes, raw serial (NMEA) lines, raw vendor
  bathymetry passthrough, and Reson bathy snippet packets (SNP0/SNP1
  headers decoded, fragments carried raw). `hydroformats.xtf.load_survey`
  (exported at package level as `load_sidescan`) bundles the header,
  per-channel ping series with channel metadata, attitude/notes/serial
  series and stream counters; unknown packet types are skipped and
  counted by type. XTF's navigation duplication (ship vs sensor
  position, layback vs coordinates) is surfaced field for field and
  deliberately not resolved: georeferencing policy belongs to the
  consumer. Spec anchor S10 in docs/FORMAT-SOURCES.md (clean-room from
  Triton's Rev. 41 specification, no third-party parser consulted);
  validated end to end against Triton's own published demo survey,
  which framed every byte exactly and pinned three spec errata (the
  attitude table's misprinted prefix offsets, the ping header's
  overlapping tail offsets, and a UniPolar flag that contradicts the
  demo data's own samples).

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
