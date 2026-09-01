"""Typed, immutable records for the Klein SDF dialect.

Every layout is hand-built from the format owner's own data page
specification and its UDP companion document (anchor S14 in
docs/FORMAT-SOURCES.md), with the 3500-series page specifics anchored
to OceanScan-MST's MIT-licensed reference reader (attribution in the
same anchor; license verified before reading). The page framing, the
spec judgment calls and the reader API live in :mod:`hydroformats.klein`,
which re-exports everything public here.

The SDF page header is one C structure shared by every towfish family
(the spec's own words: "The header is ostensibly the same for each page
structure but the data portion of a data page is unique"), so one
record class carries it: :class:`KleinPageHeader`. Byte offsets in the
field tables are computed from the spec's packed typedef; the computed
sizes land exactly on the documented 44-word (176 byte), 64-word
(256 byte) and 128-word (512 byte) boundaries, and every double sits
8-aligned, so the typedef admits no compiler padding.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from .records import Record

# --------------------------------------------------------------------------
# header field tables (spec section 2.1 typedef; units per the UDP
# companion document, anchor S14)
# --------------------------------------------------------------------------

# The 44-word base header every page version carries (bytes 0-175).
BASE_FIELDS = (
    ("number_bytes", 0, "<I"),
    ("page_version", 4, "<I"),
    ("configuration", 8, "<I"),
    ("ping_number", 12, "<I"),
    ("num_samples", 16, "<I"),
    ("beams_to_display", 20, "<I"),
    ("error_flags", 24, "<I"),
    ("range_m", 28, "<I"),
    ("speed_fish_cms", 32, "<I"),
    ("speed_sound_cms", 36, "<I"),
    ("res_mode", 40, "<I"),
    ("tx_waveform", 44, "<I"),
    ("resp_div", 48, "<I"),
    ("resp_freq", 52, "<I"),
    ("manual_speed_switch", 56, "<I"),
    ("despeckle_switch", 60, "<I"),
    ("speed_filter_switch", 64, "<I"),
    ("year", 68, "<I"),
    ("month", 72, "<I"),
    ("day", 76, "<I"),
    ("hour", 80, "<I"),
    ("minute", 84, "<I"),
    ("second", 88, "<I"),
    ("h_second", 92, "<I"),
    ("fix_time_hour", 96, "<I"),
    ("fix_time_minute", 100, "<I"),
    ("fix_time_second", 104, "<f"),
    ("heading_degrees", 108, "<f"),
    ("pitch_degrees", 112, "<f"),
    ("roll_degrees", 116, "<f"),
    ("depth_m", 120, "<f"),
    ("altitude_m", 124, "<f"),
    ("temperature_c", 128, "<f"),
    ("ship_speed_mps", 132, "<f"),
    ("ship_heading_degrees", 136, "<f"),
    ("magnetic_variation_degrees", 140, "<f"),
    ("ship_lat_radians", 144, "<d"),
    ("ship_lon_radians", 152, "<d"),
    ("fish_lat_radians", 160, "<d"),
    ("fish_lon_radians", 168, "<d"),
)

# The 20 words added at header version 3 (bytes 176-255).
V3_FIELDS = (
    ("tvg_page", 176, "<I"),
    ("header_size", 180, "<I"),
    ("fix_time_year", 184, "<I"),
    ("fix_time_month", 188, "<I"),
    ("fix_time_day", 192, "<I"),
    ("aux_pitch_degrees", 196, "<f"),
    ("aux_roll_degrees", 200, "<f"),
    ("aux_depth_m", 204, "<f"),
    ("aux_altitude_m", 208, "<f"),
    ("cable_out_m", 212, "<f"),
    ("fseconds", 216, "<f"),
    ("altimeter", 220, "<I"),
    ("sample_freq_hz", 224, "<I"),
    ("depressor_type", 228, "<I"),
    ("cable_type", 232, "<I"),
    ("shieve_x_off_m", 236, "<f"),
    ("shieve_y_off_m", 240, "<f"),
    ("shieve_z_off_m", 244, "<f"),
    ("gps_height_m", 248, "<f"),
    ("raw_data_config", 252, "<I"),
)

# The named words of the version 4 extension (bytes 256-335); the
# remainder to 512 is the spec's reserved3[44], reachable through
# header_bytes. wingAngle is read as a float, the typedef's own type
# (the UDP field table prints U32; see the module docstring of
# :mod:`hydroformats.klein`).
V4_FIELDS = (
    ("header3_extension_size", 256, "<I"),
    ("sbp_tx_waveform", 260, "<I"),
    ("sbp_preamp_gain", 264, "<I"),
    ("sbp_data_raw", 268, "<I"),
    ("sbp_num_samples", 272, "<I"),
    ("sbp_sample_freq_hz", 276, "<I"),
    ("sbp_tx_waveform_version", 280, "<I"),
    ("wing_angle_degrees", 284, "<f"),
    ("emergency_switch_state", 288, "<I"),
    ("layback_method", 292, "<I"),
    ("layback_fish_lat_radians", 296, "<d"),
    ("layback_fish_lon_radians", 304, "<d"),
    ("fish_heading_offset_degrees", 312, "<f"),
    ("pressure_sensor_offset_psi", 316, "<f"),
    ("tpu_sw_version", 320, "<I"),
    ("capability_mask", 324, "<I"),
    ("tx_version", 328, "<I"),
    ("num_samples_extra", 332, "<I"),
)

BASE_HEADER_SIZE = 176   # 44 words, header versions below 3
V3_HEADER_SIZE = 256     # 64 words, header version 3
V4_HEADER_SIZE = 512     # 128 words, header version 4

# 3500-series pages carry a center frequency word (kilohertz) at byte
# 404, inside what the 2008 spec revision still called reserved3: the
# MIT reference reader (built on spec revision 4.8) reads it there.
FREQUENCY_3500_OFFSET = 404

_TOWFISH = {
    3000: "System 3000", 3001: "System 3000",
    5000: "System 5000", 5001: "System 5000",
    7000: "System 7000", 7001: "System 7000",
    3501: "3500 series", 3502: "3500 series",
}
_V4_VERSIONS = frozenset({3001, 5001, 7001, 3501, 3502})
_3500_VERSIONS = frozenset({3501, 3502})

# Data page header "configuration" word coarse masks for the System
# 3000 family (spec Table 2): each mask covers both channels of a side
# scan band, so presence tests use a bitwise AND, never equality.
CONFIG_LF_SIDE_SCAN = 0x03
CONFIG_HF_SIDE_SCAN = 0x0C
CONFIG_SBP = 0x10


def towfish_name(page_version: int) -> str | None:
    """Towfish family for a page version, None for an unknown one."""
    return _TOWFISH.get(page_version)


def header_fields(payload: bytes, page_version: int) -> dict:
    """Every header scalar keyed by field name, plus ``header_bytes``.

    Version 4 fields are None on version 3 pages;
    ``center_frequency_khz`` is None outside the 3500 series.
    """
    values: dict = {name: struct.unpack_from(fmt, payload, offset)[0]
                    for name, offset, fmt in BASE_FIELDS + V3_FIELDS}
    v4 = page_version in _V4_VERSIONS
    for name, offset, fmt in V4_FIELDS:
        values[name] = (struct.unpack_from(fmt, payload, offset)[0]
                        if v4 else None)
    values["center_frequency_khz"] = (
        struct.unpack_from("<I", payload, FREQUENCY_3500_OFFSET)[0]
        if page_version in _3500_VERSIONS else None)
    size = V4_HEADER_SIZE if v4 else V3_HEADER_SIZE
    values["header_bytes"] = payload[:size]
    return values


# --------------------------------------------------------------------------
# channel plans (spec section 2.2 data structures)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelSpec:
    """One declared data channel array: its typedef name, the width of
    its leading sample-count word, the width of one sample, and the
    sample signedness. The spec's rule is a 2-byte count before each
    channel; the exceptions (the System 3000 SBP channel's 4-byte
    count, the 3500 series' 4-byte counts) are covered in
    :mod:`hydroformats.klein`."""

    name: str
    count_width: int
    sample_width: int
    signed: bool


def _u16_chan(name: str) -> ChannelSpec:
    return ChannelSpec(name=name, count_width=2, sample_width=2, signed=False)


def _s16_chan(name: str) -> ChannelSpec:
    return ChannelSpec(name=name, count_width=2, sample_width=2, signed=True)


def _plan_3000(sbp_width: int) -> tuple[ChannelSpec, ...]:
    """System 3000 channel order (spec section 2.2.1): the four side
    scan channels, then the sub-bottom profiler with its 4-byte count
    (spec section 2.2's stated exception) and version-dependent sample
    width (signed 16-bit at header version 3, signed 32-bit at 4)."""
    sidescan = tuple(_u16_chan(name)
                     for name in ("port_lf", "stbd_lf", "port_hf", "stbd_hf"))
    return sidescan + (ChannelSpec(name="sbp", count_width=4,
                                   sample_width=sbp_width, signed=True),)


def _plan_5000() -> tuple[ChannelSpec, ...]:
    """System 5000 channel order (spec section 2.2.2): ten processed
    side scan beams (unsigned), then the signed bathymetry I/Q pairs,
    echo sounders, sub-bottom, motion sensors and the 56 raw element
    I/Q arrays. Which of the ten beams are port and which starboard is
    not publicly documented, so the typedef names are surfaced as-is."""
    beams = tuple(_u16_chan(f"chan{i}") for i in range(1, 11))
    bathy = tuple(_s16_chan(f"bathy_{side}{i}{part}")
                  for side in ("port", "stbd")
                  for i in (1, 2, 3) for part in ("i", "q"))
    scalars = tuple(_s16_chan(name)
                    for name in ("echo1", "echo2", "sub_bottom1",
                                 "sub_bottom2", "roll_sensor", "yaw_rate"))
    raw = tuple(_s16_chan(f"rawdata_{side}{i}{part}")
                for side in ("port", "stbd")
                for i in range(1, 15) for part in ("i", "q"))
    return beams + bathy + scalars + raw


# 3500-series pages: one port and one starboard array, each a 4-byte
# count followed by unsigned 32-bit samples (anchored to the MIT
# reference reader, not the 2008 spec revision, which predates them).
_PLAN_3500 = (
    ChannelSpec(name="port", count_width=4, sample_width=4, signed=False),
    ChannelSpec(name="starboard", count_width=4, sample_width=4, signed=False),
)

_PLAN_5000 = _plan_5000()

CHANNEL_PLANS: dict[int, tuple[ChannelSpec, ...]] = {
    3000: _plan_3000(2),
    3001: _plan_3000(4),
    5000: _PLAN_5000,
    5001: _PLAN_5000,
    3501: _PLAN_3500,
    3502: _PLAN_3500,
}


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KleinChannel:
    """One count-prefixed channel array from a data page.

    ``count`` is the array's own leading sample count, the value to
    trust for sizing (the header's numSamples can lag it by
    num_samples_extra on chirp waveforms). ``sample_bytes`` is the raw
    sample data verbatim; :meth:`values` decodes on demand per the
    declared width and signedness.
    """

    name: str
    count: int
    sample_width: int
    signed: bool
    sample_bytes: bytes

    def values(self) -> tuple[int, ...]:
        """The samples decoded little endian per the channel's spec."""
        codes = {2: "h", 4: "i"} if self.signed else {2: "H", 4: "I"}
        code = codes[self.sample_width]
        n = len(self.sample_bytes) // self.sample_width
        return struct.unpack(f"<{n}{code}",
                             self.sample_bytes[:n * self.sample_width])


@dataclass(frozen=True)
class KleinPageHeader(Record):
    """The SDF data page header (spec section 2.1, one typedef for all
    towfish families): per-ping time, settings, navigation and
    attitude, ahead of the channel data.

    Field notes, per the UDP companion document unless stated:

    - Latitudes and longitudes (``ship_*``, ``fish_*`` and the layback
      pair) are **radians**; the ``*_degrees`` properties convert.
      Headings, pitch and roll are degrees.
    - ``speed_fish_cms`` and ``speed_sound_cms`` are centimeters per
      second where ``ship_speed_mps`` is meters per second: three
      different speed units live in one header.
    - ``range_m`` is the sonar range setting in whole meters;
      ``sample_freq_hz`` the receiver sample rate.
    - ``resp_freq`` selects the acoustic responder (tracking beacon)
      frequency, not the sonar frequency: the 3000/5000 header carries
      no sonar frequency field at all. 3500-series pages do carry
      ``center_frequency_khz``.
    - Two fractional-second fields exist: ``h_second`` (hundredths,
      base header) and ``fseconds`` (float seconds, version 3 area);
      :attr:`time_of_day` uses ``fseconds``, the field the reference
      reader uses.
    - ``fix_time_*`` stamp the last GPS update, so nav latency is
      recoverable per ping.
    - Version 4 fields (SBP settings, wing angle, layback, TPU
      version words) are None on version 3 pages. The reserved words
      that pad the version 4 header to 512 bytes remain reachable
      through ``header_bytes``.
    - On 3500-series pages the reference reader takes vehicle
      heading from ``ship_heading_degrees``, pitch and roll from
      ``aux_pitch_degrees``/``aux_roll_degrees``, position from
      ``ship_lat_radians``/``ship_lon_radians`` and depth and
      altitude from ``aux_depth_m``/``aux_altitude_m``: an AUV
      integration routing vehicle nav into the ship and aux slots.
      The fields are surfaced under their spec names, none is
      renamed.
    """

    page_version: int
    number_bytes: int
    configuration: int
    ping_number: int
    num_samples: int
    beams_to_display: int
    error_flags: int
    range_m: int
    speed_fish_cms: int
    speed_sound_cms: int
    res_mode: int
    tx_waveform: int
    resp_div: int
    resp_freq: int
    manual_speed_switch: int
    despeckle_switch: int
    speed_filter_switch: int
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    h_second: int
    fix_time_hour: int
    fix_time_minute: int
    fix_time_second: float
    heading_degrees: float
    pitch_degrees: float
    roll_degrees: float
    depth_m: float
    altitude_m: float
    temperature_c: float
    ship_speed_mps: float
    ship_heading_degrees: float
    magnetic_variation_degrees: float
    ship_lat_radians: float
    ship_lon_radians: float
    fish_lat_radians: float
    fish_lon_radians: float
    tvg_page: int
    header_size: int
    fix_time_year: int
    fix_time_month: int
    fix_time_day: int
    aux_pitch_degrees: float
    aux_roll_degrees: float
    aux_depth_m: float
    aux_altitude_m: float
    cable_out_m: float
    fseconds: float
    altimeter: int
    sample_freq_hz: int
    depressor_type: int
    cable_type: int
    shieve_x_off_m: float
    shieve_y_off_m: float
    shieve_z_off_m: float
    gps_height_m: float
    raw_data_config: int
    header3_extension_size: int | None
    sbp_tx_waveform: int | None
    sbp_preamp_gain: int | None
    sbp_data_raw: int | None
    sbp_num_samples: int | None
    sbp_sample_freq_hz: int | None
    sbp_tx_waveform_version: int | None
    wing_angle_degrees: float | None
    emergency_switch_state: int | None
    layback_method: int | None
    layback_fish_lat_radians: float | None
    layback_fish_lon_radians: float | None
    fish_heading_offset_degrees: float | None
    pressure_sensor_offset_psi: float | None
    tpu_sw_version: int | None
    capability_mask: int | None
    tx_version: int | None
    num_samples_extra: int | None
    center_frequency_khz: int | None
    header_bytes: bytes

    @property
    def towfish(self) -> str | None:
        """Towfish family named by the page version, None if unknown."""
        return towfish_name(self.page_version)

    @property
    def header_version(self) -> int:
        """4 for the 512-byte header pages, 3 for the 256-byte ones."""
        return 4 if self.page_version in _V4_VERSIONS else 3

    @property
    def time_of_day(self) -> float:
        """Seconds past midnight of the ping's calendar date (the
        convention this library's other dialects use), using the float
        ``fseconds`` fraction."""
        return (self.hour * 3600 + self.minute * 60 + self.second
                + self.fseconds)

    @property
    def speed_fish_mps(self) -> float:
        return self.speed_fish_cms / 100.0

    @property
    def speed_sound_mps(self) -> float:
        return self.speed_sound_cms / 100.0

    @property
    def ship_lat_degrees(self) -> float:
        return math.degrees(self.ship_lat_radians)

    @property
    def ship_lon_degrees(self) -> float:
        return math.degrees(self.ship_lon_radians)

    @property
    def fish_lat_degrees(self) -> float:
        return math.degrees(self.fish_lat_radians)

    @property
    def fish_lon_degrees(self) -> float:
        return math.degrees(self.fish_lon_radians)

    @property
    def layback_fish_lat_degrees(self) -> float | None:
        if self.layback_fish_lat_radians is None:
            return None
        return math.degrees(self.layback_fish_lat_radians)

    @property
    def layback_fish_lon_degrees(self) -> float | None:
        if self.layback_fish_lon_radians is None:
            return None
        return math.degrees(self.layback_fish_lon_radians)

    @property
    def lf_side_scan_present(self) -> bool:
        """Low frequency side scan data present (System 3000 family
        configuration semantics, spec Table 2)."""
        return bool(self.configuration & CONFIG_LF_SIDE_SCAN)

    @property
    def hf_side_scan_present(self) -> bool:
        """High frequency side scan data present (System 3000 family
        configuration semantics, spec Table 2)."""
        return bool(self.configuration & CONFIG_HF_SIDE_SCAN)

    @property
    def sbp_present(self) -> bool:
        """Sub-bottom profiler data present (System 3000 family
        configuration semantics, spec Table 2)."""
        return bool(self.configuration & CONFIG_SBP)


@dataclass(frozen=True)
class KleinPing(KleinPageHeader):
    """One decoded sidescan data page (page versions 3000/3001,
    5000/5001, 3501/3502): the shared header plus the count-prefixed
    channel arrays in typedef order.

    ``channels`` holds every array present in the data region;
    ``absent_channels`` names the declared arrays the region ended
    before (the spec never states whether an unconfigured channel is
    written as a zero count or omitted, so both shapes decode: a zero
    count appears as an empty channel, an omission as an absent name).
    ``leftover`` carries any bytes past the last decodable array
    verbatim, channel arrays a newer spec revision may have appended
    included.
    """

    channels: tuple[KleinChannel, ...]
    absent_channels: tuple[str, ...]
    leftover: bytes

    def channel(self, name: str) -> KleinChannel | None:
        """The named channel array, None when absent from the page."""
        for one in self.channels:
            if one.name == name:
                return one
        return None


@dataclass(frozen=True)
class Klein7000Page(KleinPageHeader):
    """A System 7000 data page (page versions 7000/7001): the shared
    header decoded, the data region verbatim.

    The spec's own words for the System 7000 channel structure are
    "tentatively defined", so this library deliberately does not decode
    it: ``data_bytes`` carries the region untouched for a consumer who
    can pin the layout against a real System 7000 file.
    """

    data_bytes: bytes
