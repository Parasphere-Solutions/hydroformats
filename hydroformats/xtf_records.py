"""Typed, immutable records for the Triton XTF dialect.

Every layout is hand-built from the Triton XTF specification, Rev. 41
(anchor S10 in docs/FORMAT-SOURCES.md); the packet framing, the spec
judgment calls and the reader API live in :mod:`hydroformats.xtf`,
which re-exports everything public here. The byte-table helpers at the
bottom (`parse_chan_info`, `ping_fields`, `parse_channel`) are the
shared field extraction the decoders in :mod:`hydroformats.xtf` build
records from.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .records import Record

_CHANNEL_TYPES = {0: "subbottom", 1: "port", 2: "starboard", 3: "bathymetry"}
_NOTE_CATEGORIES = {0: "notes", 1: "vessel name", 2: "survey area",
                    3: "operator name"}

# CHANINFO byte 74 sample formats (spec Table D): value -> byte width.
# 0 is "Legacy" (width from BytesPerSample), 1 is 4-byte IBM float
# (framed but not decoded), 4/6/7 are unused.
_FORMAT_WIDTHS = {1: 4, 2: 4, 3: 2, 5: 4, 8: 1}
_IEEE_FLOAT = 5
_INT_FORMATS = (0, 2, 3, 8)
_INT_CODES = {1: "B", 2: "H", 4: "I"}

SNP0_ID = 0x534E5030
SNP1_ID = 0x534E5031


def text_field(raw: bytes) -> str:
    """Text up to the first NUL, latin-1 (never raises), stripped."""
    return raw.split(b"\x00", 1)[0].decode("latin-1").strip()


# --------------------------------------------------------------------------
# channel metadata (spec Table D)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class XtfChannelInfo:
    """One 128-byte CHANINFO block: static, per-channel metadata that does
    not change during a run (spec Table D).

    ``index`` is the block's position in the file header, which is what a
    packet's per-channel ChannelNumber refers to. ``bytes_per_sample`` is
    the declared width (1, 2 or 4); ``sample_format`` refines it (spec
    values: 0 legacy, 1 IBM float, 2 four-byte integer, 3 two-byte
    integer, 5 IEEE float, 8 one-byte integer). ``unipolar`` nonzero
    means magnitude-only samples, zero means polar (signed) samples.
    ``correction_flags`` is 1 for slant-range imagery and 2 for
    ground-range (corrected) imagery. ``volt_scale`` is the full-scale
    voltage; ``frequency`` is the center transmit frequency (the spec
    table states no unit; its packet-header usage notes give kilohertz).
    Mounting offsets are meters, X positive to starboard, Y positive
    forward, Z positive down; rotations are degrees.
    """

    index: int
    type_of_channel: int
    sub_channel_number: int
    correction_flags: int
    unipolar: int
    bytes_per_sample: int
    reserved_samples_per_channel: int
    name: str
    volt_scale: float
    frequency: float
    horizontal_beam_angle: float
    tilt_angle: float
    beam_width: float
    offset_x: float
    offset_y: float
    offset_z: float
    offset_yaw: float
    offset_pitch: float
    offset_roll: float
    beams_per_array: int
    sample_format: int

    @property
    def type_name(self) -> str | None:
        """subbottom, port, starboard or bathymetry; None when unknown."""
        return _CHANNEL_TYPES.get(self.type_of_channel)

    @property
    def slant_range_corrected(self) -> bool:
        return self.correction_flags == 1

    @property
    def ground_range_corrected(self) -> bool:
        return self.correction_flags == 2


_CHANINFO = struct.Struct("<2B3HI")


def parse_chan_info(data: bytes, offset: int, index: int) -> XtfChannelInfo:
    """One CHANINFO block at ``offset``, per the spec Table D offsets."""
    (channel_type, sub_number, correction, unipolar, width,
     reserved) = _CHANINFO.unpack_from(data, offset)
    floats = struct.unpack_from("<11f", data, offset + 28)
    beams, sample_format = struct.unpack_from("<HB", data, offset + 72)
    return XtfChannelInfo(
        index=index, type_of_channel=channel_type,
        sub_channel_number=sub_number, correction_flags=correction,
        unipolar=unipolar, bytes_per_sample=width,
        reserved_samples_per_channel=reserved,
        name=text_field(data[offset + 12:offset + 28]),
        volt_scale=floats[0], frequency=floats[1],
        horizontal_beam_angle=floats[2], tilt_angle=floats[3],
        beam_width=floats[4],
        offset_x=floats[5], offset_y=floats[6], offset_z=floats[7],
        offset_yaw=floats[8], offset_pitch=floats[9], offset_roll=floats[10],
        beams_per_array=beams, sample_format=sample_format,
    )


# --------------------------------------------------------------------------
# the XTFPINGHEADER family (spec Table H)
# --------------------------------------------------------------------------

# XTFPINGHEADER scalars: field name, byte offset, format.
_PING_FIELDS = (
    ("year", 14, "<H"),
    ("month", 16, "<B"),
    ("day", 17, "<B"),
    ("hour", 18, "<B"),
    ("minute", 19, "<B"),
    ("second", 20, "<B"),
    ("hseconds", 21, "<B"),
    ("julian_day", 22, "<H"),
    ("event_number", 24, "<I"),
    ("ping_number", 28, "<I"),
    ("sound_velocity_mps", 32, "<f"),
    ("ocean_tide_m", 36, "<f"),
    ("conductivity_freq_hz", 44, "<f"),
    ("temperature_freq_hz", 48, "<f"),
    ("pressure_freq_hz", 52, "<f"),
    ("pressure_temp_c", 56, "<f"),
    ("conductivity_s_m", 60, "<f"),
    ("water_temperature_c", 64, "<f"),
    ("pressure_psia", 68, "<f"),
    ("computed_sound_velocity_mps", 72, "<f"),
    ("mag_x_mgauss", 76, "<f"),
    ("mag_y_mgauss", 80, "<f"),
    ("mag_z_mgauss", 84, "<f"),
    ("speed_log_knots", 112, "<f"),
    ("turbidity", 116, "<f"),
    ("ship_speed_knots", 120, "<f"),
    ("ship_gyro_degrees", 124, "<f"),
    ("ship_y", 128, "<d"),
    ("ship_x", 136, "<d"),
    ("ship_altitude_dm", 144, "<H"),
    ("ship_depth_dm", 146, "<H"),
    ("fix_hour", 148, "<B"),
    ("fix_minute", 149, "<B"),
    ("fix_second", 150, "<B"),
    ("fix_hsecond", 151, "<B"),
    ("sensor_speed_knots", 152, "<f"),
    ("kp", 156, "<f"),
    ("sensor_y", 160, "<d"),
    ("sensor_x", 168, "<d"),
    ("sonar_status", 176, "<H"),
    ("range_to_fish_dm", 178, "<H"),
    ("bearing_to_fish_cdeg", 180, "<H"),
    ("cable_out_word_m", 182, "<H"),
    ("layback_m", 184, "<f"),
    ("cable_tension", 188, "<f"),
    ("sensor_depth_m", 192, "<f"),
    ("sensor_primary_altitude_m", 196, "<f"),
    ("sensor_aux_altitude_m", 200, "<f"),
    ("sensor_pitch_degrees", 204, "<f"),
    ("sensor_roll_degrees", 208, "<f"),
    ("sensor_heading_degrees", 212, "<f"),
    ("heave_m", 216, "<f"),
    ("yaw_degrees", 220, "<f"),
    ("attitude_time_tag_ms", 224, "<I"),
    ("distance_off_track", 228, "<f"),
    ("nav_fix_ms", 232, "<I"),
    ("fish_position_delta_x", 240, "<h"),
    ("fish_position_delta_y", 242, "<h"),
    ("fish_position_error_code", 244, "<B"),
    ("optional_offset", 245, "<I"),
    ("cable_out_hundredths", 249, "<B"),
)


@dataclass(frozen=True)
class XtfPing(Record):
    """Base of the XTFPINGHEADER-led packets (spec Table H, 256 bytes):
    the per-ping time, navigation, attitude, tow and environment fields.

    Coordinates (``ship_y``/``ship_x`` and ``sensor_y``/``sensor_x``,
    Y latitude-or-northing first per the spec) are degrees or projected
    meters as selected by the file header's ``nav_units``. Both the ship
    and the sensor positions are decoded, along with the tow geometry
    (``layback_m``, ``cable_out_m``, the Trackpoint deltas): XTF leaves
    which of them describes the imagery's true position to the reader,
    so this library surfaces all of them and none is blessed (see
    :mod:`hydroformats.xtf`). ``sound_velocity_mps`` is verbatim: the
    spec warns it is a one-way 750 m/s in Isis files but 1500 in others.

    Depth conventions: ``sensor_depth_m`` is meters below the sea
    surface, positive down; the altitudes are meters above the seafloor;
    ``heave_m`` is positive up; pitch positive nose up; roll positive to
    starboard. ``ship_altitude_dm``/``ship_depth_dm`` are the raw
    decimeter words; ``fish_position_delta_x``/``_y`` are the raw
    Trackpoint words (meters times three); the ``*_m`` properties
    convert. The full 256 header bytes ride along in ``header_bytes``,
    so the fields not named here (contact and reserved words) remain
    reachable.
    """

    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    hseconds: int
    julian_day: int
    event_number: int
    ping_number: int
    sound_velocity_mps: float
    ocean_tide_m: float
    conductivity_freq_hz: float
    temperature_freq_hz: float
    pressure_freq_hz: float
    pressure_temp_c: float
    conductivity_s_m: float
    water_temperature_c: float
    pressure_psia: float
    computed_sound_velocity_mps: float
    mag_x_mgauss: float
    mag_y_mgauss: float
    mag_z_mgauss: float
    aux_values: tuple[float, ...]
    speed_log_knots: float
    turbidity: float
    ship_speed_knots: float
    ship_gyro_degrees: float
    ship_y: float
    ship_x: float
    ship_altitude_dm: int
    ship_depth_dm: int
    fix_hour: int
    fix_minute: int
    fix_second: int
    fix_hsecond: int
    sensor_speed_knots: float
    kp: float
    sensor_y: float
    sensor_x: float
    sonar_status: int
    range_to_fish_dm: int
    bearing_to_fish_cdeg: int
    cable_out_word_m: int
    layback_m: float
    cable_tension: float
    sensor_depth_m: float
    sensor_primary_altitude_m: float
    sensor_aux_altitude_m: float
    sensor_pitch_degrees: float
    sensor_roll_degrees: float
    sensor_heading_degrees: float
    heave_m: float
    yaw_degrees: float
    attitude_time_tag_ms: int
    distance_off_track: float
    nav_fix_ms: int
    fish_position_delta_x: int
    fish_position_delta_y: int
    fish_position_error_code: int
    optional_offset: int
    cable_out_hundredths: int
    header_bytes: bytes

    @property
    def time_of_day(self) -> float:
        """Seconds past midnight of the ping's calendar date (the time
        convention this library's other dialects use)."""
        return (self.hour * 3600 + self.minute * 60 + self.second
                + self.hseconds / 100.0)

    @property
    def ship_altitude_m(self) -> float:
        return self.ship_altitude_dm / 10.0

    @property
    def ship_depth_m(self) -> float:
        return self.ship_depth_dm / 10.0

    @property
    def range_to_fish_m(self) -> float:
        return self.range_to_fish_dm / 10.0

    @property
    def bearing_to_fish_degrees(self) -> float:
        return self.bearing_to_fish_cdeg / 100.0

    @property
    def cable_out_m(self) -> float:
        """Cable payed out: the whole-meter word plus the hundredths."""
        return self.cable_out_word_m + self.cable_out_hundredths / 100.0

    @property
    def fish_position_delta_x_m(self) -> float:
        """Trackpoint delta in meters (stored as meters times three)."""
        return self.fish_position_delta_x / 3.0

    @property
    def fish_position_delta_y_m(self) -> float:
        return self.fish_position_delta_y / 3.0


def ping_fields(payload: bytes) -> dict:
    """Every XTFPINGHEADER scalar keyed by field name, plus the six
    auxiliary floats as one tuple and the raw 256 header bytes."""
    values = {name: struct.unpack_from(fmt, payload, offset)[0]
              for name, offset, fmt in _PING_FIELDS}
    values["aux_values"] = struct.unpack_from("<6f", payload, 88)
    values["header_bytes"] = payload[:256]
    return values


# --------------------------------------------------------------------------
# per-channel sidescan data (spec Table I)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class XtfPingChannel:
    """One channel of a sonar ping: the 64-byte XTFPINGCHANHEADER (spec
    Table I) plus the channel's sample bytes.

    ``sample_bytes`` is the raw data verbatim; :meth:`values` decodes on
    demand. ``bytes_per_sample``, ``sample_format`` and ``unipolar`` are
    carried in from the matching CHANINFO so every channel record is
    self-sufficient. ``slant_range_m`` is the recorded range;
    ``time_duration_s`` the listening time; ``frequency_khz`` the center
    frequency word (kilohertz per the spec's usage notes; the table
    itself states no unit); ``fixed_vsop_cm`` the fixed along-track ping
    footprint in centimeters. Contact-marking words (unused per the
    spec) remain reachable through ``header_bytes``.
    """

    channel_number: int
    downsample_method: int
    slant_range_m: float
    ground_range_m: float
    time_delay_s: float
    time_duration_s: float
    seconds_per_ping_s: float
    processing_flags: int
    frequency_khz: int
    initial_gain_code: int
    gain_code: int
    bandwidth: int
    num_samples: int
    millivolt_scale: int
    fixed_vsop_cm: float
    weight_factor: int
    sample_format: int
    bytes_per_sample: int
    unipolar: int
    sample_bytes: bytes
    header_bytes: bytes

    def values(self, signed: bool | None = None) -> tuple | None:
        """The samples decoded per the channel's declared format, or None
        when the format cannot be decoded faithfully (IBM float, unused
        format codes): the raw bytes are always in ``sample_bytes``.

        Integer samples decode unsigned when the channel is unipolar and
        signed when it is polar (the spec never states signedness; see
        :mod:`hydroformats.xtf`); pass ``signed`` to override.
        """
        if self.sample_format == _IEEE_FLOAT:
            count = len(self.sample_bytes) // 4
            return struct.unpack(f"<{count}f", self.sample_bytes[:count * 4])
        if self.sample_format not in _INT_FORMATS:
            return None
        code = _INT_CODES.get(self.bytes_per_sample)
        if code is None:
            return None
        if signed is None:
            signed = self.unipolar == 0
        count = len(self.sample_bytes) // self.bytes_per_sample
        fmt = f"<{count}{code.lower() if signed else code}"
        return struct.unpack(fmt,
                             self.sample_bytes[:count * self.bytes_per_sample])


def sample_width(info: XtfChannelInfo) -> int | None:
    """Bytes per sample for one channel, None when it cannot be sized:
    the sample format's implied width when it has one, else the CHANINFO
    BytesPerSample word (valid values 1, 2 and 4 per the spec)."""
    width = _FORMAT_WIDTHS.get(info.sample_format)
    if width is not None:
        return width
    return info.bytes_per_sample if info.bytes_per_sample in (1, 2, 4) else None


_CHAN_HEAD = struct.Struct("<2H5f5H")


def parse_channel(payload: bytes, position: int, end: int,
                  info: XtfChannelInfo) -> XtfPingChannel:
    """One channel header plus its samples at ``position``; raises
    ValueError when the declared sample count overruns the packet."""
    head = payload[position:position + 64]
    (channel_number, downsample, slant, ground, delay, duration, per_ping,
     flags, frequency, initial_gain, gain,
     bandwidth) = _CHAN_HEAD.unpack_from(head, 0)
    (num_samples,) = struct.unpack_from("<I", head, 42)
    (millivolt,) = struct.unpack_from("<H", head, 46)
    (vsop,) = struct.unpack_from("<f", head, 54)
    (weight,) = struct.unpack_from("<h", head, 58)
    width = sample_width(info)
    size = num_samples * (width or 0)
    if position + 64 + size > end:
        raise ValueError(
            f"channel {channel_number}: {num_samples} samples of {width} "
            f"bytes overrun the packet at offset {position + 64}")
    return XtfPingChannel(
        channel_number=channel_number, downsample_method=downsample,
        slant_range_m=slant, ground_range_m=ground, time_delay_s=delay,
        time_duration_s=duration, seconds_per_ping_s=per_ping,
        processing_flags=flags, frequency_khz=frequency,
        initial_gain_code=initial_gain, gain_code=gain, bandwidth=bandwidth,
        num_samples=num_samples, millivolt_scale=millivolt,
        fixed_vsop_cm=vsop, weight_factor=weight,
        sample_format=info.sample_format, bytes_per_sample=width or 0,
        unipolar=info.unipolar,
        sample_bytes=payload[position + 64:position + 64 + size],
        header_bytes=head,
    )


@dataclass(frozen=True)
class XtfSonarPing(XtfPing):
    """One sidescan sonar ping (header type 0): the shared ping header
    plus one :class:`XtfPingChannel` per channel, in packet order.

    A channel whose ChannelNumber has no CHANINFO block cannot be sized
    (the sample width lives in the file header), so it and everything
    after it in the packet are skipped and reported in
    ``skipped_channels`` as (channel number, bytes remaining) pairs.
    """

    channels: tuple[XtfPingChannel, ...]
    skipped_channels: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class XtfRawBathy(XtfPing):
    """A bathymetry packet (header type 2, XTFBATHHEADER): the shared
    ping header, then the multibeam vendor's datagram logged raw.

    The spec defines no layout for the payload ("logged exactly as
    received from the multibeam system"); it is carried verbatim,
    including any zero padding the writer appended, for a
    vendor-specific consumer to decode.
    """

    payload: bytes


# --------------------------------------------------------------------------
# bathy snippet blocks (spec Tables N and O)
# --------------------------------------------------------------------------

SNP0_FIELDS = (
    ("header_size", 4, "<H"),
    ("data_size", 6, "<H"),
    ("ping_number", 8, "<I"),
    ("seconds", 12, "<I"),
    ("milliseconds", 16, "<I"),
    ("latency_ms", 20, "<H"),
    ("sonar_model", 26, "<H"),
    ("frequency_khz", 28, "<H"),
    ("sound_velocity_mps", 30, "<H"),
    ("sample_rate_hz", 32, "<H"),
    ("ping_rate_millihz", 34, "<H"),
    ("range_m", 36, "<H"),
    ("power", 38, "<H"),
    ("gain_word", 40, "<H"),
    ("pulse_width_us", 42, "<H"),
    ("spread", 44, "<H"),
    ("absorb", 46, "<H"),
    ("projector", 48, "<H"),
    ("projector_width", 50, "<H"),
    ("spacing_numerator", 52, "<H"),
    ("spacing_denominator", 54, "<H"),
    ("projector_angle", 56, "<h"),
    ("min_range", 58, "<H"),
    ("max_range", 60, "<H"),
    ("min_depth", 62, "<H"),
    ("max_depth", 64, "<H"),
    ("filters", 66, "<H"),
    ("head_temp", 70, "<h"),
    ("beam_count", 72, "<H"),
)

SNP1 = struct.Struct("<I2HI6H")


@dataclass(frozen=True)
class XtfSnp0:
    """The SNP0 block leading a bathy snippet packet (spec Table N, 74
    bytes): the Reson SeaBat ping settings in force. Words are verbatim
    sonar units per the table: ``frequency_khz`` kilohertz,
    ``sound_velocity_mps`` whole meters per second,
    ``sample_rate_hz`` samples per second, ``ping_rate_millihz``
    thousandths of hertz, ``gain_word`` with control bits 15/14,
    ``head_temp`` tenths of a degree (see ``head_temp_c``)."""

    header_size: int
    data_size: int
    ping_number: int
    seconds: int
    milliseconds: int
    latency_ms: int
    sonar_id: tuple[int, int]
    sonar_model: int
    frequency_khz: int
    sound_velocity_mps: int
    sample_rate_hz: int
    ping_rate_millihz: int
    range_m: int
    power: int
    gain_word: int
    pulse_width_us: int
    spread: int
    absorb: int
    projector: int
    projector_width: int
    spacing_numerator: int
    spacing_denominator: int
    projector_angle: int
    min_range: int
    max_range: int
    min_depth: int
    max_depth: int
    filters: int
    flags: tuple[int, int]
    head_temp: int
    beam_count: int

    @property
    def head_temp_c(self) -> float:
        return self.head_temp / 10.0


@dataclass(frozen=True)
class XtfSnippetBeam:
    """One SNP1 block plus its fragment (spec Table O): a snippet of
    samples around the bottom detection of one beam. Gains are 0.01 dB
    steps (zero means ignore); ``fragment_offset`` and
    ``fragment_samples`` count samples from the ping. The fragment
    sample encoding is the sonar's own and the spec does not define it,
    so ``fragment_bytes`` is carried raw."""

    header_size: int
    data_size: int
    ping_number: int
    beam: int
    snippet_samples: int
    gain_start: int
    gain_end: int
    fragment_offset: int
    fragment_samples: int
    fragment_bytes: bytes


@dataclass(frozen=True)
class XtfBathySnippet(XtfPing):
    """A bathy snippet packet (header type 19): the shared ping header,
    the sonar's SNP0 settings block, then one :class:`XtfSnippetBeam`
    per beam (SNP0's beam count of them).

    Reson snippet passthrough: the SNP0/SNP1 headers are decoded, the
    fragment samples are carried raw (their encoding is vendor-defined;
    see :class:`XtfSnippetBeam`). A beam list that ends early (bad SNP1
    magic, truncation) keeps the decoded beams and leaves the remaining
    bytes, packet padding included, in ``leftover``.
    """

    snp0: XtfSnp0
    beams: tuple[XtfSnippetBeam, ...]
    leftover: bytes


# --------------------------------------------------------------------------
# attitude, notes and raw serial packets (spec Tables E, F, G)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class XtfAttitude(Record):
    """An XTFATTITUDEDATA packet (spec Table E, 64 bytes): one MRU/TSS
    attitude update.

    Angles are degrees: pitch positive nose up, roll positive to
    starboard, yaw positive turning right; ``heave_m`` is positive
    sensor up (the spec notes Isis flips MRU heave to keep this
    convention). Two clocks are carried: ``time_tag_ms`` is the system
    millisecond timer that pairs with the ping header's
    ``attitude_time_tag_ms``, and ``source_epoch`` (+
    ``epoch_microseconds``) is seconds since 1970 when the source
    supplied it, zero otherwise. This packet's table misprints the two
    leading byte offsets; the uniform packet prefix is used (see
    :mod:`hydroformats.xtf`).
    """

    epoch_microseconds: int
    source_epoch: int
    pitch_degrees: float
    roll_degrees: float
    heave_m: float
    yaw_degrees: float
    time_tag_ms: int
    heading_degrees: float
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    milliseconds: int

    @property
    def source_time(self) -> float | None:
        """Epoch seconds from the source clock, None when not supplied."""
        if self.source_epoch == 0:
            return None
        return self.source_epoch + self.epoch_microseconds / 1e6

    @property
    def time_of_day(self) -> float:
        """Seconds past midnight of the packet's calendar fields."""
        return (self.hour * 3600 + self.minute * 60 + self.second
                + self.milliseconds / 1000.0)


@dataclass(frozen=True)
class XtfNotes(Record):
    """An XTFNOTESHEADER packet (spec Table F, 256 bytes): free-text
    annotation. ``sub_channel`` labels the text (0 notes, 1 vessel name,
    2 survey area, 3 operator name; see ``category``)."""

    sub_channel: int
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    text: str

    @property
    def category(self) -> str | None:
        return _NOTE_CATEGORIES.get(self.sub_channel)


@dataclass(frozen=True)
class XtfRawSerial(Record):
    """An XTFRAWSERIALHEADER packet (spec Table G): one line of raw
    ASCII from a serial port, typically an NMEA sentence, so navigation
    can interleave with the sonar stream. ``serial_port`` is the COM
    port number (0 when received another way); ``time_tag_ms`` the
    millisecond timer. Decoded latin-1 (never raises) with trailing
    NUL/CR/LF stripped; parsing the sentence is the caller's job."""

    serial_port: int
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    hseconds: int
    julian_day: int
    time_tag_ms: int
    text: str

    @property
    def time_of_day(self) -> float:
        return (self.hour * 3600 + self.minute * 60 + self.second
                + self.hseconds / 100.0)
