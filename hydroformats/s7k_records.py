"""Typed records for the Teledyne RESON s7k dialect (see hydroformats/s7k.py).

A multibeam echosounder such as the SeaBat sends out one fan of sound
(a ping) and listens on hundreds of receive beams at once, each aimed a
little further to the side. For every beam the sonar finds the moment
the seafloor echo arrives (the bottom detection): a sample number on
the sonar's clock plus the beam's steering angle. Those two raw
observables, echo time and angle, are what a survey reduces into a
depth and an across-track position, once sound speed and the vessel's
motion are applied. Alongside the detection, the sonar can keep a short
window of the echo's intensity samples around the bottom (a snippet),
which is the raw material for seabed backscatter: how strongly the
bottom reflects, a clue to what it is made of.

Every field layout here is hand-built from the format owner's data
format definition alone (anchor S12 in docs/FORMAT-SOURCES.md):
*7k Data Format*, Teledyne RESON Data Format Definition, Version 3.10,
April 3, 2019. Table numbers quoted in docstrings are that document's.
Byte offsets are into each record's data section (Record Type Header
plus Record Data), after the 64-byte Data Record Frame; all fields are
little endian (DFD section 2.4).

Units and sign conventions follow the DFD (Table 2): distances are
meters, angles radians, and time tags UTC. The vertical axis points
up: heave is positive up, depth and height values are positive up, and
a bottom below the vessel reference point is negative. Roll is positive
port up; pitch is positive bow up. Receive beam angles are negative to
port and positive to starboard, zero at the vertical (Figure 10-1).
Fields named ``*_rad`` are radians exactly as stored; the common ones
also expose ``*_degrees`` conveniences.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .records import Record


@dataclass(frozen=True)
class S7kRecord(Record):
    """Base of every s7k record: the Data Record Frame context.

    Each record rides in a frame that names the device that produced it
    (``device_identifier``, e.g. 7125 for a SeaBat 7125), an enumerator
    telling twin devices apart (dual-head or dual-frequency
    installations), and the 7KTIME tag: UTC year, day of year (1-366),
    and seconds plus hours and minutes (DFD Tables 3 and 5). The frame
    stamps when the data was produced; for ping records that is when
    the transmitter finished the ping (DFD section 8).
    """

    device_identifier: int
    system_enumerator: int
    year: int
    day: int
    seconds: float
    hours: int
    minutes: int

    @property
    def time(self) -> float | None:
        """Seconds since the epoch, UTC; None when the frame carried no
        time (all 7KTIME fields zero, per the DFD's note)."""
        if self.year == 0 and self.day == 0:
            return None
        moment = datetime(self.year, 1, 1, tzinfo=timezone.utc) + timedelta(
            days=self.day - 1, hours=self.hours, minutes=self.minutes,
            seconds=self.seconds)
        return moment.timestamp()


@dataclass(frozen=True)
class S7kPosition(S7kRecord):
    """Record 1003: one position fix (Table 14, 37 bytes).

    The same three doubles carry either geographic coordinates
    (latitude and longitude in radians on the datum, WGS84 when
    ``datum_identifier`` is zero) or grid coordinates (northing and
    easting in meters), told apart by ``position_type`` (0 geographic,
    1 grid). The typed properties resolve the pair and are None for the
    other kind. ``height_m`` is relative to the datum, positive up.
    ``latency_sec`` is the sensor latency in seconds. The trailing
    satellite count is the one field Table 14 marks optional; real logs
    omit it, and it is None when the record ends before it.
    """

    datum_identifier: int
    latency_sec: float
    latitude_or_northing: float
    longitude_or_easting: float
    height_m: float
    position_type: int
    utm_zone: int
    quality_flag: int
    positioning_method: int
    number_of_satellites: int | None = None

    @property
    def latitude_degrees(self) -> float | None:
        if self.position_type != 0:
            return None
        return math.degrees(self.latitude_or_northing)

    @property
    def longitude_degrees(self) -> float | None:
        if self.position_type != 0:
            return None
        return math.degrees(self.longitude_or_easting)

    @property
    def northing_m(self) -> float | None:
        return self.latitude_or_northing if self.position_type == 1 else None

    @property
    def easting_m(self) -> float | None:
        return self.longitude_or_easting if self.position_type == 1 else None


@dataclass(frozen=True)
class S7kCtd(S7kRecord):
    """Record 1010: a CTD cast or sound velocity profile (Tables 24-25).

    Sound moves faster or slower depending on the water's temperature,
    salinity and pressure, so a profile of sound speed against depth is
    what turns echo times into honest ranges. Each sample carries five
    floats; which physical quantity two of them hold is switched by the
    header flags: ``conductivity_flag`` picks conductivity (S/m) or
    salinity (ppt) and ``pressure_flag`` picks pressure (Pascal) or
    depth (meters). ``sample_validity`` is a bit field (bit 0
    conductivity/salinity, 1 temperature, 2 pressure/depth, 3 sound
    velocity, 4 absorption). Position fields are radians on WGS84,
    meaningful when ``position_flag`` is 1.
    """

    frequency_hz: float
    sound_velocity_source: int
    sound_velocity_algorithm: int
    conductivity_flag: int
    pressure_flag: int
    position_flag: int
    sample_validity: int
    latitude_rad: float
    longitude_rad: float
    sample_rate_hz: float
    conductivity_salinity: tuple[float, ...]
    temperature_c: tuple[float, ...]
    pressure_depth: tuple[float, ...]
    sound_velocity_mps: tuple[float, ...]
    absorption_db_per_km: tuple[float, ...]

    @property
    def num_samples(self) -> int:
        return len(self.sound_velocity_mps)


@dataclass(frozen=True)
class S7kGeodesy(S7kRecord):
    """Record 1011: the geodesy in force for navigational data
    (Table 26, 320 bytes): spheroid, datum shift and grid definitions.
    Text fields are stripped of trailing NULs; reserved blocks are not
    carried. Rotations are radians, shifts meters; ``grid_distance_units``
    and ``grid_angular_units`` are the DFD's enumerations (0 meters /
    0 radians)."""

    spheroid: str
    semi_major_axis_m: float
    inverse_flattening: float
    datum: str
    calculation_method: int
    number_of_parameters: int
    dx_m: float
    dy_m: float
    dz_m: float
    rx_rad: float
    ry_rad: float
    rz_rad: float
    scale: float
    grid_name: str
    grid_distance_units: int
    grid_angular_units: int
    latitude_of_origin: float
    central_meridian: float
    false_easting_m: float
    false_northing_m: float
    central_scale_factor: float
    custom_identifier: int


@dataclass(frozen=True)
class S7kRollPitchHeave(S7kRecord):
    """Record 1012: vessel motion (Table 27, 12 bytes). Roll positive
    port up, pitch positive bow up, heave positive up, all per DFD
    Table 2; angles radians, heave meters."""

    roll_rad: float
    pitch_rad: float
    heave_m: float

    @property
    def roll_degrees(self) -> float:
        return math.degrees(self.roll_rad)

    @property
    def pitch_degrees(self) -> float:
        return math.degrees(self.pitch_rad)


@dataclass(frozen=True)
class S7kHeading(S7kRecord):
    """Record 1013: vessel heading in radians (Table 28, 4 bytes),
    always positive per DFD Table 2."""

    heading_rad: float

    @property
    def heading_degrees(self) -> float:
        return math.degrees(self.heading_rad)


@dataclass(frozen=True)
class S7kSonarSettings(S7kRecord):
    """Record 7000: the sonar settings in force for one ping
    (Table 39, 156 bytes), updated every ping.

    These are the knobs that shape the acoustics: transmit frequency
    and pulse, the sample rate that turns detection sample numbers into
    time, range/power/gain selections, projector and receive beam
    shaping, the bottom detection gates, and the absorption, sound
    velocity and spreading values applied on board. Kept first-class so
    every raw observable in the ping records can be re-reduced
    downstream. Angles are radians; the projector steering angle is the
    pitch stabilization steer (the same value as the 7027 record's tx
    angle). ``control_flags`` bit 15 set means the system was active.
    """

    sonar_id: int
    ping_number: int
    multiping_sequence: int
    frequency_hz: float
    sample_rate_hz: float
    receiver_bandwidth_hz: float
    tx_pulse_width_sec: float
    tx_pulse_type: int
    tx_pulse_envelope: int
    tx_pulse_envelope_parameter: float
    tx_pulse_mode: int
    max_ping_rate_hz: float
    ping_period_sec: float
    range_selection_m: float
    power_selection_db: float
    gain_selection_db: float
    control_flags: int
    projector_identifier: int
    projector_steering_vertical_rad: float
    projector_steering_horizontal_rad: float
    projector_beam_width_vertical_rad: float
    projector_beam_width_horizontal_rad: float
    projector_focal_point_m: float
    projector_weighting_window: int
    projector_weighting_parameter: float
    transmit_flags: int
    hydrophone_identifier: int
    receive_weighting_window: int
    receive_weighting_parameter: float
    receive_flags: int
    receive_beam_width_rad: float
    min_range_m: float
    max_range_m: float
    min_depth_m: float
    max_depth_m: float
    absorption_db_per_km: float
    sound_velocity_mps: float
    spreading_loss_db: float

    @property
    def active(self) -> bool:
        """True when control flag bit 15 (system active) is set."""
        return bool(self.control_flags & 0x8000)


@dataclass(frozen=True)
class S7kBeamGeometry(S7kRecord):
    """Record 7004: receive beam pointing and widths (Tables 44-45).

    Four (or five) arrays of one float per receive beam, radians.
    The horizontal direction angle is the across-track steering angle,
    negative to port and positive to starboard of the vertical
    (Figure 10-1), typically spanning about -75 to +75 degrees; the
    vertical direction angle is the along-track steer, normally zero.
    Beam widths are measured at the -3 dB points, Y along track and X
    across track. ``tx_delays`` (fractional samples) exists only on
    sonar models that steer per-beam transmit delays; the DFD says to
    detect its presence from the record length, and this reader does
    (None when absent).
    """

    sonar_id: int
    vertical_angles_rad: tuple[float, ...]
    horizontal_angles_rad: tuple[float, ...]
    beam_width_y_rad: tuple[float, ...]
    beam_width_x_rad: tuple[float, ...]
    tx_delays: tuple[float, ...] | None = None

    @property
    def num_beams(self) -> int:
        return len(self.horizontal_angles_rad)

    @property
    def horizontal_angles_degrees(self) -> tuple[float, ...]:
        return tuple(math.degrees(a) for a in self.horizontal_angles_rad)


@dataclass(frozen=True)
class S7kBathymetry(S7kRecord):
    """Record 7006: per-beam bottom detection results (Tables 46-47).

    The DFD marks 7006 as superseded by 7027 and kept for backwards
    compatibility; older logs still carry it. ``travel_times_sec`` is
    the two-way travel time per beam (the DFD names the array "Range"
    but defines it in seconds); ``qualities`` is the raw quality bit
    field per beam (bit 0 brightness passed, bit 1 colinearity passed,
    bits 2-3 which detection processes were used); ``intensities`` is
    relative, uncalibrated bottom reflectivity (and the DFD warns that
    some 7150 versions store quality information here instead). The
    two-way travel time filter gates ride along when the record carries
    them (older layouts do not; then None). ``flags`` bit 0 is layer
    compensation, bit 1 XYZ compensation; ``sound_velocity_manual`` is
    1 when the sound velocity was entered by hand rather than measured.
    """

    sonar_id: int
    ping_number: int
    multiping_sequence: int
    flags: int
    sound_velocity_manual: int
    sound_velocity_mps: float
    travel_times_sec: tuple[float, ...]
    qualities: tuple[int, ...]
    intensities: tuple[float, ...]
    min_filter_sec: tuple[float, ...] | None = None
    max_filter_sec: tuple[float, ...] | None = None

    @property
    def num_beams(self) -> int:
        return len(self.travel_times_sec)


@dataclass(frozen=True)
class S7kRawDetections(S7kRecord):
    """Record 7027: raw, non-compensated bottom detections, the
    modern replacement for 7006 (Tables 71-73).

    Every detection is one echo the sonar decided is the bottom: a
    beam number, a fractional sample number on the sonar clock (zero at
    transmit; divide by ``sampling_rate_hz`` for the two-way travel
    time, per the DFD's Appendix F), and the receive steering angle at
    the detection in the sonar reference frame, radians, negative to
    port. With multi-detect a beam may contribute several detections,
    so the count is not the beam count. These are the raw observables a
    bathymetric reduction starts from, kept exactly as stored.

    Per-detection fields beyond the base five exist only when the
    record's declared detection block size covers them; the DFD grows
    this record by appending fields, so older logs surface None for
    ``uncertainties`` (an error normalized to the detection point),
    ``intensities`` (relative), and the two gate limits (in samples).
    ``detection_flags`` per detection: bit 0 magnitude-based detection,
    bit 1 phase-based, bits 2-8 the quality type interpreting
    ``qualities`` (type 1: bit 0 brightness passed, bit 1 colinearity
    passed), bits 9-12 the multi-detect priority. ``tx_angle_rad`` is
    the applied transmit steer (pitch stabilization);
    ``applied_roll_rad`` is the roll applied to the gates, zero when
    roll stabilization is on.
    """

    sonar_id: int
    ping_number: int
    multiping_sequence: int
    detection_algorithm: int
    flags: int
    sampling_rate_hz: float
    tx_angle_rad: float
    applied_roll_rad: float
    detection_size: int
    beam_numbers: tuple[int, ...]
    detection_points: tuple[float, ...]
    rx_angles_rad: tuple[float, ...]
    detection_flags: tuple[int, ...]
    qualities: tuple[int, ...]
    uncertainties: tuple[float, ...] | None = None
    intensities: tuple[float, ...] | None = None
    min_limits: tuple[float, ...] | None = None
    max_limits: tuple[float, ...] | None = None

    @property
    def num_detections(self) -> int:
        return len(self.beam_numbers)

    @property
    def two_way_travel_times_sec(self) -> tuple[float, ...]:
        """Two-way travel time per detection: the detection point
        divided by the sampling rate (DFD Appendix F)."""
        return tuple(point / self.sampling_rate_hz
                     for point in self.detection_points)

    @property
    def rx_angles_degrees(self) -> tuple[float, ...]:
        return tuple(math.degrees(a) for a in self.rx_angles_rad)


@dataclass(frozen=True)
class S7kSnippets(S7kRecord):
    """Record 7028: snippet imagery, a window of raw intensity samples
    around each beam's bottom detection (Tables 74-75).

    Per detection: the beam number, the first and last sample numbers
    included, the detection sample between them, and then the intensity
    series itself, ``snippet_ends[i] - snippet_starts[i] + 1`` integers
    (16-bit, or 32-bit when ``flags`` bit 0 is set), stored raw. A
    window whose start exceeds its end holds no data for that beam (the
    convention the DFD states for the 7058 record; applied here too,
    see the module judgment notes in hydroformats/s7k.py). A nonzero
    ``error_flag`` means the sonar produced no snippet data for the
    ping (6 = bottom detection failed) and every tuple is empty.
    """

    sonar_id: int
    ping_number: int
    multiping_sequence: int
    error_flag: int
    control_flags: int
    flags: int
    beam_numbers: tuple[int, ...]
    snippet_starts: tuple[int, ...]
    detection_samples: tuple[int, ...]
    snippet_ends: tuple[int, ...]
    snippets: tuple[tuple[int, ...], ...]

    @property
    def num_detections(self) -> int:
        return len(self.beam_numbers)

    @property
    def is_32_bit(self) -> bool:
        """True when flags bit 0 declares 32-bit snippet samples."""
        return bool(self.flags & 1)


@dataclass(frozen=True)
class S7kSnippetBackscatter(S7kRecord):
    """Record 7058: snippets reduced to backscattering strength
    (Tables 100-101), available when the sonar's normalized
    backscatter license is enabled.

    Same shape as 7028, but each sample is a float: backscattering
    strength BS = 10 * log10(sigma) in dB, compensated on board for
    the sonar system, propagation losses and footprint. A window whose
    begin sample exceeds its end holds no data for that beam (the
    length rule is end - begin + 1, stated by the DFD). When
    ``control_flags`` bit 6 is set, a footprint area series (square
    meters, one per sample) follows for every detection. A nonzero
    ``error_flag`` names why calibration was not possible (per the
    DFD's table; the record then carries the original uncalibrated
    data). ``absorption_db_per_km`` is meaningful when control flag
    bit 8 (single absorption value) is set.
    """

    sonar_id: int
    ping_number: int
    multiping_sequence: int
    error_flag: int
    control_flags: int
    absorption_db_per_km: float
    beam_numbers: tuple[int, ...]
    begin_samples: tuple[int, ...]
    detection_samples: tuple[int, ...]
    end_samples: tuple[int, ...]
    backscatter_db: tuple[tuple[float, ...], ...]
    footprints_m2: tuple[tuple[float, ...], ...] | None = None

    @property
    def num_detections(self) -> int:
        return len(self.beam_numbers)

    @property
    def calibrated(self) -> bool:
        return self.error_flag == 0


@dataclass(frozen=True)
class S7kSoundVelocity(S7kRecord):
    """Record 7610: the surface sound velocity in force, meters per
    second (Table 117). Temperature (Kelvin) and pressure (Pascal) ride
    along on newer writers; the DFD says the pressure field is absent
    on older ones and zero means not valid, and this reader detects
    both trailing fields from the record length (None when absent)."""

    sound_velocity_mps: float
    temperature_k: float | None = None
    pressure_pa: float | None = None


@dataclass(frozen=True)
class S7kBeamformedHeader(S7kRecord):
    """Record 7018 header only: full water column magnitude and phase
    data (Table 63). The sample matrix (beams x samples of u16 + i16)
    is deliberately not materialized: water column payloads dwarf
    everything else in a file. The header says what was there."""

    sonar_id: int
    ping_number: int
    multiping_sequence: int
    beams: int
    samples: int


@dataclass(frozen=True)
class S7kCompressedWaterColumnHeader(S7kRecord):
    """Record 7042 header only: compressed water column data
    (Table 82). As with 7018, the per-beam sample payload is skipped;
    the header carries the compression flags, the first sample number
    and the effective sample rate after downsampling."""

    sonar_id: int
    ping_number: int
    multiping_sequence: int
    beams: int
    samples: int
    compressed_samples: int
    flags: int
    first_sample: int
    sample_rate_hz: float
    compression_factor: float


@dataclass(frozen=True)
class S7kRemoteSonarSettings(S7kRecord):
    """Record 7503: the remote control settings snapshot (Table 113),
    sent with every ping (ping number zero marks a current-settings
    reply rather than a ping).

    The leading fields mirror record 7000 (same knobs, same units);
    past the spreading loss the record appends installation geometry
    (the transmit array offset from the receive array, meters, X
    starboard / Y forward / Z up; head tilt radians), the ping state,
    beam spacing and coverage controls. The DFD grows this record by
    appending fields, so everything after the 7000-equivalent core is
    None when a shorter vintage of the record ends before it.
    """

    sonar_id: int
    ping_number: int
    frequency_hz: float
    sample_rate_hz: float
    receiver_bandwidth_hz: float
    tx_pulse_width_sec: float
    tx_pulse_type: int
    tx_pulse_envelope: int
    tx_pulse_envelope_parameter: float
    tx_pulse_mode: int
    max_ping_rate_hz: float
    ping_period_sec: float
    range_selection_m: float
    power_selection_db: float
    gain_selection_db: float
    control_flags: int
    projector_identifier: int
    projector_steering_vertical_rad: float
    projector_steering_horizontal_rad: float
    projector_beam_width_vertical_rad: float
    projector_beam_width_horizontal_rad: float
    projector_focal_point_m: float
    projector_weighting_window: int
    projector_weighting_parameter: float
    transmit_flags: int
    hydrophone_identifier: int
    receive_weighting_window: int
    receive_weighting_parameter: float
    receive_flags: int
    min_range_m: float
    max_range_m: float
    min_depth_m: float
    max_depth_m: float
    absorption_db_per_km: float
    sound_velocity_mps: float
    spreading_loss_db: float
    vernier_operation_mode: int | None = None
    automatic_filter_window: int | None = None
    tx_offset_x_m: float | None = None
    tx_offset_y_m: float | None = None
    tx_offset_z_m: float | None = None
    head_tilt_x_rad: float | None = None
    head_tilt_y_rad: float | None = None
    head_tilt_z_rad: float | None = None
    ping_state: int | None = None
    beam_spacing_mode: int | None = None
    sonar_source_mode: int | None = None
    adaptive_gate_min_depth_m: float | None = None
    adaptive_gate_max_depth_m: float | None = None
    trigger_out_width_sec: float | None = None
    trigger_out_offset_sec: float | None = None
    projector_81xx_selection: int | None = None
    alternate_gain_db: float | None = None
    vernier_filter: int | None = None
    custom_beams: int | None = None
    coverage_angle_rad: float | None = None
    coverage_mode: int | None = None
    quality_filter_flags: int | None = None
    rx_steering_angle_rad: float | None = None
    flexmode_coverage_rad: float | None = None
    flexmode_steering_rad: float | None = None
    constant_spacing_m: float | None = None
    beam_mode_selection: int | None = None
    depth_gate_tilt_rad: float | None = None
    applied_frequency_hz: float | None = None
    element_number: int | None = None
