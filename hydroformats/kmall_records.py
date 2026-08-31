"""Typed records for the Kongsberg KMALL dialect (see hydroformats/kmall.py).

A multibeam echo sounder transmits a fan of sound across the vessel's
track and listens for the bottom echo on hundreds of beams at once.
Each ping therefore yields a swath of soundings: for every beam, the
sonar measures how long the echo took to return (two way travel time)
and from which angle it arrived, then traces the sound path through
the water column's sound speed profile to compute a depth point. The
KMALL format records both stages: the raw observables (travel time,
beam angle, the sound speed used) and the processed depth points
(x, y, z), so the soundings can be reprocessed later under a corrected
sound speed profile. Each beam also carries reflectivity (backscatter):
how strongly the seafloor scattered the sound back, a proxy for bottom
type, plus a strip of seabed image samples around the detection point.

Every field layout here is hand-built from the format owner's own
specification alone (anchor S11 in docs/FORMAT-SOURCES.md): *EM
datagrams on \\*.kmall format*, Kongsberg document 410224 revision J,
2023-09-15. Field names in docstrings are the spec's struct member
names; all data is little endian with 4-byte alignment. Sizes quoted
are pinned by ``test_docstring_layout_sizes``.

Vertical sign conventions follow the spec's surface coordinate system
(SCS): z is positive down, x positive forward, y positive starboard,
origin at the vessel reference point at the time of transmission.
Heave is positive down. Depth points are relative to the vessel
reference point; subtract ``z_water_level_re_ref_point_m`` to refer
them to the waterline (spec chapter on reference points and offsets).
"""
from __future__ import annotations

from dataclasses import dataclass

from .records import Record

# Sentinels for unavailable sensor data (spec defines, exact values).
UNAVAILABLE_LATITUDE = 200.0
UNAVAILABLE_LONGITUDE = 200.0
UNAVAILABLE_SPEED = -1.0
UNAVAILABLE_COURSE = -4.0
UNAVAILABLE_ELLIPSOID_HEIGHT = -999.0


@dataclass(frozen=True)
class KmallRecord(Record):
    """Base of every KMALL record: the general datagram header fields
    (EMdgmHeader_def, 20 bytes: u32 length, 4-char type code, version,
    system id, echo sounder id, UTC time). ``echo_sounder_id`` names
    the sonar model, e.g. 124, 304, 712, 2040. ``system_id`` separates
    echosounders when more than one logs into the same stream."""

    dgm_version: int
    system_id: int
    echo_sounder_id: int
    time_sec: int
    time_nanosec: int

    @property
    def time(self) -> float:
        """Seconds since the 1970 epoch, UTC."""
        return self.time_sec + self.time_nanosec / 1e9


@dataclass(frozen=True)
class KmallInstallation(KmallRecord):
    """#IIP (EMdgmIIP_def): installation parameters and sensor setup.

    The body is a text blob: parameters as ``KEY=VALUE`` and
    ``KEY:VALUE`` pairs, comma delimited per the spec, carried
    verbatim in ``text`` (trailing NULs stripped). The spec defers the
    key meanings to its separate installation parameters document, so
    nothing is interpreted here; ``lines`` splits on commas for
    convenience only."""

    info: int
    status: int
    text: str

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(self.text.split(","))


@dataclass(frozen=True)
class KmallRuntime(KmallRecord):
    """#IOP (EMdgmIOP_def): runtime parameters exactly as chosen by
    the operator in the K-Controller/SIS menus, as a text blob carried
    verbatim in ``text`` (trailing NULs stripped). The spec defers the
    key meanings to its separate runtime parameters document."""

    info: int
    status: int
    text: str

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(self.text.split(","))


@dataclass(frozen=True)
class KmallPosition(KmallRecord):
    """#SPO (EMdgmSPO_def): one position sensor observation.

    An 8-byte common sensor part (EMdgmScommon_def) then the 40-byte
    fixed data block (EMdgmSPOdataBlock_def) and the raw sensor
    telegram as text. The corrected values are what the processing
    unit used in the depth calculations (corrected for installation
    offsets, and for motion if enabled); the raw text preserves the
    sensor's own telegram, e.g. a NMEA GGA sentence. Sentinels mark
    unavailable values: latitude/longitude 200.0, speed -1.0, course
    -4.0, ellipsoid height -999.0 (spec defines, matched exactly).
    ``sensor_status`` bit 0 set means the sensor is the active one.
    """

    sensor_system: int
    sensor_status: int
    time_from_sensor_sec: int
    time_from_sensor_nanosec: int
    pos_fix_quality_m: float
    corrected_lat_deg: float
    corrected_long_deg: float
    speed_over_ground_mps: float
    course_over_ground_deg: float
    ellipsoid_height_re_ref_point_m: float
    raw_text: str

    @property
    def time_from_sensor(self) -> float:
        return self.time_from_sensor_sec + self.time_from_sensor_nanosec / 1e9

    @property
    def active(self) -> bool:
        """True when the sensor is chosen as active (status bit 0)."""
        return bool(self.sensor_status & 0x01)

    @property
    def position_available(self) -> bool:
        """False when the spec's unavailable sentinel (200.0) is set."""
        return (self.corrected_lat_deg != UNAVAILABLE_LATITUDE
                and self.corrected_long_deg != UNAVAILABLE_LONGITUDE)


@dataclass(frozen=True)
class KmallCompatibilityPosition(KmallPosition):
    """#CPO (EMdgmCPO_def): position data for backward compatibility
    with the older .all format. Byte for byte the same layout as #SPO
    (EMdgmCPOdataBlock_def mirrors EMdgmSPOdataBlock_def), kept as its
    own type so the two streams stay apart."""


@dataclass(frozen=True)
class KmallAttitudeSample(Record):
    """One #SKM sensor sample (EMdgmSKMsample_def, 132 bytes): a
    120-byte KM binary block (KMbinary_def) plus a 12-byte delayed
    heave block (KMdelayedHeave_def) when the sensor provides one.

    Data is timestamped but uncorrected: installation offsets and
    angles have not been applied. ``status`` is the spec's validity
    word (bits 0-7 flag invalid data: bit 0 position/velocity, 1 roll
    and pitch, 2 heading, 3 heave, 4 acceleration, 5-6 delayed heave;
    bits 16+ flag reduced performance in the same order). Angles are
    degrees, heave meters positive down, rates degrees per second,
    velocities meters per second (north, east, down), errors are
    standard deviations, accelerations m/s^2. The delayed heave fields
    are None when the sample carries no delayed heave block."""

    time_sec: int
    time_nanosec: int
    status: int
    latitude_deg: float
    longitude_deg: float
    ellipsoid_height_m: float
    roll_deg: float
    pitch_deg: float
    heading_deg: float
    heave_m: float
    roll_rate: float
    pitch_rate: float
    yaw_rate: float
    vel_north: float
    vel_east: float
    vel_down: float
    latitude_error_m: float
    longitude_error_m: float
    ellipsoid_height_error_m: float
    roll_error_deg: float
    pitch_error_deg: float
    heading_error_deg: float
    heave_error_m: float
    north_acceleration: float
    east_acceleration: float
    down_acceleration: float
    delayed_heave_time_sec: int | None = None
    delayed_heave_time_nanosec: int | None = None
    delayed_heave_m: float | None = None

    @property
    def time(self) -> float:
        """Seconds since the epoch, from inside the sensor data."""
        return self.time_sec + self.time_nanosec / 1e9


@dataclass(frozen=True)
class KmallAttitude(KmallRecord):
    """#SKM (EMdgmSKM_def): a block of attitude sensor samples.

    A 12-byte info part (EMdgmSKMinfo_def) then up to 148 samples.
    The header time is the arrival time at the processing unit; each
    sample carries the sensor's own timestamp, so consecutive #SKM
    records overlap the position stream and must be interpolated by
    sample time, not header time (spec introduction). All values are
    uncorrected sensor data. ``sensor_input_format`` codes the raw
    input format (1 KM binary, 2 EM 3000, 3 Sagem, 4-6 Seapath binary
    11/23/26, 7 POS M/V GRP 102/103); ``sensor_data_contents`` flags
    which fields the input format actually supplies (bit 0 position
    and velocity, 1 roll and pitch, 2 heading, 3 heave, 4
    acceleration, 5-6 delayed heave)."""

    sensor_system: int
    sensor_status: int
    sensor_input_format: int
    sensor_data_contents: int
    samples: tuple[KmallAttitudeSample, ...]

    @property
    def num_samples(self) -> int:
        return len(self.samples)


@dataclass(frozen=True)
class KmallSvp(KmallRecord):
    """#SVP (EMdgmSVP_def): one sound velocity profile or CTD cast.

    A 28-byte common part then one 20-byte point per sample
    (EMdgmSVPpoint_def). ``sensor_format`` is 'S00' for a measured
    sound velocity profile and 'S01' for a CTD profile (sound velocity
    then calculated); for 'S00' the temperature and salinity arrays
    are zero by spec. Depths are meters below the surface, sound
    speeds meters per second. Each point's voided former absorption
    word (now padding) is not carried. ``profile_time_sec`` and the
    position are as extracted from the profile itself, zero or the
    200.0 sentinel when not found."""

    sensor_format: str
    profile_time_sec: int
    latitude_deg: float
    longitude_deg: float
    depths_m: tuple[float, ...]
    sound_speeds_mps: tuple[float, ...]
    temperatures_c: tuple[float, ...]
    salinities: tuple[float, ...]

    @property
    def num_points(self) -> int:
        return len(self.depths_m)


@dataclass(frozen=True)
class KmallMbody(KmallRecord):
    """Base for records carrying the common multibeam body
    (EMdgmMbody_def, 12 bytes): which ping, which receiver fan and
    which swath this datagram belongs to. A ping may spread over
    several datagrams (dual receiver heads, multi swath); combining
    ``rx_fans_per_ping`` and ``swaths_per_ping`` tells how many
    datagrams to join for a complete swath. ``rx_fan_index`` 0 is the
    aft swath, port side; ``swath_along_position`` 0 is the aftmost
    swath in multi swath mode."""

    ping_cnt: int
    rx_fans_per_ping: int
    rx_fan_index: int
    swaths_per_ping: int
    swath_along_position: int
    tx_transducer_ind: int
    rx_transducer_ind: int
    num_rx_transducers: int
    algorithm_type: int


@dataclass(frozen=True)
class KmallHeave(KmallMbody):
    """#CHE (EMdgmCHE_def): compatibility heave, sent for backward
    compatibility with the .all format alongside water column data.
    The heave reference point is at the transducer, not the vessel
    reference point. Meters, positive down."""

    heave_m: float


@dataclass(frozen=True)
class KmallWaterColumn(KmallMbody):
    """#MWC (EMdgmMWC_def): water column datagram, deliberately
    decoded header-only.

    Water column data is the full per-beam amplitude (and optionally
    phase) sample series through the water, routinely gigabytes per
    survey and usually logged to a separate .kmwcd file. This reader
    decodes the general header, the partition words and the common
    multibeam body so the datagram is identified, timed and tied to
    its ping, and records the byte size; the transmit/receiver info
    and sample payload are deliberately not decoded (see
    docs/FORMAT-SOURCES.md anchor S11). ``num_bytes`` is the declared
    size of the whole datagram, length words included."""

    num_of_dgms: int
    dgm_num: int
    num_bytes: int


@dataclass(frozen=True)
class KmallTxSector(Record):
    """One #MRZ transmit sector (EMdgmMRZ_txSectorInfo_def, 48 bytes;
    36 before #MRZ version 1, which leaves the last three fields
    None). A ping is transmitted as one or more sectors, each with
    its own delay, tilt, frequency and pulse; every sounding names
    its sector by ``tx_sector_numb``. The transmit delay is relative
    to the time in the datagram header (midpoint of the first pulse).
    ``signal_wave_form`` is 0 CW, 1 FM upsweep, 2 FM downsweep.
    Actual source level = nominal + high voltage level (spec)."""

    tx_sector_numb: int
    tx_arr_number: int
    tx_sub_array: int
    sector_transmit_delay_sec: float
    tilt_angle_re_tx_deg: float
    tx_nominal_source_level_db: float
    tx_focus_range_m: float
    centre_freq_hz: float
    signal_bandwidth_hz: float
    total_signal_length_sec: float
    pulse_shading: int
    signal_wave_form: int
    high_voltage_level_db: float | None = None
    sector_tracking_corr_db: float | None = None
    effective_signal_length_sec: float | None = None


@dataclass(frozen=True)
class KmallSounding(Record):
    """One #MRZ sounding (EMdgmMRZ_sounding_def, 120 bytes): raw
    observables, detection info, reflectivity and the processed depth
    point for one beam.

    Detection info: ``detection_type`` is 0 normal, 1 extra detection
    (a water column point), 2 rejected (its range is estimated from
    neighbors); ``detection_method`` is 0 no valid detection, 1
    amplitude, 2 phase. ``quality_factor`` is the estimated standard
    deviation as percent of depth (from the IFREMER quality factor);
    the vertical and horizontal uncertainties derive from it.

    Raw observables: ``two_way_travel_time_sec`` and
    ``beam_angle_re_rx_deg`` (angle relative to the receiver array)
    are the measurement itself, kept beside the applied corrections
    so soundings can be re-reduced under a corrected sound speed.

    Reflectivity: ``reflectivity1_db`` is the backscatter with the
    traditional Kongsberg TVG (insonified area, Lambert's law and
    normal incidence corrections); ``reflectivity2_db`` uses the
    simplified water column TVG without those corrections, with the
    operator's display offset still included. The applied source
    level, receiver sensitivity, calibration offset and TVG ride
    along so either can be undone.

    Depth point: x (forward), y (starboard) and z (down) in meters
    from the vessel reference point at the time of the first transmit
    pulse, in the surface coordinate system; also given as delta
    latitude/longitude in degrees. Subtract the ping's
    ``z_water_level_re_ref_point_m`` from z for depth below the
    waterline.

    Seabed image: this beam contributes ``si_num_samples`` samples to
    the ping's flat seabed image array, starting at range sample
    ``si_start_range_samples``; sample ``si_centre_sample`` (counting
    from 1) is the one at the depth point."""

    sounding_index: int
    tx_sector_numb: int
    detection_type: int
    detection_method: int
    rejection_info1: int
    rejection_info2: int
    post_processing_info: int
    detection_class: int
    detection_confidence_level: int
    range_factor: float
    quality_factor: float
    detection_uncertainty_ver_m: float
    detection_uncertainty_hor_m: float
    detection_window_length_sec: float
    echo_length_sec: float
    wc_beam_numb: int
    wc_range_samples: int
    wc_nom_beam_angle_across_deg: float
    mean_abs_coeff_db_per_km: float
    reflectivity1_db: float
    reflectivity2_db: float
    receiver_sensitivity_applied_db: float
    source_level_applied_db: float
    bs_calibration_db: float
    tvg_db: float
    beam_angle_re_rx_deg: float
    beam_angle_correction_deg: float
    two_way_travel_time_sec: float
    two_way_travel_time_correction_sec: float
    delta_latitude_deg: float
    delta_longitude_deg: float
    z_re_ref_point_m: float
    y_re_ref_point_m: float
    x_re_ref_point_m: float
    beam_inc_angle_adj_deg: float
    real_time_clean_info: int
    si_start_range_samples: int
    si_centre_sample: int
    si_num_samples: int


def sounding_usable(sounding: KmallSounding) -> bool:
    """True when a sounding is neither rejected nor detection-less.

    Per the spec's detection info: ``detection_type`` 2 marks a
    rejected detection (range merely estimated from neighbors) and
    ``detection_method`` 0 marks no valid detection. Extra detections
    (type 1) pass; filter on ``detection_type == 0`` to keep only
    main bottom detections.
    """
    return sounding.detection_type != 2 and sounding.detection_method != 0


@dataclass(frozen=True)
class KmallPing(KmallMbody):
    """#MRZ (EMdgmMRZ_def): one receiver fan of one ping, with raw
    ranges, processed depths, reflectivity and the seabed image.

    Block order per the spec: general header, partition, common body,
    ping info (152 bytes; 144 before #MRZ version 1), transmit
    sectors, receiver info (32 bytes), extra detection class info,
    soundings (120 bytes each), seabed image samples. Every block
    declares its own byte size, and this reader walks by those
    declared sizes, so unknown trailing fields from newer revisions
    are skipped, never misread. ``num_partitions`` is how many
    datagram parts were joined to form this record (always 1 in files
    logged by SIS/K-Controller, which merge parts before writing).

    Ping info highlights (all fields per EMdgmMRZ_pingInfo_def):
    the position of the vessel reference point at the midpoint of the
    first transmit pulse (``latitude_deg``/``longitude_deg``, decimal
    degrees, the 200.0 sentinel when unavailable), the sound speed at
    transducer depth used for the depth calculations, the transducer
    depth below the waterline, and the mode/filter words exactly as
    stored. ``z_water_level_re_ref_point_m`` moves depths from the
    vessel reference point to the waterline (subtract it from z).

    Soundings are in datagram order: the main bottom detections
    (``num_soundings_max_main`` of them, extra detections excluded)
    then any extra detections. The seabed image is one flat array of
    0.1 dB amplitude samples in the same beam order; each sounding's
    ``si_num_samples`` says how many belong to that beam, shortest
    range first (see :meth:`seabed_image_per_beam`).
    """

    num_partitions: int
    ping_rate_hz: float
    beam_spacing: int
    depth_mode: int
    sub_depth_mode: int
    distance_btw_swath: int
    detection_mode: int
    pulse_form: int
    fixed_gain_control: int
    frequency_mode_hz: float
    freq_range_low_lim_hz: float
    freq_range_high_lim_hz: float
    max_total_tx_pulse_length_sec: float
    max_eff_tx_pulse_length_sec: float
    max_eff_tx_bandwidth_hz: float
    abs_coeff_db_per_km: float
    port_sector_edge_deg: float
    starb_sector_edge_deg: float
    port_mean_cov_deg: float
    starb_mean_cov_deg: float
    port_mean_cov_m: int
    starb_mean_cov_m: int
    mode_and_stabilisation: int
    runtime_filter1: int
    runtime_filter2: int
    pipe_tracking_status: int
    transmit_array_size_used_deg: float
    receive_array_size_used_deg: float
    transmit_power_db: float
    sl_ramp_up_time_remaining: int
    yaw_angle_deg: float
    heading_vessel_deg: float
    sound_speed_at_tx_depth_mps: float
    tx_transducer_depth_m: float
    z_water_level_re_ref_point_m: float
    x_kmall_to_all_m: float
    y_kmall_to_all_m: float
    lat_long_info: int
    pos_sensor_status: int
    attitude_sensor_status: int
    latitude_deg: float
    longitude_deg: float
    ellipsoid_height_re_ref_point_m: float
    tx_sectors: tuple[KmallTxSector, ...]
    num_soundings_max_main: int
    num_soundings_valid_main: int
    wc_sample_rate_hz: float
    seabed_image_sample_rate_hz: float
    bs_normal_db: float
    bs_oblique_db: float
    extra_detection_alarm_flag: int
    num_extra_detections: int
    extra_detection_classes: tuple[tuple[int, int], ...]
    soundings: tuple[KmallSounding, ...]
    si_samples: tuple[int, ...]
    bs_correction_offset_db: float | None = None
    lamberts_law_applied: int | None = None
    ice_window: int | None = None
    active_modes: int | None = None

    @property
    def position_available(self) -> bool:
        """False when the spec's unavailable sentinel (200.0) is set."""
        return (self.latitude_deg != UNAVAILABLE_LATITUDE
                and self.longitude_deg != UNAVAILABLE_LONGITUDE)

    @property
    def num_tx_sectors(self) -> int:
        return len(self.tx_sectors)

    # ---- per-beam array views (datagram beam order) ----

    @property
    def two_way_travel_times_sec(self) -> tuple[float, ...]:
        """Raw two way travel time per sounding, seconds."""
        return tuple(s.two_way_travel_time_sec for s in self.soundings)

    @property
    def beam_angles_re_rx_deg(self) -> tuple[float, ...]:
        """Raw beam angle per sounding, relative to the RX array."""
        return tuple(s.beam_angle_re_rx_deg for s in self.soundings)

    @property
    def z_re_ref_point_m(self) -> tuple[float, ...]:
        """Depth per sounding, meters, positive down, from the vessel
        reference point in the surface coordinate system."""
        return tuple(s.z_re_ref_point_m for s in self.soundings)

    @property
    def y_re_ref_point_m(self) -> tuple[float, ...]:
        """Across track distance per sounding, meters, positive to
        starboard."""
        return tuple(s.y_re_ref_point_m for s in self.soundings)

    @property
    def x_re_ref_point_m(self) -> tuple[float, ...]:
        """Along track distance per sounding, meters, positive forward."""
        return tuple(s.x_re_ref_point_m for s in self.soundings)

    @property
    def reflectivity1_db(self) -> tuple[float, ...]:
        """Backscatter per sounding with the traditional Kongsberg TVG."""
        return tuple(s.reflectivity1_db for s in self.soundings)

    @property
    def reflectivity2_db(self) -> tuple[float, ...]:
        """Backscatter per sounding with the simplified TVG (no Lambert
        or normal incidence correction; display offset included)."""
        return tuple(s.reflectivity2_db for s in self.soundings)

    @property
    def depths_re_waterline_m(self) -> tuple[float, ...]:
        """Depth per sounding referred to the waterline: z minus the
        water level distance, per the spec's reference points chapter."""
        z_wl = self.z_water_level_re_ref_point_m
        return tuple(s.z_re_ref_point_m - z_wl for s in self.soundings)

    @property
    def seabed_image_db(self) -> tuple[float, ...]:
        """The flat seabed image restored to dB (stored 0.1 dB steps)."""
        return tuple(value / 10.0 for value in self.si_samples)

    def seabed_image_per_beam(self) -> tuple[tuple[int, ...], ...]:
        """The flat seabed image split per sounding, raw 0.1 dB counts.

        Per the spec's seabed image organization: counting soundings
        in datagram order, each takes its ``si_num_samples`` samples
        from the flat array in turn, shortest range first within the
        beam. Sample counts that overrun the stored array yield
        shorter tails rather than raising.
        """
        pieces: list[tuple[int, ...]] = []
        position = 0
        for entry in self.soundings:
            pieces.append(
                self.si_samples[position:position + entry.si_num_samples])
            position += entry.si_num_samples
        return tuple(pieces)
