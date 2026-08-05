"""Typed, immutable record classes for HYPACK RAW and HYSWEEP HSX logs.

Every class documents the source that anchors its field layout (see
docs/FORMAT-SOURCES.md for full citations). Time tags throughout are
**seconds past midnight** of the survey date carried by the ``TND`` header
record; positions are grid coordinates (easting/northing) in the project's
coordinate system — the file does not carry the CRS, only clues (``PRO``,
``ELL``, ``PRJ``).

Records the sources do not anchor are never guessed at: they surface as
:class:`UnknownRecord`. Lines whose tag is known but whose body does not
parse surface as :class:`MalformedRecord` with the error preserved.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Record:
    """Base of every parsed line."""

    tag: str


@dataclass(frozen=True)
class UnknownRecord(Record):
    """A record type the library does not (yet) have an anchored spec for."""

    fields: tuple[str, ...]
    line_number: int = 0


@dataclass(frozen=True)
class MalformedRecord(Record):
    """A known record type whose body failed to parse; nothing is guessed."""

    fields: tuple[str, ...]
    error: str
    line_number: int = 0


# --------------------------------------------------------------------------
# Header records (shared shapes; dialect notes in each docstring)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FileType(Record):
    """``FTP`` — file type identifier. RAW: ``FTP NEW 2``; HSX: ``FTP <name>``."""

    value: str


@dataclass(frozen=True)
class Version(Record):
    """``VER`` — logging software version string."""

    value: str


@dataclass(frozen=True)
class HsxVersion(Record):
    """``HSX <n>`` — HSX format version (HSX dialect only). Anchor: MB-System."""

    version: int


@dataclass(frozen=True)
class SurveyInfo(Record):
    """``INF`` — surveyor, boat, project, area + tide/draft corrections, SV.

    Anchor: MB-System writer (``INF "s" "b" "p" "a" tide draft sv``); USGS
    metadata confirms the trailing three floats.
    """

    surveyor: str
    boat: str
    project: str
    area: str
    tide_correction: float | None = None
    draft_correction: float | None = None
    sound_velocity: float | None = None


@dataclass(frozen=True)
class TimeDate(Record):
    """``TND HH:MM:SS MM/DD/YYYY [extra]`` — survey start time and date.

    Anchor: MB-System (time+date); the RAW example file shows a trailing
    numeric field whose semantics are not anchored — preserved in ``extras``.
    """

    hour: int
    minute: int
    second: int
    month: int
    day: int
    year: int
    extras: tuple[str, ...] = ()


@dataclass(frozen=True)
class Device(Record):
    """``DEV n capability "name" [extras]`` — logged device declaration.

    Anchor: MB-System (``DEV %d %d "%s"``); RAW files append driver details
    (dll path, version) preserved verbatim in ``extras``.
    """

    device: int
    capability: int
    name: str
    extras: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeviceCapability(Record):
    """``DV2 n capability(hex) towfish enabled`` (HSX). Anchor: MB-System."""

    device: int
    capability: int
    towfish: int
    enabled: int


@dataclass(frozen=True)
class DeviceOffsets(Record):
    """Device mounting offsets.

    RAW ``OFF dn n1..n7``: starboard(+stbd), forward(+fwd), height/draft,
    yaw(+cw), roll(+port up), pitch(+bow up), latency seconds. Anchor: USGS
    2014-009-FA metadata. HSX ``OF2 dn type`` + the same seven values with an
    ``offset_type`` discriminator. Anchor: MB-System.
    """

    device: int
    starboard: float
    forward: float
    vertical: float
    yaw: float
    roll: float
    pitch: float
    latency: float
    offset_type: int | None = None


@dataclass(frozen=True)
class PrimaryNav(Record):
    """``PRI n`` — primary navigation device (HSX). Anchor: MB-System."""

    device: int


@dataclass(frozen=True)
class MultibeamInfo(Record):
    """``MBI`` — multibeam device geometry (HSX). Anchor: MB-System.

    ``MBI dn sonar_type(hex) sonar_flags(hex) beam_data_available(hex)
    num_beams_1 num_beams_2 first_beam_angle angle_increment``
    """

    device: int
    sonar_type: int
    sonar_flags: int
    beam_data_available: int
    num_beams_1: int
    num_beams_2: int
    first_beam_angle: float
    angle_increment: float


@dataclass(frozen=True)
class SidescanInfo(Record):
    """``SSI dn flags(hex) port_samples stbd_samples`` (HSX). Anchor: MB-System."""

    device: int
    sonar_flags: int
    port_num_samples: int
    starboard_num_samples: int


@dataclass(frozen=True)
class SurveyParameters(Record):
    """``HSP`` — HYSWEEP survey parameters (HSX). Anchor: MB-System.

    ``HSP min_depth max_depth port_offset_limit stbd_offset_limit
    port_angle_limit stbd_angle_limit high_beam_quality low_beam_quality
    sonar_range towfish_layback units sonar_id``
    """

    minimum_depth: float
    maximum_depth: float
    port_offset_limit: float
    starboard_offset_limit: float
    port_angle_limit: float
    starboard_angle_limit: float
    high_beam_quality: int
    low_beam_quality: int
    sonar_range: float
    towfish_layback: float
    units: int
    sonar_id: int


@dataclass(frozen=True)
class PlannedLineStart(Record):
    """``LBP x y`` — planned line begin point. Anchor: MB-System tag list +
    RAW example file."""

    x: float
    y: float


@dataclass(frozen=True)
class PlannedLine(Record):
    """``LIN n`` — planned line with n waypoints following. Anchor: MB-System
    tag list + RAW example file."""

    waypoints: int


@dataclass(frozen=True)
class PlannedLineName(Record):
    """``LNN <name>`` — planned line name."""

    name: str


@dataclass(frozen=True)
class PlannedWaypoint(Record):
    """``PTS x y`` — planned line waypoint."""

    x: float
    y: float


@dataclass(frozen=True)
class EndOfLine(Record):
    """``EOL`` — end of planned-line block."""


@dataclass(frozen=True)
class EndOfHeader(Record):
    """``EOH`` — header/data boundary."""


@dataclass(frozen=True)
class Projection(Record):
    """``PRJ <proj string>`` — MB-System extension carrying a PROJ command;
    also used for RAW ``PRO`` projection parameters (kept as raw fields)."""

    value: str


@dataclass(frozen=True)
class HeaderMisc(Record):
    """RAW header records preserved with their fields but no deeper model:
    ``ELL`` (ellipsoid), ``PRO`` (projection params), ``DTM`` (datum),
    ``GEO`` (geoid), ``HVU`` (units), ``LTP`` (line template point).
    Attested by the Hydromagic example file; field semantics not fully
    anchored, so they are carried verbatim.
    """

    fields: tuple[str, ...]


# --------------------------------------------------------------------------
# Timestamped data records (device, seconds-past-midnight)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TimedRecord(Record):
    """Base for data records: device number + time tag (s past midnight)."""

    device: int
    time: float


@dataclass(frozen=True)
class Position(TimedRecord):
    """``POS dn t x y`` — grid position (easting, northing).

    Anchors: USGS metadata (RAW), MB-System (HSX).
    """

    x: float
    y: float


@dataclass(frozen=True)
class RawPosition(TimedRecord):
    """``RAW dn t n lat lon alt utc`` — raw GNSS position (RAW dialect).

    lat/lon are logged in HYPACK's "ddmmmm.mmmm" packing: multiply by 100
    to obtain NMEA-style ``ddmm.mmmmm`` (degrees*100 + minutes) — per the
    USGS 2014-009-FA metadata, which anchors this record. ``altitude`` is
    ellipsoid height in meters; ``utc`` is GPS time HHMM(SS...) as logged.
    The raw fields are stored untouched; ``latitude_degrees`` /
    ``longitude_degrees`` decode to signed decimal degrees.
    """

    count: int
    latitude_raw: float
    longitude_raw: float
    altitude: float
    utc: str

    @staticmethod
    def _decode(value: float) -> float:
        nmea = value * 100.0  # -> ddmm.mmmmm (degrees*100 + minutes)
        degrees = int(nmea // 100.0)
        minutes = nmea - degrees * 100.0
        return degrees + minutes / 60.0

    @property
    def latitude_degrees(self) -> float:
        """Decimal degrees; sign convention follows the logged value."""
        sign = -1.0 if self.latitude_raw < 0 else 1.0
        return sign * self._decode(abs(self.latitude_raw))

    @property
    def longitude_degrees(self) -> float:
        sign = -1.0 if self.longitude_raw < 0 else 1.0
        return sign * self._decode(abs(self.longitude_raw))


@dataclass(frozen=True)
class Echosounding(TimedRecord):
    """``EC1 dn t depth`` — single-frequency echosounder depth.

    Anchors: USGS metadata (RAW), MB-System (HSX).
    """

    depth: float


@dataclass(frozen=True)
class Heading(TimedRecord):
    """``GYR dn t heading`` — gyro/compass heading (degrees)."""

    heading: float


@dataclass(frozen=True)
class Attitude(TimedRecord):
    """``HCP dn t heave roll pitch`` — motion sensor.

    Heave meters; roll degrees (+ port up); pitch degrees (+ bow up).
    Anchor: USGS metadata (RAW), MB-System (HSX; MB negates roll into its own
    convention on write, confirming the logged order).
    """

    heave: float
    roll: float
    pitch: float


@dataclass(frozen=True)
class Tide(TimedRecord):
    """``TID dn t correction`` — tide/draft correction."""

    correction: float


@dataclass(frozen=True)
class Draft(TimedRecord):
    """``DFT dn t draft`` — dynamic draft (squat) correction (HSX).
    Anchor: MB-System."""

    draft: float


@dataclass(frozen=True)
class FixMark(TimedRecord):
    """``FIX dn t event [x y]`` — manual event mark.

    Anchor: USGS metadata (3-field form); the Hydromagic example file shows
    a 5-field form with grid coordinates appended.
    """

    event: int
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class Quality(TimedRecord):
    """``QUA dn t n m hdop sats mode [sd_lat sd_lon sd_major]`` — GNSS
    quality. ``m`` is documented as 10 minus HDOP; mode follows NMEA-0183
    fix types; the trailing standard deviations come from GST when present.
    Anchor: USGS 2014-009-FA metadata.
    """

    count: int
    m: float
    hdop: float
    satellites: int
    mode: int
    extras: tuple[float, ...] = ()


@dataclass(frozen=True)
class Message(TimedRecord):
    """``MSG dn t <text>`` — pass-through device message (often NMEA).
    Anchor: USGS metadata."""

    text: str


@dataclass(frozen=True)
class KinematicTide(TimedRecord):
    """``KTC dn t nv eh lh u kval offset draft final`` — RTK water level.

    eh: WGS84 ellipsoidal height; lh: local ellipsoidal height; u:
    undulation; kval: K value; offset: antenna offset; draft: draft
    correction; final: final tide. Anchor: USGS 2014-009-FA metadata.
    """

    count: int
    ellipsoid_height: float
    local_height: float
    undulation: float
    k_value: float
    antenna_offset: float
    draft: float
    final_tide: float


@dataclass(frozen=True)
class GpsMeasurement(TimedRecord):
    """``GPS dn t cog sog hdop mode nsats`` (HSX). Anchor: MB-System."""

    course_over_ground: float
    speed_over_ground: float
    hdop: float
    mode: int
    satellites: int


@dataclass(frozen=True)
class PitchStabilization(TimedRecord):
    """``PSA dn t ping a0 a1`` — pitch stabilization angles (HSX).
    Anchor: MB-System."""

    ping: int
    a0: float
    a1: float


@dataclass(frozen=True)
class SonarSettings(TimedRecord):
    """``SNR dn t ping sonar_id n settings...`` (HSX). Anchor: MB-System."""

    ping: int
    sonar_id: int
    settings: tuple[float, ...]


@dataclass(frozen=True)
class Comment(Record):
    """``COM <text>`` — MB-System extension."""

    text: str


# Bitmask meanings for RawMultibeam.beam_data_available. Anchor: MB-System.
RMB_BEAM_RANGES = 0x0001
RMB_MULTI_RANGES = 0x0002
RMB_SOUNDING_XY = 0x0004
RMB_SOUNDING_DEPTHS = 0x0008
RMB_SOUNDING_ALONG = 0x0010
RMB_SOUNDING_ACROSS = 0x0020
RMB_PITCH_ANGLES = 0x0040
RMB_ROLL_ANGLES = 0x0080
RMB_TAKEOFF_ANGLES = 0x0100
RMB_AZIMUTH_ANGLES = 0x0200
RMB_TIME_DELAYS = 0x0400
RMB_INTENSITIES = 0x0800
RMB_QUALITY = 0x1000
RMB_FLAGS = 0x2000
RMB_UNCERTAINTIES = 0x4000


@dataclass(frozen=True)
class RawMultibeam(TimedRecord):
    """``RMB`` — one multibeam ping (HSX). Anchor: MB-System.

    First line: ``RMB dn t sonar_type(hex) sonar_flags(hex)
    beam_data_available(hex) num_beams sound_velocity ping``. Follow-on
    lines carry per-beam arrays in bitmask order; ``RMB_SOUNDING_XY``
    contributes two lines (eastings then northings). Absent arrays are None.
    """

    sonar_type: int
    sonar_flags: int
    beam_data_available: int
    num_beams: int
    sound_velocity: float
    ping: int
    beam_ranges: tuple[float, ...] | None = None
    multi_ranges: tuple[float, ...] | None = None
    eastings: tuple[float, ...] | None = None
    northings: tuple[float, ...] | None = None
    depths: tuple[float, ...] | None = None
    along: tuple[float, ...] | None = None
    across: tuple[float, ...] | None = None
    pitch_angles: tuple[float, ...] | None = None
    roll_angles: tuple[float, ...] | None = None
    takeoff_angles: tuple[float, ...] | None = None
    azimuth_angles: tuple[float, ...] | None = None
    time_delays: tuple[int, ...] | None = None
    intensities: tuple[int, ...] | None = None
    quality: tuple[int, ...] | None = None
    flags: tuple[int, ...] | None = None
    uncertainties: tuple[float, ...] | None = None


@dataclass(frozen=True)
class RawSidescan(TimedRecord):
    """``RSS`` — one sidescan ping (HSX): header line + port and starboard
    sample lines. Anchor: MB-System.

    ``RSS dn t flags(hex) port_n stbd_n sound_velocity ping altitude
    sample_rate min_amplitude max_amplitude bit_shift frequency``
    """

    sonar_flags: int
    port_num_samples: int
    starboard_num_samples: int
    sound_velocity: float
    ping: int
    altitude: float
    sample_rate: float
    minimum_amplitude: int
    maximum_amplitude: int
    bit_shift: int
    frequency: int
    port: tuple[int, ...] = field(default=())
    starboard: tuple[int, ...] = field(default=())
