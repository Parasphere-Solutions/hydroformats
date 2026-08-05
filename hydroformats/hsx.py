"""HYSWEEP HSX dialect parser (multibeam text logging).

Anchor: MB-System's mb201 reader/writer (mbsys_hysweep.h, mbr_hysweep1.c),
which embeds the HSX format specification. Multi-line records (RMB, RSS)
are assembled here: the header line declares which per-beam arrays follow,
one array per subsequent line (RMB_SOUNDING_XY contributes two lines).
"""
from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

from ._dispatch import COMMON_TABLE, ParseFn, _f, _i, dispatch
from .framing import Line, iter_lines
from .records import (
    RMB_AZIMUTH_ANGLES,
    RMB_BEAM_RANGES,
    RMB_FLAGS,
    RMB_INTENSITIES,
    RMB_MULTI_RANGES,
    RMB_PITCH_ANGLES,
    RMB_QUALITY,
    RMB_ROLL_ANGLES,
    RMB_SOUNDING_ACROSS,
    RMB_SOUNDING_ALONG,
    RMB_SOUNDING_DEPTHS,
    RMB_SOUNDING_XY,
    RMB_TAKEOFF_ANGLES,
    RMB_TIME_DELAYS,
    RMB_UNCERTAINTIES,
    Device,
    DeviceCapability,
    DeviceOffsets,
    Draft,
    GpsMeasurement,
    HsxVersion,
    MalformedRecord,
    MultibeamInfo,
    PitchStabilization,
    PrimaryNav,
    Projection,
    RawMultibeam,
    RawSidescan,
    Record,
    SidescanInfo,
    SonarSettings,
    SurveyParameters,
)

# (bitmask, RawMultibeam attribute, element converter) in follow-line order.
# Anchor: MB-System writer emits arrays in ascending bitmask order, with
# RMB_SOUNDING_XY producing eastings then northings on two lines.
_RMB_ARRAYS: tuple[tuple[int, str, type], ...] = (
    (RMB_BEAM_RANGES, "beam_ranges", float),
    (RMB_MULTI_RANGES, "multi_ranges", float),
    (RMB_SOUNDING_XY, "eastings", float),
    (RMB_SOUNDING_XY, "northings", float),
    (RMB_SOUNDING_DEPTHS, "depths", float),
    (RMB_SOUNDING_ALONG, "along", float),
    (RMB_SOUNDING_ACROSS, "across", float),
    (RMB_PITCH_ANGLES, "pitch_angles", float),
    (RMB_ROLL_ANGLES, "roll_angles", float),
    (RMB_TAKEOFF_ANGLES, "takeoff_angles", float),
    (RMB_AZIMUTH_ANGLES, "azimuth_angles", float),
    (RMB_TIME_DELAYS, "time_delays", int),
    (RMB_INTENSITIES, "intensities", int),
    (RMB_QUALITY, "quality", int),
    (RMB_FLAGS, "flags", int),
    (RMB_UNCERTAINTIES, "uncertainties", float),
)


def _parse_hsx_version(fields: tuple[str, ...], line: Line) -> Record:
    return HsxVersion(tag="HSX", version=int(fields[0]))


def _parse_dv2(fields: tuple[str, ...], line: Line) -> Record:
    return DeviceCapability(
        tag="DV2", device=_i(fields, 0), capability=int(fields[1], 16),
        towfish=_i(fields, 2), enabled=_i(fields, 3),
    )


def _parse_of2(fields: tuple[str, ...], line: Line) -> Record:
    return DeviceOffsets(
        tag="OF2", device=_i(fields, 0), offset_type=_i(fields, 1),
        starboard=_f(fields, 2), forward=_f(fields, 3), vertical=_f(fields, 4),
        yaw=_f(fields, 5), roll=_f(fields, 6), pitch=_f(fields, 7),
        latency=_f(fields, 8),
    )


def _parse_pri(fields: tuple[str, ...], line: Line) -> Record:
    return PrimaryNav(tag="PRI", device=_i(fields, 0))


def _parse_mbi(fields: tuple[str, ...], line: Line) -> Record:
    return MultibeamInfo(
        tag="MBI", device=_i(fields, 0), sonar_type=int(fields[1], 16),
        sonar_flags=int(fields[2], 16), beam_data_available=int(fields[3], 16),
        num_beams_1=_i(fields, 4), num_beams_2=_i(fields, 5),
        first_beam_angle=_f(fields, 6), angle_increment=_f(fields, 7),
    )


def _parse_ssi(fields: tuple[str, ...], line: Line) -> Record:
    return SidescanInfo(
        tag="SSI", device=_i(fields, 0), sonar_flags=int(fields[1], 16),
        port_num_samples=_i(fields, 2), starboard_num_samples=_i(fields, 3),
    )


def _parse_hsp(fields: tuple[str, ...], line: Line) -> Record:
    return SurveyParameters(
        tag="HSP",
        minimum_depth=_f(fields, 0), maximum_depth=_f(fields, 1),
        port_offset_limit=_f(fields, 2), starboard_offset_limit=_f(fields, 3),
        port_angle_limit=_f(fields, 4), starboard_angle_limit=_f(fields, 5),
        high_beam_quality=_i(fields, 6), low_beam_quality=_i(fields, 7),
        sonar_range=_f(fields, 8), towfish_layback=_f(fields, 9),
        units=_i(fields, 10), sonar_id=_i(fields, 11),
    )


def _parse_dft(fields: tuple[str, ...], line: Line) -> Record:
    return Draft(tag="DFT", device=_i(fields, 0), time=_f(fields, 1), draft=_f(fields, 2))


def _parse_gps(fields: tuple[str, ...], line: Line) -> Record:
    return GpsMeasurement(
        tag="GPS", device=_i(fields, 0), time=_f(fields, 1),
        course_over_ground=_f(fields, 2), speed_over_ground=_f(fields, 3),
        hdop=_f(fields, 4), mode=_i(fields, 5), satellites=_i(fields, 6),
    )


def _parse_psa(fields: tuple[str, ...], line: Line) -> Record:
    return PitchStabilization(
        tag="PSA", device=_i(fields, 0), time=_f(fields, 1),
        ping=_i(fields, 2), a0=_f(fields, 3), a1=_f(fields, 4),
    )


def _parse_snr(fields: tuple[str, ...], line: Line) -> Record:
    count = _i(fields, 4)
    return SonarSettings(
        tag="SNR", device=_i(fields, 0), time=_f(fields, 1), ping=_i(fields, 2),
        sonar_id=_i(fields, 3),
        settings=tuple(float(x) for x in fields[5 : 5 + count]),
    )


def _parse_prj(fields: tuple[str, ...], line: Line) -> Record:
    return Projection(tag="PRJ", value=line.body.strip())


HSX_TABLE: dict[str, ParseFn] = {
    **COMMON_TABLE,
    "HSX": _parse_hsx_version,
    "DV2": _parse_dv2,
    "OF2": _parse_of2,
    "PRI": _parse_pri,
    "MBI": _parse_mbi,
    "SSI": _parse_ssi,
    "HSP": _parse_hsp,
    "DFT": _parse_dft,
    "GPS": _parse_gps,
    "PSA": _parse_psa,
    "SNR": _parse_snr,
    "PRJ": _parse_prj,
}

_HSX_DEV_ANCHOR = Device  # HSX DEV parses via the common table.


def _values(line: Line, converter: type) -> tuple:
    text = f"{line.tag} {line.body}".strip() if line.body else line.tag
    return tuple(converter(token) for token in text.split())


def parse_hsx(source: str | Path | io.TextIOBase) -> Iterator[Record]:
    """Stream records from an HSX file, assembling multi-line RMB/RSS.

    Never raises on content: a short or corrupt continuation block yields a
    MalformedRecord carrying the header fields, and parsing resumes on the
    next tagged line.
    """
    lines = iter_lines(source)
    pending: Line | None = None
    while True:
        line = pending if pending is not None else next(lines, None)
        pending = None
        if line is None:
            return
        if line.tag == "RMB":
            record, pending = _assemble_rmb(line, lines)
            yield record
        elif line.tag == "RSS":
            record, pending = _assemble_rss(line, lines)
            yield record
        else:
            yield dispatch(line, HSX_TABLE)


def _assemble_rmb(header: Line, lines) -> tuple[Record, Line | None]:
    from .framing import tokenize

    fields = tokenize(header.body)
    try:
        base = dict(
            device=int(fields[0]), time=float(fields[1]),
            sonar_type=int(fields[2], 16), sonar_flags=int(fields[3], 16),
            beam_data_available=int(fields[4], 16), num_beams=int(fields[5]),
            sound_velocity=float(fields[6]), ping=int(fields[7]),
        )
    except (ValueError, IndexError) as error:
        return (
            MalformedRecord(tag="RMB", fields=fields, error=str(error),
                            line_number=header.number),
            None,
        )
    arrays: dict[str, tuple] = {}
    available = base["beam_data_available"]
    for mask, attribute, converter in _RMB_ARRAYS:
        if not available & mask:
            continue
        line = next(lines, None)
        if line is None:
            return (
                MalformedRecord(tag="RMB", fields=fields,
                                error=f"file ended before {attribute} line",
                                line_number=header.number),
                None,
            )
        try:
            arrays[attribute] = _values(line, converter)
        except ValueError:
            # Not a continuation line: a truncated ping. Surface the header as
            # malformed and hand the tagged line back to the main loop.
            return (
                MalformedRecord(tag="RMB", fields=fields,
                                error=f"expected {attribute} array, got tagged line "
                                      f"{line.tag!r}",
                                line_number=header.number),
                line,
            )
    return RawMultibeam(tag="RMB", **base, **arrays), None


def _assemble_rss(header: Line, lines) -> tuple[Record, Line | None]:
    from .framing import tokenize

    fields = tokenize(header.body)
    try:
        base = dict(
            device=int(fields[0]), time=float(fields[1]),
            sonar_flags=int(fields[2], 16), port_num_samples=int(fields[3]),
            starboard_num_samples=int(fields[4]), sound_velocity=float(fields[5]),
            ping=int(fields[6]), altitude=float(fields[7]),
            sample_rate=float(fields[8]), minimum_amplitude=int(fields[9]),
            maximum_amplitude=int(fields[10]), bit_shift=int(fields[11]),
            frequency=int(fields[12]),
        )
    except (ValueError, IndexError) as error:
        return (
            MalformedRecord(tag="RSS", fields=fields, error=str(error),
                            line_number=header.number),
            None,
        )
    samples: list[tuple[int, ...]] = []
    for side in ("port", "starboard"):
        line = next(lines, None)
        if line is None:
            return (
                MalformedRecord(tag="RSS", fields=fields,
                                error=f"file ended before {side} samples",
                                line_number=header.number),
                None,
            )
        try:
            samples.append(_values(line, int))
        except ValueError:
            return (
                MalformedRecord(tag="RSS", fields=fields,
                                error=f"expected {side} samples, got tagged line "
                                      f"{line.tag!r}",
                                line_number=header.number),
                line,
            )
    return RawSidescan(tag="RSS", **base, port=samples[0], starboard=samples[1]), None
