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

import struct
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
    """``POS dn t x y [extras]`` — grid position (easting, northing).

    Anchors: USGS metadata (RAW), MB-System (HSX). Real RAW files log a
    fifth numeric value (observed in USGS 2014-009-FA data; semantics
    unanchored) — trailing fields are preserved verbatim in ``extras``.
    """

    x: float
    y: float
    extras: tuple[str, ...] = ()


@dataclass(frozen=True)
class RawPosition(TimedRecord):
    """``RAW dn t n lat lon alt utc`` — raw GNSS position (RAW dialect).

    lat/lon are logged in HYPACK's ``ddmmmm.mmmm`` packing. **Divide by 100**
    to obtain NMEA-style ``ddmm.mmmmm`` (degrees*100 + minutes). The USGS
    2014-009-FA metadata prose says "multiply by 100", but the actual data
    files from that same survey prove division: ``RAW ... 410966.80360
    -714331.75760 ...`` decodes to 41.1611°N 71.7220°W, which matches the
    UTM-18N ``POS`` eastings/northings logged in the same second, while
    multiplication yields impossible coordinates (see
    docs/FORMAT-SOURCES.md, "anchor errata"). ``altitude`` is ellipsoid
    height in meters; ``utc`` is GPS time (HHMMSS.sss in real files) kept
    verbatim. Raw fields are stored untouched; ``latitude_degrees`` /
    ``longitude_degrees`` decode to signed decimal degrees.
    """

    count: int
    latitude_raw: float
    longitude_raw: float
    altitude: float
    utc: str

    @staticmethod
    def _decode(value: float) -> float:
        nmea = value / 100.0  # ddmmmm.mmmm -> ddmm.mmmmm (degrees*100 + minutes)
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


# --------------------------------------------------------------------------
# HS2X binary records (HYSWEEP 64-bit edit format)
#
# There is no public byte-level HS2X specification (HYPACK's stated policy;
# see docs/FORMAT-SOURCES.md, source S5). Field layouts below are anchored
# empirically: every named field was cross-validated against the paired HSX
# text log of the same logging session. Words whose meaning that validation
# could not pin are carried verbatim in ``unassigned`` tuples, in payload
# order, with their offsets documented — nothing is guessed.
#
# Integer conventions proven by S5: times are milliseconds past midnight;
# grid coordinates and elevations are metric centimetres (divide by
# 30.4800609601 for US-survey-foot grids — dividing by 30.48 leaves a 2 ppm
# scale error that grows to tens of feet at State Plane magnitudes); angles
# are millidegrees except beam angles, which are centidegrees.
# --------------------------------------------------------------------------


def _packed_to_degrees(value: float) -> float:
    """HYPACK ``ddmmmm.mmmm`` packing to signed decimal degrees (see
    :class:`RawPosition` for the division-not-multiplication errata)."""
    sign = -1.0 if value < 0 else 1.0
    nmea = abs(value) / 100.0
    degrees = int(nmea // 100.0)
    minutes = nmea - degrees * 100.0
    return sign * (degrees + minutes / 60.0)


@dataclass(frozen=True)
class Hs2xFileHeader(Record):
    """Bootstrap record (type 26): format magic, version, build date.

    Payload: NUL-separated strings (``DATAGRAM VERSION <n>``, a build date
    such as ``03-FEB-2022``) then one 32-bit word (``unassigned``). The
    build date identifies the writing software build, not the survey date.
    """

    text: str
    build_date: str
    version: int | None
    unassigned: tuple[int, ...]


@dataclass(frozen=True)
class Hs2xOpaque(Record):
    """Any HS2X record type without an anchored layout — payload verbatim.

    Covers the configuration block (types 50–55: device/geodesy/text blobs,
    observed but not decoded) and any type this library has no decoder
    for. Tag is ``T<type>``.
    """

    record_type: int
    payload: bytes


@dataclass(frozen=True)
class Hs2xTimed(Record):
    """Base for HS2X records led by a time tag (ms past midnight)."""

    time_ms: int

    @property
    def time(self) -> float:
        """Seconds past midnight (matches HSX/RAW time tags)."""
        return self.time_ms / 1000.0


@dataclass(frozen=True)
class Hs2xTimeMark(Hs2xTimed):
    """Type 61 — line time marker (one at data start, one at end).

    Anchor S5: the two payload words after the time were zero in the
    validation capture (``unassigned``).
    """

    unassigned: tuple[int, ...]


@dataclass(frozen=True)
class Hs2xHeading(Hs2xTimed):
    """Type 62 — gyro heading.

    Payload: time_ms(i32), device(u16), u16, heading(i32, millidegrees).
    Anchor S5: per-device record counts equal the paired HSX ``GYR`` census
    exactly, and headings match HSX values at the same timestamps.
    """

    device: int
    heading_millideg: int
    unassigned: tuple[int, ...]

    @property
    def heading_degrees(self) -> float:
        return self.heading_millideg / 1000.0


@dataclass(frozen=True)
class Hs2xAttitude(Hs2xTimed):
    """Type 63 — motion sensor (roll/pitch; heave not yet located).

    Payload: time_ms(i32), device(u16), u16, i32, i32, roll(i32, mdeg),
    i32, pitch(i32, mdeg), i32. Anchor S5: roll and pitch equal the paired
    HSX ``HCP`` values to the millidegree at matching timestamps. The
    remaining words were zero in validation (heave was 0.00 throughout that
    session, so the heave word cannot be identified yet) — ``unassigned``.
    """

    device: int
    roll_millideg: int
    pitch_millideg: int
    unassigned: tuple[int, ...]

    @property
    def roll_degrees(self) -> float:
        return self.roll_millideg / 1000.0

    @property
    def pitch_degrees(self) -> float:
        return self.pitch_millideg / 1000.0


@dataclass(frozen=True)
class Hs2xPosition(Hs2xTimed):
    """Type 67 — navigation fix: grid position plus raw geographic inputs.

    Payload: time_ms(i32), i32, easting(i32 cm), northing(i32 cm), a
    duplicate easting/northing pair, i32, i32, four u16, then four doubles:
    latitude and longitude in HYPACK ``ddmmmm.mmmm`` packing, ellipsoidal
    height (metres), and UTC seconds past midnight. Anchor S5: grid pair
    tracks the paired HSX ``POS`` series (constant sub-foot antenna offset,
    0.01 ft scatter) once the survey-foot factor is applied; the packed
    lat/lon decode to the survey site; ``utc_seconds`` equals the local
    time tag plus the session's UTC offset exactly. The duplicate grid pair
    was byte-identical to the first in validation — ``unassigned``.
    """

    easting_cm: int
    northing_cm: int
    latitude_packed: float
    longitude_packed: float
    ellipsoid_height: float
    utc_seconds: float
    unassigned: tuple[int, ...]

    @property
    def latitude_degrees(self) -> float:
        return _packed_to_degrees(self.latitude_packed)

    @property
    def longitude_degrees(self) -> float:
        return _packed_to_degrees(self.longitude_packed)


@dataclass(frozen=True)
class Hs2xTide(Hs2xTimed):
    """Type 60 — water level record.

    Payload: time_ms(i32), flags(u16), u16, i32, i32, value(u16,
    centimetres), u16. Anchor S5: the series aligns with the paired HSX
    ``TID`` records one-to-one on time. Two sub-series share the layout,
    split by ``flags``: with ``flags`` 0 (329 of 408 in validation),
    ``tide_cm`` tracks the HSX tide at centimetre level (median 1 cm,
    occasional 3–7 cm excursions — a differently staged/filtered value,
    not a byte copy); with ``flags`` 0x0300 (the rest), the value sits
    ~5.1 m above the tide — an uncorrected water-level candidate. Treat
    ``flags != 0`` records with care.
    """

    flags: int
    tide_cm: int
    unassigned: tuple[int, ...]


@dataclass(frozen=True)
class Hs2xPing(Hs2xTimed):
    """Type 68 — one multibeam ping header; its beams follow as type-69
    records (``beam_count`` of them, in swath order).

    Named fields and anchors (S5, against the paired HSX log): ``time_ms``
    and ``ping_number`` equal the HSX ``RMB`` time and ping fields for all
    pings; ``device`` and ``sonar_type`` equal the RMB device and
    sonar-type fields; ``sound_velocity_cm_s`` is the RMB sound velocity
    ×100; ``easting_cm``/``northing_cm`` track the HSX ``POS`` series with
    a constant sub-foot lever arm; ``heading_millideg``,
    ``roll_millideg``, ``pitch_millideg`` equal the navigation gyro and
    ``HCP`` values at the ping time to the millidegree.

    ``unassigned`` carries, in order: u32 at +4 (sub-second-scale counter),
    u16 at +14, i32 at +32, i32 at +40, i32 at +44 (millimetre-scale,
    near-constant in validation; heave candidate), i32 at +56 (constant
    134), i32 at +60 (a second heading-like series matching neither gyro
    exactly), u16 at +64, u16 at +66. ``tail`` is payload bytes 68–143
    verbatim (zeros plus a constant 10-byte marker at +96 in validation).
    """

    device: int
    sonar_type: int
    beam_count: int
    sound_velocity_cm_s: int
    ping_number: int
    easting_cm: int
    northing_cm: int
    heading_millideg: int
    roll_millideg: int
    pitch_millideg: int
    unassigned: tuple[int, ...]
    tail: bytes


# Indices into Hs2xSounding.unassigned used by the no-detect signature.
_SND_LINEAR = 2
_SND_LOG = 4
_SND_QUALITY = 8


@dataclass(frozen=True)
class Hs2xSounding(Record):
    """Type 69 — one beam-solved sounding (52-byte payload).

    Anchored fields (S5): ``easting_cm``/``northing_cm``/``elevation_cm``
    are the solved grid position and elevation in metric centimetres
    (validated against the paired HSX ``POS``/``EC1``/``TID`` series and
    the transducer offset chain; elevation is negative below datum).
    ``beam_angle_cdeg`` is the beam angle in centidegrees, sweeping
    monotonically across the swath with port negative in validation.

    ``unassigned`` carries the remaining words in payload order:

    ======  ======  =====================================================
    index   offset  observed behaviour in the validation capture
    ======  ======  =====================================================
    0       +16     i32, always 0
    1       +20     i32, millimetre-scale, monotone with beam angle
                    (across-track-like, but not the E/N offset; scale
                    varies with depth — an intermediate solver quantity)
    2       +24     i32, linear scalar; 0 on no-detect beams; grows with
                    range (TVU/THU candidate per the HYPACK manuals)
    3       +28     i16, always 0
    4       +30     i16, 78-step geometric ladder (ratio 1.0593 ≈ 0.5 dB);
                    1 on no-detect beams (TVU/THU candidate)
    5       +32     i32, high half a small counter (0–5)
    6       +36     i32, always 0
    7       +40     i16, millimetre-scale, along-track-like (see index 1)
    8       +42     i16, quality-like; exactly 1 on no-detect beams
    9       +44     i32, always 0
    10      +48     i32, always 0
    ======  ======  =====================================================

    No-detect beams (unfilled swath slots) park at the transducer position
    with the signature tested by :attr:`is_no_detect`; they are storage
    artefacts, not soundings.
    """

    easting_cm: int
    northing_cm: int
    elevation_cm: int
    beam_angle_cdeg: int
    unassigned: tuple[int, ...]

    @property
    def easting_m(self) -> float:
        return self.easting_cm / 100.0

    @property
    def northing_m(self) -> float:
        return self.northing_cm / 100.0

    @property
    def elevation_m(self) -> float:
        return self.elevation_cm / 100.0

    @property
    def beam_angle_degrees(self) -> float:
        return self.beam_angle_cdeg / 100.0

    @property
    def is_no_detect(self) -> bool:
        """True for unfilled beam slots (zero return, sentinel words).

        Signature validated on every no-detect record in the S5 capture:
        the linear scalar is 0 and both the ladder and quality words are 1.
        """
        return (
            self.unassigned[_SND_LINEAR] == 0
            and self.unassigned[_SND_LOG] == 1
            and self.unassigned[_SND_QUALITY] == 1
        )


@dataclass(frozen=True)
class Hs2xSidescanHeader(Hs2xTimed):
    """Type 70 — sidescan ping header; the sample block follows as type 72.

    Payload: time_ms(i32), device(u16), port samples(u16), starboard
    samples(u16), u16, sound velocity(i32, cm/s), ping number(i32), four
    i32, easting(i32 cm), northing(i32 cm), heading(i32 mdeg). Anchor S5:
    sample counts × 4 bytes equal the following type-72 payload size;
    sound velocity and ping number continue the type-68 series; the grid
    pair and heading sit on the navigation track.
    """

    device: int
    port_samples: int
    starboard_samples: int
    sound_velocity_cm_s: int
    ping_number: int
    easting_cm: int
    northing_cm: int
    heading_millideg: int
    unassigned: tuple[int, ...]


@dataclass(frozen=True)
class Hs2xSidescanData(Record):
    """Type 72 — sidescan samples: little-endian u32 amplitudes, port
    samples first then starboard (counts from the preceding type-70
    header). Kept as raw bytes; :meth:`values` decodes on demand."""

    samples: bytes

    def values(self) -> tuple[int, ...]:
        count = len(self.samples) // 4
        return struct.unpack(f"<{count}I", self.samples[: count * 4])
