"""Sound Metrics DDF reader: ARIS (.aris, DDF v5) and DIDSON (.ddf, DDF v3).

These are imaging sonars, not bathymetry sounders: an acoustic camera.
Where a multibeam echosounder reduces each ping to one depth per beam,
an acoustic camera keeps everything each beam heard. Every frame is a
two-dimensional image: one column per beam (a fixed fan of narrow
acoustic beams spread across a lens, beam 0 rightmost), one row per
range sample (echo strength recorded at fixed time steps as the ping's
sound travels out and back). A pixel is one unsigned byte of echo
strength at one beam direction and one range. Because the vertical axis
is time of flight, converting a row index to meters needs the sound
speed and the sampling clock; those live in the headers, and this module
surfaces them next to every frame.

The container is plain: one file header at offset zero, then frames
back to back, each a fixed-size frame header followed by the image bytes
(beam count times samples per beam of them, range row by range row, the
nearest row first). The file header's leading version word doubles as a
format signature, the ASCII bytes ``DDF`` plus a version byte: v5 is an
ARIS recording (1024-byte file and frame headers), v3 an original DIDSON
recording (512-byte file header, 256-byte frame headers). There are no
per-frame sizes, sync markers or checksums; the lattice is implied by
the geometry, so a broken frame ends the walk with a
:class:`~hydroformats.records.MalformedRecord`, never an exception.

Layouts are translated from the MIT-licensed reference definitions in
Sound Metrics' ARIS File SDK (anchor S8 in docs/FORMAT-SOURCES.md),
copyright (c) 2015 Sound Metrics, used under the MIT license:

- https://github.com/SoundMetrics/aris-file-sdk: FileHeader.h and
  FrameHeader.h (field offsets and comments), FrameFuncs.c (the ping
  mode to beam count table, :func:`beam_count_for_ping_mode`), and
  docs/understanding-aris-data.md (container layout, sample ordering,
  window range formulas).
- https://support.echoview.com/WebHelp/Reference/File_Formats/DIDSON_data_files.htm:
  the DDF v3 header sizes and the v3 range decode (the window start code
  times a per-sonar delay period, :func:`didson_delay_period`).

Readings the sources leave open are documented in the relevant docstring
and summarized here:

- The v3 file header shares the v5 field layout through its 512 bytes.
  The SDK states the ARIS header preserved the legacy DIDSON parameters
  for backward compatibility, and the real v3 clips read correctly under
  the shared layout (date text, beam and sample counts, gain, serial,
  the 1457 m/s sound speed word). Same for the v3 frame header against
  the first 256 bytes of the v5 frame header, with three proven
  deviations: the window start and length words are integer codes (u32)
  where v5 stores meters (f32); the two words v5 uses for the PC
  timestamp hold the calendar year and month, extending the v5 day
  through hundredth-second fields into a full calendar clock; and the
  64-bit sonar time counts whole seconds, not microseconds.
- The v3 calendar clock is the frame clock. The whole-second sonar time
  word rolls late against the calendar fields in the S8 clips (the
  hundredths belong to the calendar clock, not to it), so ordering and
  timing should come from year through hsecond;
  :attr:`DidsonFrame.time_of_day` is the format-wide seconds-past-
  midnight convention used by this library's other dialects.
- ARIS frames store window start and length floats, but the S8 sample
  proves they can carry a nominal sound speed baked in by the writer
  rather than the frame's own calculated one. The stored floats are
  surfaced verbatim; :attr:`ArisFrame.derived_window_start_m` and
  :attr:`ArisFrame.derived_window_length_m` re-derive both from the
  sampling settings and the frame's sound speed per the SDK formulas,
  and are the self-consistent values (see the anchor errata for S8).
- Frame sizes come from the first frame's own header (v5) or the file
  header (v3): the SDK pins frames as uniform within a file and tells
  readers to trust frame headers over the writer-populated file header.
  When a v5 ping mode is unknown to the beam table the file header's
  beam count is the fallback; a mid-file frame declaring different
  geometry is decoded on the established lattice and counted by
  :func:`load_imaging`, never trusted for walking.

Unknown and reserved header regions ride along verbatim in each record's
``header_bytes``. Truncation is tolerated everywhere: a partial trailing
frame degrades to a MalformedRecord and is counted, never raised.
"""
from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .records import MalformedRecord, Record

ARIS_SIGNATURE = 0x05464444  # b"DDF\x05" little endian
DIDSON_V3_SIGNATURE = 0x03464444  # b"DDF\x03" little endian

_V5_FILE_HEADER_SIZE = 1024
_V5_FRAME_HEADER_SIZE = 1024
_V3_FILE_HEADER_SIZE = 512
_V3_FRAME_HEADER_SIZE = 256

# System type word values (SDK FrameHeader.h, TheSystemType).
_SYSTEM_MODELS = {0: "ARIS 1800", 1: "ARIS 3000", 2: "ARIS 1200"}


def beam_count_for_ping_mode(ping_mode: int) -> int:
    """Beams per frame for an ARIS ping mode; 0 for a mode outside 1..12.

    Translated from ``get_beams_from_pingmode`` in the SDK's FrameFuncs.c
    (which also returns 0 for unknown modes): modes 1-2 are 48 beams,
    3-5 are 96, 6-8 are 64 and 9-12 are 128. The mode selects how the
    transducer's elements are multiplexed, which fixes how many beam
    directions each frame resolves.
    """
    if ping_mode in (1, 2):
        return 48
    if ping_mode in (3, 4, 5):
        return 96
    if ping_mode in (6, 7, 8):
        return 64
    if ping_mode in (9, 10, 11, 12):
        return 128
    return 0


def didson_delay_period(high_resolution: int, serial_number: int) -> float:
    """Seconds of transmit-to-sampling delay per DIDSON window start step.

    A v3 window start code counts these units; the value depends on the
    sonar's frequency mode and (as a hardware revision proxy) its serial
    number. Table per Echoview's DIDSON format description: 0.001024 s
    (low resolution, serial 18 or below), 0.001144 s (low resolution,
    above 18), 0.000512 s and 0.000572 s for the high resolution column.
    """
    if high_resolution:
        return 0.000512 if serial_number <= 18 else 0.000572
    return 0.001024 if serial_number <= 18 else 0.001144


# --------------------------------------------------------------------------
# typed records (offsets per the SDK header enums; anchor S8)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DdfFileHeader(Record):
    """The one file header at offset zero (1024 bytes v5, 512 bytes v3).

    The layout is shared between versions (see the module docstring for
    the v3 reading) except the window words at offsets 32 and 36: v5
    stores meters as floats (``window_start_m``/``window_length_m``), v3
    stores the DIDSON integer codes (``window_start_code``/
    ``window_length_code``); the variant not present is None. The SDK
    marks most of this header obsolete for ARIS in favor of the frame
    headers, and its own sample file leaves much of it zero, so treat
    the per-frame values as authoritative for v5; for v3 this header is
    load-bearing (it carries the only beam count, sample count, sample
    rate and sound speed the file has). ``sound_speed_code`` is the
    legacy integer sound speed in m/s. ``frame_count`` is
    writer-populated; the SDK tells readers to derive the count from the
    file size instead, and :func:`load_imaging` counts what it walks.
    """

    version: int
    frame_count: int
    frame_rate: int
    high_resolution: int
    num_raw_beams: int
    sample_rate_hz: float
    samples_per_channel: int
    receiver_gain: int
    window_start_code: int | None
    window_length_code: int | None
    window_start_m: float | None
    window_length_m: float | None
    reverse: int
    serial_number: int
    date_text: str
    header_id_text: str
    user_ids: tuple[int, int, int, int]
    start_frame: int
    end_frame: int
    time_lapse: int
    record_interval: int
    radio_seconds: int
    frame_interval: int
    flags: int
    aux_flags: int
    sound_speed_code: int
    flags_3d: int
    software_version: int
    water_temp_code: int
    salinity_code: int
    large_lens: int
    header_bytes: bytes

    @property
    def is_aris(self) -> bool:
        """True for a DDF v5 (ARIS) file, False for DDF v3 (DIDSON)."""
        return self.version == ARIS_SIGNATURE


@dataclass(frozen=True)
class DdfFrame(Record):
    """Base of both frame records: identity, image geometry, image bytes.

    ``samples`` is the frame's image, one unsigned byte of echo strength
    per pixel, range row by range row with the nearest row first; within
    a row, one byte per beam with beam 0 rightmost (SDK sample ordering
    note). ``beam_count`` and ``samples_per_beam`` describe the declared
    geometry; when a malformed frame declares geometry that disagrees
    with the file's lattice the samples keep the lattice size and the
    mismatch is counted by :func:`load_imaging`. ``header_bytes`` is the
    frame header verbatim, so fields this module does not name remain
    reachable. ``version`` is the per-frame signature word, expected to
    equal the file's magic; mismatches are surfaced and counted, never
    raised.
    """

    frame_index: int
    version: int
    status: int
    beam_count: int
    samples_per_beam: int
    samples: bytes
    header_bytes: bytes

    def rows(self) -> tuple[bytes, ...]:
        """The image as range rows, nearest first, one byte per beam.

        Slices ``samples`` into ``beam_count``-wide rows; a short or
        oversized buffer yields only the complete rows, never raises.
        """
        width = self.beam_count
        if width <= 0:
            return ()
        count = len(self.samples) // width
        return tuple(
            self.samples[i * width:(i + 1) * width] for i in range(count)
        )

    def beam_profile(self, beam: int) -> bytes:
        """One beam's echo strength by range, nearest sample first."""
        if not 0 <= beam < self.beam_count:
            raise ValueError(
                f"beam {beam} outside 0..{self.beam_count - 1}")
        return self.samples[beam::self.beam_count]


@dataclass(frozen=True)
class ArisFrame(DdfFrame):
    """One ARIS (DDF v5) frame: image plus the settings that shaped it.

    Times are microseconds since the Unix epoch on two clocks: the
    sonar's (``frame_time_us``) and the recording PC's (``pc_time_us``).
    The sonar settings are the geometry facts an image consumer needs:
    ``sample_start_delay_us`` (transmit to first sample),
    ``sample_period_us`` (per range row), ``samples_per_beam``,
    ``sound_speed_mps`` (calculated by the sonar from temperature, depth
    and salinity), ``ping_mode`` (fixing ``beam_count``),
    ``frequency_hi_low`` (1 high frequency, 0 low), ``receiver_gain``
    (dB) and ``large_lens`` (telephoto fitted). ``window_start_m`` and
    ``window_length_m`` are the header's own floats verbatim; prefer the
    derived properties, which apply the SDK formulas to this frame's
    settings (the stored floats can carry a writer default sound speed;
    see the module docstring and the S8 anchor errata).

    Attitude and position, where the platform provided them: the sonar's
    own compass (``compass_heading``/``compass_pitch``/``compass_roll``,
    degrees), platform motion from AUV integration (``platform_*``), and
    an auxiliary GPS fix (``latitude``/``longitude``); zeros where no
    sensor fed them. ``water_temp_c`` is the housing sensor in Celsius.
    ``reordered_samples`` nonzero means the image is already in
    [beam, sample] order, which recorded files are; zero appears only in
    live integration streams.
    """

    frame_time_us: int
    pc_time_us: int
    transmit_mode: int
    window_start_m: float
    window_length_m: float
    threshold: int
    intensity: int
    receiver_gain: int
    platform_velocity: float
    platform_depth: float
    platform_altitude: float
    platform_pitch: float
    platform_roll: float
    platform_heading: float
    compass_heading: float
    compass_pitch: float
    compass_roll: float
    latitude: float
    longitude: float
    water_temp_c: float
    sample_rate_hz: float
    ping_mode: int
    frequency_hi_low: int
    pulse_width_us: int
    cycle_period_us: int
    sample_period_us: int
    transmit_enable: int
    frame_rate_hz: float
    sound_speed_mps: float
    sample_start_delay_us: int
    large_lens: int
    system_type: int
    sonar_serial_number: int
    reordered_samples: int
    salinity: int

    @property
    def time(self) -> float:
        """Sonar clock, seconds since the epoch."""
        return self.frame_time_us / 1e6

    @property
    def pc_time(self) -> float:
        """Recording PC clock, seconds since the epoch."""
        return self.pc_time_us / 1e6

    @property
    def is_high_frequency(self) -> bool:
        return bool(self.frequency_hi_low)

    @property
    def system_model(self) -> str | None:
        """Model name for the system type word, None when unknown."""
        return _SYSTEM_MODELS.get(self.system_type)

    @property
    def derived_window_start_m(self) -> float:
        """Range of the first sample per the SDK formula: the sampling
        delay is a two-way travel time, so half of delay times sound
        speed."""
        return self.sample_start_delay_us * 1e-6 * self.sound_speed_mps / 2.0

    @property
    def derived_window_length_m(self) -> float:
        """Downrange extent of the image per the SDK formula: sample
        period times samples per beam is the listening time, halved for
        the two-way path."""
        return (self.sample_period_us * self.samples_per_beam * 1e-6
                * self.sound_speed_mps / 2.0)

    @property
    def sample_spacing_m(self) -> float:
        """Meters between adjacent range rows (one sample period out and
        back)."""
        return self.sample_period_us * 1e-6 * self.sound_speed_mps / 2.0


@dataclass(frozen=True)
class DidsonFrame(DdfFrame):
    """One DIDSON (DDF v3) frame: image plus the legacy header fields.

    The frame clock is the calendar fields, ``year`` through ``hsecond``
    (hundredths), on the sonar's local time; ``sonar_time_s`` is the
    whole-second epoch counter, which rolls late against the calendar
    fields in the S8 clips and should not be combined with them (module
    docstring). ``window_start_code`` and ``window_length_code`` are the
    DIDSON integer window codes. Geometry does not live in v3 frame
    headers, so ``beam_count``, ``samples_per_beam``,
    ``sample_rate_hz``, ``sound_speed_mps`` (the file header's legacy
    integer sound speed) and ``delay_period_s`` (the
    :func:`didson_delay_period` value for this sonar) are carried in
    from the file header to make every frame self-sufficient; the
    ``window_*_m`` properties decode the codes through them. The
    environment and compass fields follow the shared legacy layout:
    ``deg_c1``/``deg_c2`` (electronics temperatures, Celsius),
    ``humidity`` (percent), ``focus`` and ``battery`` (raw units),
    compass heading/pitch/roll (degrees), an auxiliary GPS fix, and
    ``timer_period`` (the frame cycle; milliseconds in the S8 clips,
    where it matches the file header frame rate).
    """

    sonar_time_s: int
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    hsecond: int
    transmit_mode: int
    window_start_code: int
    window_length_code: int
    threshold: int
    intensity: int
    receiver_gain: int
    deg_c1: int
    deg_c2: int
    humidity: int
    focus: int
    battery: int
    compass_heading: float
    compass_pitch: float
    compass_roll: float
    latitude: float
    longitude: float
    timer_period: int
    delay_period_s: float
    sound_speed_mps: float
    sample_rate_hz: float

    @property
    def time_of_day(self) -> float:
        """Seconds past midnight of the calendar date (the convention
        this library's other dialects use for time tags)."""
        return (self.hour * 3600 + self.minute * 60 + self.second
                + self.hsecond / 100.0)

    @property
    def window_start_m(self) -> float:
        """Range of the first sample: the start code counts delay
        periods of two-way travel (Echoview's v3 range decode)."""
        return (self.window_start_code * self.delay_period_s
                * self.sound_speed_mps / 2.0)

    @property
    def window_length_m(self) -> float | None:
        """Downrange extent of the image; None when the file header's
        sample rate is unusable (zero or negative)."""
        if self.sample_rate_hz <= 0:
            return None
        return (self.samples_per_beam * self.sound_speed_mps
                / (2.0 * self.sample_rate_hz))

    @property
    def sample_spacing_m(self) -> float | None:
        """Meters between adjacent range rows; None when the sample rate
        is unusable."""
        if self.sample_rate_hz <= 0:
            return None
        return self.sound_speed_mps / (2.0 * self.sample_rate_hz)


# --------------------------------------------------------------------------
# header field tables (name, offset, format), per the SDK offset enums
# --------------------------------------------------------------------------

# Shared file header words; the window words at 32/36 are version-typed.
_FILE_FIELDS = (
    ("frame_count", 4, "<I"),
    ("frame_rate", 8, "<I"),
    ("high_resolution", 12, "<I"),
    ("num_raw_beams", 16, "<I"),
    ("sample_rate_hz", 20, "<f"),
    ("samples_per_channel", 24, "<I"),
    ("receiver_gain", 28, "<I"),
    ("reverse", 40, "<I"),
    ("serial_number", 44, "<I"),
    ("start_frame", 352, "<I"),
    ("end_frame", 356, "<I"),
    ("time_lapse", 360, "<I"),
    ("record_interval", 364, "<I"),
    ("radio_seconds", 368, "<I"),
    ("frame_interval", 372, "<I"),
    ("flags", 376, "<I"),
    ("aux_flags", 380, "<I"),
    ("sound_speed_code", 384, "<I"),
    ("flags_3d", 388, "<I"),
    ("software_version", 392, "<I"),
    ("water_temp_code", 396, "<I"),
    ("salinity_code", 400, "<I"),
)

_V5_FRAME_FIELDS = (
    ("frame_index", 0, "<I"),
    ("frame_time_us", 4, "<Q"),
    ("version", 12, "<I"),
    ("status", 16, "<I"),
    ("pc_time_us", 20, "<Q"),
    ("transmit_mode", 48, "<I"),
    ("window_start_m", 52, "<f"),
    ("window_length_m", 56, "<f"),
    ("threshold", 60, "<I"),
    ("intensity", 64, "<i"),
    ("receiver_gain", 68, "<I"),
    ("platform_velocity", 124, "<f"),
    ("platform_depth", 128, "<f"),
    ("platform_altitude", 132, "<f"),
    ("platform_pitch", 136, "<f"),
    ("platform_roll", 144, "<f"),
    ("platform_heading", 152, "<f"),
    ("compass_heading", 160, "<f"),
    ("compass_pitch", 164, "<f"),
    ("compass_roll", 168, "<f"),
    ("latitude", 172, "<d"),
    ("longitude", 180, "<d"),
    ("water_temp_c", 224, "<f"),
    ("sample_rate_hz", 420, "<f"),
    ("ping_mode", 436, "<I"),
    ("frequency_hi_low", 440, "<I"),
    ("pulse_width_us", 444, "<I"),
    ("cycle_period_us", 448, "<I"),
    ("sample_period_us", 452, "<I"),
    ("transmit_enable", 456, "<I"),
    ("frame_rate_hz", 460, "<f"),
    ("sound_speed_mps", 464, "<f"),
    ("samples_per_beam", 468, "<I"),
    ("sample_start_delay_us", 476, "<I"),
    ("large_lens", 480, "<I"),
    ("system_type", 484, "<I"),
    ("sonar_serial_number", 488, "<I"),
    ("reordered_samples", 516, "<I"),
    ("salinity", 520, "<I"),
)

_V3_FRAME_FIELDS = (
    ("frame_index", 0, "<I"),
    ("sonar_time_s", 4, "<Q"),
    ("version", 12, "<I"),
    ("status", 16, "<I"),
    ("year", 20, "<I"),
    ("month", 24, "<I"),
    ("day", 28, "<I"),
    ("hour", 32, "<I"),
    ("minute", 36, "<I"),
    ("second", 40, "<I"),
    ("hsecond", 44, "<I"),
    ("transmit_mode", 48, "<I"),
    ("window_start_code", 52, "<I"),
    ("window_length_code", 56, "<I"),
    ("threshold", 60, "<I"),
    ("intensity", 64, "<i"),
    ("receiver_gain", 68, "<I"),
    ("deg_c1", 72, "<I"),
    ("deg_c2", 76, "<I"),
    ("humidity", 80, "<I"),
    ("focus", 84, "<I"),
    ("battery", 88, "<I"),
    ("compass_heading", 160, "<f"),
    ("compass_pitch", 164, "<f"),
    ("compass_roll", 168, "<f"),
    ("latitude", 172, "<d"),
    ("longitude", 180, "<d"),
    ("timer_period", 228, "<I"),
)


def _fields(buffer: bytes, table: tuple[tuple[str, int, str], ...]) -> dict:
    return {
        name: struct.unpack_from(fmt, buffer, offset)[0]
        for name, offset, fmt in table
    }


def _text(raw: bytes) -> str:
    """Text up to the first NUL, latin-1 (never raises), stripped."""
    return raw.split(b"\x00", 1)[0].decode("latin-1").strip()


# --------------------------------------------------------------------------
# walking the frame lattice
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Gap:
    """Bytes that could not be framed; read_aris renders these as
    MalformedRecord, load_imaging counts them."""

    tag: str
    offset: int
    size: int
    error: str


def _read_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def _parse_file_header(data: bytes) -> DdfFileHeader | _Gap:
    if len(data) < 4:
        return _Gap(tag="HDR", offset=0, size=len(data),
                    error=f"no version word in {len(data)} bytes")
    (magic,) = struct.unpack_from("<I", data, 0)
    if magic == ARIS_SIGNATURE:
        size = _V5_FILE_HEADER_SIZE
    elif magic == DIDSON_V3_SIGNATURE:
        size = _V3_FILE_HEADER_SIZE
    else:
        return _Gap(tag="HDR", offset=0, size=len(data),
                    error=f"unrecognized version word 0x{magic:08X}")
    if len(data) < size:
        return _Gap(tag="HDR", offset=0, size=len(data),
                    error=f"file header needs {size} bytes, got {len(data)}")
    header = data[:size]
    values = _fields(header, _FILE_FIELDS)
    if magic == ARIS_SIGNATURE:
        start_m, length_m = struct.unpack_from("<2f", header, 32)
        window: dict = {"window_start_m": start_m, "window_length_m": length_m,
                        "window_start_code": None, "window_length_code": None}
    else:
        start_code, length_code = struct.unpack_from("<2I", header, 32)
        window = {"window_start_m": None, "window_length_m": None,
                  "window_start_code": start_code,
                  "window_length_code": length_code}
    (large_lens,) = struct.unpack_from("<I", header, 452)
    return DdfFileHeader(
        tag="HDR", version=magic, date_text=_text(header[48:80]),
        header_id_text=_text(header[80:336]),
        user_ids=struct.unpack_from("<4i", header, 336),
        large_lens=large_lens, header_bytes=header, **window, **values,
    )


def _truncation_gap(tag: str, offset: int, size: int, needed: int) -> _Gap:
    return _Gap(tag=tag, offset=offset, size=size,
                error=f"truncated frame: {needed} bytes needed, {size} remain")


def _make_v5_frame(header: bytes, samples: bytes, lattice_beams: int) -> ArisFrame:
    values = _fields(header, _V5_FRAME_FIELDS)
    beams = beam_count_for_ping_mode(values["ping_mode"]) or lattice_beams
    return ArisFrame(tag="FRAME", beam_count=beams, samples=samples,
                     header_bytes=header, **values)


def _walk_v5(data: bytes, header: DdfFileHeader) -> Iterator[ArisFrame | _Gap]:
    n = len(data)
    position = _V5_FILE_HEADER_SIZE
    if position + _V5_FRAME_HEADER_SIZE > n:
        yield _truncation_gap("FRAME", position, n - position,
                              _V5_FRAME_HEADER_SIZE)
        return
    first = _fields(data[position:position + _V5_FRAME_HEADER_SIZE],
                    _V5_FRAME_FIELDS)
    beams = beam_count_for_ping_mode(first["ping_mode"]) or header.num_raw_beams
    cell = beams * first["samples_per_beam"]
    if cell <= 0:
        yield _Gap(tag="FRAME", offset=position, size=n - position,
                   error=f"cannot size frames: ping mode {first['ping_mode']}, "
                         f"{beams} beams, "
                         f"{first['samples_per_beam']} samples per beam")
        return
    frame_size = _V5_FRAME_HEADER_SIZE + cell
    while position + frame_size <= n:
        head = data[position:position + _V5_FRAME_HEADER_SIZE]
        samples = data[position + _V5_FRAME_HEADER_SIZE:position + frame_size]
        yield _make_v5_frame(head, samples, beams)
        position += frame_size
    if position < n:
        yield _truncation_gap("FRAME", position, n - position, frame_size)


def _walk_v3(data: bytes, header: DdfFileHeader) -> Iterator[DidsonFrame | _Gap]:
    n = len(data)
    position = _V3_FILE_HEADER_SIZE
    cell = header.num_raw_beams * header.samples_per_channel
    if cell <= 0:
        if position < n:
            yield _Gap(tag="FRAME", offset=position, size=n - position,
                       error=f"cannot size frames: {header.num_raw_beams} "
                             f"beams, {header.samples_per_channel} samples "
                             f"per channel")
        return
    frame_size = _V3_FRAME_HEADER_SIZE + cell
    delay = didson_delay_period(header.high_resolution, header.serial_number)
    while position + frame_size <= n:
        head = data[position:position + _V3_FRAME_HEADER_SIZE]
        samples = data[position + _V3_FRAME_HEADER_SIZE:position + frame_size]
        yield DidsonFrame(
            tag="FRAME", beam_count=header.num_raw_beams,
            samples_per_beam=header.samples_per_channel, samples=samples,
            header_bytes=head, delay_period_s=delay,
            sound_speed_mps=float(header.sound_speed_code),
            sample_rate_hz=header.sample_rate_hz,
            **_fields(head, _V3_FRAME_FIELDS),
        )
        position += frame_size
    if position < n:
        yield _truncation_gap("FRAME", position, n - position, frame_size)


def _walk(source: str | Path | bytes) -> Iterator[Record | _Gap]:
    data = _read_bytes(source)
    header = _parse_file_header(data)
    yield header
    if isinstance(header, _Gap):
        return
    if header.version == ARIS_SIGNATURE:
        yield from _walk_v5(data, header)
    else:
        yield from _walk_v3(data, header)


def read_aris(source: str | Path | bytes) -> Iterator[Record]:
    """Stream typed records from a DDF file (path or bytes), in file
    order: the :class:`DdfFileHeader`, then one :class:`ArisFrame` (v5)
    or :class:`DidsonFrame` (v3) per frame. Bytes that cannot be framed
    (a bad or truncated file header, an unsizable first frame, a partial
    trailing frame) yield :class:`~hydroformats.records.MalformedRecord`
    and end the walk; there is nothing to resynchronize on in a lattice
    with no sync markers. Never raises on content.
    """
    for event in _walk(source):
        if isinstance(event, _Gap):
            yield MalformedRecord(
                tag=event.tag,
                fields=(f"offset={event.offset}",
                        f"bytes_remaining={event.size}"),
                error=event.error,
            )
        else:
            yield event


# --------------------------------------------------------------------------
# imaging loading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DdfCounters:
    """Stream accounting from one :func:`load_imaging` pass.

    ``frames`` counts every complete frame decoded.
    ``signature_mismatches`` counts frames whose per-frame version word
    is not the file's magic (the SDK's corruption check); such frames
    are still decoded and kept. ``geometry_mismatches`` counts frames
    whose declared beam count times samples per beam disagrees with the
    image size the lattice actually carries. ``malformed`` counts the
    records dropped for unframeable bytes, whose size is
    ``bytes_skipped``.
    """

    frames: int
    malformed: int
    signature_mismatches: int
    geometry_mismatches: int
    bytes_skipped: int


@dataclass(frozen=True)
class DdfImaging:
    """One materialized DDF recording: the file header, every frame in
    file order (all :class:`ArisFrame` or all :class:`DidsonFrame`), and
    the stream counters. ``file_header`` is None when the input is not a
    DDF file at all; the malformed record is dropped here but counted
    (use :func:`read_aris` to see it)."""

    file_header: DdfFileHeader | None
    frames: tuple[ArisFrame | DidsonFrame, ...]
    counters: DdfCounters


def load_imaging(source: str | Path | bytes) -> DdfImaging:
    """Materialize a whole DDF recording (small files, tests).

    Every frame keeps its raw image bytes and the settings needed to
    place each pixel in meters (beam count, samples per beam, window
    start and length, sound speed), so downstream imaging never has to
    reopen the file.
    """
    file_header: DdfFileHeader | None = None
    frames: list[ArisFrame | DidsonFrame] = []
    malformed = mismatched_signatures = mismatched_geometry = skipped = 0
    expected_signature = 0
    for event in _walk(source):
        if isinstance(event, _Gap):
            malformed += 1
            skipped += event.size
        elif isinstance(event, DdfFileHeader):
            file_header = event
            expected_signature = event.version
        else:
            frames.append(event)
            if event.version != expected_signature:
                mismatched_signatures += 1
            if event.beam_count * event.samples_per_beam != len(event.samples):
                mismatched_geometry += 1
    return DdfImaging(
        file_header=file_header, frames=tuple(frames),
        counters=DdfCounters(
            frames=len(frames), malformed=malformed,
            signature_mismatches=mismatched_signatures,
            geometry_mismatches=mismatched_geometry,
            bytes_skipped=skipped,
        ),
    )
