"""Typed records for the EdgeTech JSF dialect (see hydroformats/jsf.py).

A side scan sonar tows two fans of sound looking sideways: one to port,
one to starboard. Each ping, each side records the echo strength coming
back over time; laid out ping after ping those two intensity traces
paint a picture of the seafloor to either side of the track. A
dual-frequency system (such as the EdgeTech 6205) runs two side scan
pairs at once: a lower frequency that reaches farther, and a higher one
that resolves finer detail closer in. On top of that, a bathymetric
side scan measures the arrival angle of each echo across a receiver
array, turning every echo into a sounding: a slant range plus an angle
from straight down, which resolve into an across-track distance and a
depth below the sonar.

Every field layout here is hand-built from the EdgeTech interface
control document alone (anchor S9 in docs/FORMAT-SOURCES.md): *JSF File
and Message Descriptions*, EdgeTech document 0023492 Rev. R,
2025-12-22. Byte offsets quoted in docstrings are into each message's
payload, after the 16-byte message header; all fields are little
endian. Sizes quoted are pinned by ``test_docstring_layout_sizes``.

Vertical sign conventions are stated per field and follow the document:
heave is positive down, depth is positive down (below the surface),
altitude is positive up (off the seafloor), antenna height is positive
up, and the bathymetric z observable is positive down from the sonar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .records import Record

# Subsystem number assignments (ICD Table 2-1, byte 7). The subsystem
# says which sonar produced a message; for multi-frequency systems it
# also says which frequency. 0 is sub-bottom; 20/21/22 are the low,
# high and very high frequencies of a side scan; 40/41/42 the same for
# bathymetric data; 70/71/72 for motion tolerant bathymetric data;
# 100/101 raw and parsed serial passthrough; 120 gap filler.
_FREQUENCY_BANDS = {
    20: "low", 21: "high", 22: "very high",
    40: "low", 41: "high", 42: "very high",
    70: "low", 71: "high", 72: "very high",
}

_SIDES = {0: "port", 1: "starboard"}


def frequency_band(subsystem: int) -> str | None:
    """The frequency band a subsystem number names, or None.

    Per ICD Table 2-1: subsystems 20/40/70 carry low frequency data,
    21/41/71 high, 22/42/72 very high (side scan, bathymetric, and
    motion tolerant bathymetric respectively). Other subsystems
    (sub-bottom 0, serial 100/101, gap filler 120) have no band.
    """
    return _FREQUENCY_BANDS.get(subsystem)


def side_name(subsystem: int, channel: int) -> str | None:
    """"port" or "starboard" for a side scan or bathymetric channel.

    Per ICD Table 2-1, channel 0 is port and channel 1 starboard for
    side scan subsystems; the bathymetric message header states the
    same convention. For other subsystems the channel is not a side
    (serial ports use it as a logical port number), so this is None.
    """
    if subsystem in _FREQUENCY_BANDS:
        return _SIDES.get(channel)
    return None


def sounding_usable(flag: int) -> bool:
    """True when none of a bathymetric sample's invalid bits are set.

    ICD 2.5.1.4.7: bits 0-4 mark points the processing algorithm
    rejected (outlier, water column, weak amplitude, angle quality,
    SNR); bit 5 marks a null bin, which parks a false sounding at the
    sonar head if not excluded. Bits 6-7 are reserved and ignored here.
    """
    return not flag & 0x3F


@dataclass(frozen=True)
class JsfSonarTrace(Record):
    """Sonar Data Message (type 80): one ping of one channel's trace.

    This is the side scan picture itself. Each message carries a
    240-byte header (ICD Tables 2-2 through 2-10) and then the trace:
    the echo strengths one channel heard during one ping, as 16-bit
    integers in block floating point form. The sonar picks one shared
    exponent N per message so that 16 bits are enough for the whole
    trace; :meth:`scaled` multiplies every integer by ``2**-N``
    (Equation 2-2-1) to restore the physical values. ``trace`` keeps
    the integers exactly as stored.

    ``subsystem`` and ``channel`` come from the message header and say
    which fan this is: :attr:`frequency_band` names the frequency
    ("low" or "high" on a dual-frequency system) and :attr:`side`
    is "port" or "starboard". ``data_format`` says what one sample is:
    0 and 2 are one short per sample (envelope, and pre-matched-filter
    data), 1 and 9 are two shorts per sample (real and imaginary
    parts); values above 255 are EdgeTech proprietary formats whose
    samples this reader does not interpret, leaving ``trace`` None.

    Navigation and attitude riders decode per the tables: position is
    the raw pair plus ``coordinate_units`` (1 = X/Y millimeters, 2 =
    latitude/longitude in ten-thousandths of minutes of arc, 3 =
    decimeters, 4 = centimeters), resolved by the
    :attr:`longitude_degrees`/:attr:`latitude_degrees` and
    :attr:`x_m`/:attr:`y_m` properties; the ICD warns this is the last
    position received before pinging, not the sonar's position, unless
    validity bit 13 (interpolated) is set. ``heave_m`` is positive
    down, ``depth_m`` positive down, ``altitude_m`` positive up off the
    seafloor (zero means not filled). Pitch is positive bow up and roll
    positive port up, decoded from the format's 32768-counts-per-180-
    degrees convention. The 16-bit ``validity`` word says which rider
    fields are populated (bit 0 position, 1 course, 2 speed, 3 heading,
    4 pressure, 5 pitch/roll, 6 altitude, 7 heave, 8 water temperature,
    9 depth, 10 annotation, 11 cable counter, 12 KP, 13 position
    interpolated, 14 sound speed); absent values are zero by
    convention.

    Fields the ICD extends past 16 bits arrive here already extended:
    ``samples``, ``start_frequency_hz``, ``end_frequency_hz`` and
    ``mark_number`` fold in their four MSB extension bits, and
    ``course_degrees``, ``speed_knots`` and ``sweep_length_ms`` fold in
    their fractional LSB digits. The one fraction whose encoding the
    ICD does not state, the sample interval's, is carried verbatim in
    ``sample_interval_fraction_raw`` and never interpreted.
    """

    subsystem: int
    channel: int
    protocol_version: int
    time_sec: int
    milliseconds_today: int
    starting_depth_samples: int
    ping_number: int
    id_code: int
    validity: int
    data_format: int
    samples: int
    sample_interval_ns: int
    sample_interval_fraction_raw: int
    sample_frequency_hz: int
    start_frequency_hz: int
    end_frequency_hz: int
    sweep_length_ms: float
    gain_factor: int
    transmit_level_percent: int
    pulse_identifier: int
    pulses_in_water: int
    weighting_factor: int
    trace: tuple[int, ...] | None
    coordinate_units: int
    longitude_raw: int
    latitude_raw: int
    kilometers_of_pipe: float
    heave_m: float
    gap_filler_offset_m: float
    annotation: str
    pressure_psi: float
    depth_m: float
    altitude_m: float
    sound_speed_mps: float
    mixer_hz: float
    cpu_time: tuple[int, int, int, int, int]
    time_basis: int
    heading_degrees: float
    pitch_degrees: float
    roll_degrees: float
    trigger_source: int
    mark_number: int
    fix_time: tuple[int, int, int, int, int]
    course_degrees: float
    speed_knots: float
    max_adc: int
    software_version: str
    spherical_correction_raw: int
    packet_number: int
    adc_decimation: float
    water_temperature_c: float
    layback_m: float
    cable_out_m: float
    antenna_to_tow_aft_m: float
    antenna_to_tow_starboard_m: float

    @property
    def time(self) -> float:
        """Epoch seconds of the ping, to the millisecond: the seconds
        word plus the sub-second part of milliseconds-since-midnight,
        combined per the ICD's own guidance on Table 2-10."""
        return self.time_sec + self.milliseconds_today % 1000 / 1000.0

    @property
    def frequency_band(self) -> str | None:
        return frequency_band(self.subsystem)

    @property
    def side(self) -> str | None:
        return side_name(self.subsystem, self.channel)

    @property
    def shorts_per_sample(self) -> int | None:
        """Integers per sample for the data format: 1 for envelope and
        raw (0, 2), 2 for analytic real/imaginary pairs (1, 9), None
        for proprietary formats."""
        if self.data_format in (0, 2):
            return 1
        if self.data_format in (1, 9):
            return 2
        return None

    @property
    def complete(self) -> bool:
        """True when the trace holds exactly the declared sample count
        (times two for analytic data)."""
        per = self.shorts_per_sample
        return (per is not None and self.trace is not None
                and len(self.trace) == self.samples * per)

    def scaled(self) -> tuple[float, ...] | None:
        """The trace restored to physical scale: every stored integer
        times ``2**-N`` with N the weighting factor (Equation 2-2-1).
        N may be negative, which scales the integers up. None when the
        data format is one this reader does not interpret."""
        if self.trace is None:
            return None
        factor = 2.0 ** -self.weighting_factor
        return tuple(value * factor for value in self.trace)

    @property
    def longitude_degrees(self) -> float | None:
        """Longitude, east positive, when the coordinate units are
        geographic (2: ten-thousandths of minutes of arc); else None."""
        if self.coordinate_units == 2:
            return self.longitude_raw / 10_000.0 / 60.0
        return None

    @property
    def latitude_degrees(self) -> float | None:
        """Latitude, north positive, when the coordinate units are
        geographic; else None."""
        if self.coordinate_units == 2:
            return self.latitude_raw / 10_000.0 / 60.0
        return None

    @property
    def x_m(self) -> float | None:
        """Grid X in meters when the coordinate units are metric
        (1 millimeters, 3 decimeters, 4 centimeters); else None."""
        scale = {1: 1000.0, 3: 10.0, 4: 100.0}.get(self.coordinate_units)
        return self.longitude_raw / scale if scale else None

    @property
    def y_m(self) -> float | None:
        """Grid Y in meters when the coordinate units are metric."""
        scale = {1: 1000.0, 3: 10.0, 4: 100.0}.get(self.coordinate_units)
        return self.latitude_raw / scale if scale else None


@dataclass(frozen=True)
class JsfBathyPing(Record):
    """Bathymetric Data Message (type 3000): one ping of one side's
    soundings as raw observables.

    A bathymetric side scan does not just record how strong each echo
    was, it also measures the angle each echo arrived from, using the
    phase differences across a small vertical receiver array. Each
    sample here is therefore a sounding candidate: a time delay (when
    the echo arrived) and an angle from nadir (straight down), plus an
    amplitude and quality measures for cleaning. Range needs the local
    speed of sound, which lives in the type 3002 pressure message, so
    the geometric accessors take it as an argument and the raw
    observables stay unreduced, exactly as this library keeps GSF
    travel times and beam angles.

    The 80-byte header (ICD Table 2-28) carries the ping's shared
    values: the sampling setup, the two scale factors that turn the
    16-bit sample words into seconds and degrees, the TVG the sonar
    applied during collection (``tvg_db_per_100m``, byte 70; it applies
    to these bathymetry records, not to the side scan traces), the
    binning setup (0 raw interferometric, 1 equidistant bins, 2
    equiangular bins; ``span`` and ``bin_size`` are then meters or
    degrees accordingly) and the format revision. Sample sets are
    decoded for revision 4 and higher, the only layout the ICD details
    (Table 2-29, 8 bytes each); older revisions keep the header and
    leave the arrays None. A short sample block decodes as far as it
    goes; ``num_samples`` is the declared count and :attr:`complete`
    compares.

    Raw arrays and their units: ``time_delays`` are unsigned counts of
    ``time_scale_factor_sec``; ``angles`` are signed counts of
    ``angle_scale_factor_degrees`` (the ICD prints the scale factor's
    size as UINT32 but it is read here as the 4-byte float its
    neighbors use: its unit is degrees and Equation 2-5 multiplies it
    directly onto a 16-bit count, which an integer number of degrees
    could not scale, see anchor S9); ``amplitudes`` count 0.5 dB steps
    (0 to 127.5 dB, ICD 2.5.1.4.5); ``angle_uncertainties`` count
    0.02 degree steps at the 2-sigma level, clamped at 5.1 degrees
    (2.5.1.4.6); ``snr_db`` is already in whole dB (0-31, 2.5.1.4.8);
    ``qualities`` are the 3-bit interstave agreement codes, 0 (below
    50 percent) to 7 (90 percent and up, 2.5.1.4.9); ``flags`` are the
    cleaning bits (see :func:`sounding_usable`).

    The along-track position of these soundings is not an observable of
    this message: the ICD's geometry (Equations 2-7 and 2-8) is purely
    athwartships, x across track and z straight down from the sonar.
    """

    subsystem: int
    channel: int
    time_sec: int
    time_nsec: int
    ping_number: int
    num_samples: int
    algorithm_type: int
    num_pulses: int
    pulse_phase: int
    pulse_length_usec: int
    transmit_pulse_amplitude: float
    chirp_start_hz: float
    chirp_end_hz: float
    mixer_hz: float
    sample_rate_hz: float
    offset_to_first_sample_ns: int
    time_delay_uncertainty_sec: float
    time_scale_factor_sec: float
    time_scale_accuracy_percent: float
    angle_scale_factor_degrees: float
    time_to_first_bottom_ns: int
    format_revision: int
    binning_flag: int
    tvg_db_per_100m: int
    span: float
    bin_size: float
    time_delays: tuple[int, ...] | None
    angles: tuple[int, ...] | None
    amplitudes: tuple[int, ...] | None
    angle_uncertainties: tuple[int, ...] | None
    flags: tuple[int, ...] | None
    snr_db: tuple[int, ...] | None
    qualities: tuple[int, ...] | None

    @property
    def time(self) -> float:
        """Epoch seconds of the ping, UTC."""
        return self.time_sec + self.time_nsec / 1e9

    @property
    def frequency_band(self) -> str | None:
        return frequency_band(self.subsystem)

    @property
    def side(self) -> str | None:
        """"port" or "starboard" from the message's own channel byte
        (0 port, 1 starboard per Table 2-28)."""
        return _SIDES.get(self.channel)

    @property
    def complete(self) -> bool:
        """True when the decoded arrays hold the declared count."""
        return (self.time_delays is not None
                and len(self.time_delays) == self.num_samples)

    @property
    def amplitudes_db(self) -> tuple[float, ...] | None:
        """Amplitudes in dB: 0.5 dB per count (ICD 2.5.1.4.5)."""
        if self.amplitudes is None:
            return None
        return tuple(value * 0.5 for value in self.amplitudes)

    @property
    def angle_uncertainties_degrees(self) -> tuple[float, ...] | None:
        """2-sigma angle uncertainties: 0.02 degrees per count
        (ICD 2.5.1.4.6)."""
        if self.angle_uncertainties is None:
            return None
        return tuple(value * 0.02 for value in self.angle_uncertainties)

    @property
    def echo_times_sec(self) -> tuple[float, ...] | None:
        """Two-way echo time per sample (Equation 2-2): the offset to
        the first sample plus the delay count times the time scale."""
        if self.time_delays is None:
            return None
        offset = self.offset_to_first_sample_ns / 1e9
        scale = self.time_scale_factor_sec
        return tuple(offset + delay * scale for delay in self.time_delays)

    @property
    def angles_from_nadir_degrees(self) -> tuple[float, ...] | None:
        """Signed angles from nadir in a single frame (Equation 2-5):
        negative to port, positive to starboard, so the port channel's
        angles are negated. Use the angle as signed, per the ICD's own
        caution."""
        if self.angles is None:
            return None
        sign = (-1.0) ** (self.channel + 1)
        scale = self.angle_scale_factor_degrees
        return tuple(sign * angle * scale for angle in self.angles)

    def slant_ranges_m(self, sound_speed_mps: float) -> tuple[float, ...] | None:
        """Slant range to each echo in meters (Equation 2-3): half the
        sound speed times the two-way echo time. The sound speed should
        come from the type 3002 message, per the ICD."""
        times = self.echo_times_sec
        if times is None:
            return None
        return tuple(sound_speed_mps / 2.0 * t for t in times)

    def soundings_xz_m(
            self, sound_speed_mps: float) -> tuple[tuple[float, float], ...] | None:
        """Raw (x, z) per sample in meters (Equations 2-7 and 2-8):
        x across track (negative to port, positive to starboard) and z
        straight down from the sonar (positive down). This is geometry
        before any motion correction; combine with attitude, position
        and the sonar's own depth to place soundings in the world."""
        ranges = self.slant_ranges_m(sound_speed_mps)
        angles = self.angles_from_nadir_degrees
        if ranges is None or angles is None:
            return None
        return tuple(
            (slant * math.sin(math.radians(angle)),
             slant * math.cos(math.radians(angle)))
            for slant, angle in zip(ranges, angles, strict=True)
        )

    def nadir_depth_m(self, sound_speed_mps: float) -> float:
        """Depth below the sounder in meters (Equation 2-6), from the
        built-in single-beam mode's time to first bottom return."""
        return sound_speed_mps / 2.0 * self.time_to_first_bottom_ns / 1e9


@dataclass(frozen=True)
class JsfAttitude(Record):
    """AttitudeMessageType (3001): one roll/pitch/heave/heading reading.

    32 bytes (ICD Table 2-30). Signs per the EdgeTech convention: roll
    positive port up, pitch positive bow up, heave positive down, yaw
    positive to starboard. The valid flags say which fields the sensor
    actually supplied (bit 0 heading, 1 heave, 2 pitch, 3 roll, 4 yaw);
    unsupplied fields are zero.
    """

    time_sec: int
    time_nsec: int
    valid_flags: int
    heading_degrees: float
    heave_m: float
    pitch_degrees: float
    roll_degrees: float
    yaw_degrees: float

    @property
    def time(self) -> float:
        return self.time_sec + self.time_nsec / 1e9

    @property
    def heading_valid(self) -> bool:
        return bool(self.valid_flags & 0x01)

    @property
    def heave_valid(self) -> bool:
        return bool(self.valid_flags & 0x02)

    @property
    def pitch_valid(self) -> bool:
        return bool(self.valid_flags & 0x04)

    @property
    def roll_valid(self) -> bool:
        return bool(self.valid_flags & 0x08)

    @property
    def yaw_valid(self) -> bool:
        return bool(self.valid_flags & 0x10)


@dataclass(frozen=True)
class JsfBathyPressure(Record):
    """PressureMessageType (3002): sound speed at the sonar head, with
    pressure, temperature, salinity, conductivity and depth when fitted.

    36 bytes (ICD Table 2-31). This message's sound speed is the one
    the ICD says to use for the type 3000 slant range calculation.
    ``depth_m`` is positive down below the water surface and only valid
    on subsea platforms. The valid flag's bits are read in field order
    (bit 0 pressure through bit 5 depth), the order both of the ICD's
    fully enumerated validity tables follow; the table for this message
    defers to the 3001 description without listing bits (anchor S9
    judgment). The conductivity unit is carried as stored: the table
    prints "Degrees", while the equivalent 2060 field is defined in
    micro-siemens per centimeter.
    """

    time_sec: int
    time_nsec: int
    valid_flags: int
    pressure_psi: float
    water_temperature_c: float
    salinity_ppm: float
    conductivity: float
    sound_speed_mps: float
    depth_m: float

    @property
    def time(self) -> float:
        return self.time_sec + self.time_nsec / 1e9


@dataclass(frozen=True)
class JsfAltitude(Record):
    """AltitudeMessageType (3003): height off the seafloor, with speed
    and heading when a source supplies them.

    24 bytes (ICD Table 2-32). ``altitude_m`` is positive up off the
    seafloor, computed from the built-in single-beam mode; the ICD says
    to add it to the 3002 depth to get total water depth. Valid flag
    bits: 0 altitude, 1 speed, 2 heading.
    """

    time_sec: int
    time_nsec: int
    valid_flags: int
    altitude_m: float
    speed_knots: float
    heading_degrees: float

    @property
    def time(self) -> float:
        return self.time_sec + self.time_nsec / 1e9


@dataclass(frozen=True)
class JsfPosition(Record):
    """PositionMessageType (3004): geographic position, with speed,
    heading and antenna height when supplied.

    56 bytes (ICD Table 2-33). Latitude is positive north, longitude
    positive east, both double precision degrees; antenna (ellipsoid)
    height is positive up. The UTM zone/easting/northing fields exist
    in the layout but the ICD notes they are not typically used. Valid
    flag bits: 0 UTM zone, 1 easting, 2 northing, 3 latitude,
    4 longitude, 5 speed, 6 heading, 7 antenna height.
    """

    time_sec: int
    time_nsec: int
    valid_flags: int
    utm_zone: int
    easting_m: float
    northing_m: float
    latitude_degrees: float
    longitude_degrees: float
    speed_knots: float
    heading_degrees: float
    antenna_height_m: float

    @property
    def time(self) -> float:
        return self.time_sec + self.time_nsec / 1e9


@dataclass(frozen=True)
class JsfNmea(Record):
    """NMEA String (2002): one sentence from a GPS, gyro or other
    serial device, timestamped at receipt.

    12-byte fixed part (ICD Table 2-19) then the sentence text, decoded
    latin-1 (never raises) with trailing CR/LF/NUL stripped. ``source``
    is 1 sonar, 2 Discover, 3 ETSI.
    """

    time_sec: int
    milliseconds: int
    source: int
    text: str

    @property
    def time(self) -> float:
        return self.time_sec + self.milliseconds / 1000.0


@dataclass(frozen=True)
class JsfPitchRoll(Record):
    """Pitch Roll Data (2020): one motion sensor reading.

    44 bytes (ICD Table 2-20). Decodes to engineering units per the
    table's own multipliers: accelerations by 30/32768 G per count,
    rate gyros by 750/32768 degrees per second per count, pitch and
    roll by 180/32768 degrees per count (pitch positive bow up, roll
    positive port up). ``heave_m`` is positive down (from millimeters).
    The valid flags word says which fields the device populated (bit 0
    Ax, 1 Ay, 2 Az, 3 Rx, 4 Ry, 5 Rz, 6 pitch, 7 roll, 8 heave,
    9 heading, 10 temperature, 11 device info, 12 yaw).
    """

    time_sec: int
    milliseconds: int
    acceleration_g: tuple[float, float, float]
    rate_dps: tuple[float, float, float]
    pitch_degrees: float
    roll_degrees: float
    temperature_c: float
    device_info: int
    heave_m: float
    heading_degrees: float
    valid_flags: int
    yaw_degrees: float

    @property
    def time(self) -> float:
        return self.time_sec + self.milliseconds / 1000.0


@dataclass(frozen=True)
class JsfPressureReading(Record):
    """Pressure Sensor Reading (2060): one CTD-style sensor reading.

    40-byte fixed part plus 36 reserved bytes (ICD Table 2-21).
    Pressure decodes from thousandths of a PSI (absolute by default),
    temperature from thousandths of a degree Celsius, sound speed from
    millimeters per second. ``salinity_ppm``,
    ``conductivity_usiemens_per_cm`` and ``depth_m`` (whole meters,
    positive down) are stored as integers and stay integers. Valid flag
    bits: 0 pressure, 1 temperature, 2 salinity, 3 conductivity,
    4 sound velocity, 5 depth.
    """

    time_sec: int
    milliseconds: int
    pressure_psi: float
    temperature_c: float
    salinity_ppm: int
    valid_flags: int
    conductivity_usiemens_per_cm: int
    sound_speed_mps: float
    depth_m: int

    @property
    def time(self) -> float:
        return self.time_sec + self.milliseconds / 1000.0


@dataclass(frozen=True)
class JsfSystemInfo(Record):
    """System Information (182): what recorded the file.

    24 decoded bytes (ICD Table 2-17); the ICD says the message may
    grow, so any additional bytes are tolerated and ignored. Normally
    present at the start of a file and repeated if the configuration
    changes.
    """

    system_type: int
    low_rate_io: int
    software_version: int
    num_subsystems: int
    num_serial_devices: int
    tow_vehicle_serial: int
