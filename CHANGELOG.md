# Changelog

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
