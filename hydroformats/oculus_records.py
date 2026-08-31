"""Typed records for the Blueprint Oculus dialect (see hydroformats/oculus.py).

An Oculus is a multibeam imaging sonar, an acoustic camera: each ping
insonifies a fan of water and the sonar reports what every beam heard at
every range step. One ping therefore decodes to a two-dimensional image,
one row per range line (nearest first) and one column per beam, each
cell an echo-strength sample of one, two, three or four bytes. Unlike a
fixed-lens acoustic camera, the beam directions are not implied by the
geometry: every ping carries its own bearing table, one signed
hundredth-of-a-degree entry per beam, so the fan's aperture is data,
not a model constant.

Field layouts follow the sources cited in docs/FORMAT-SOURCES.md anchor
S13 and are byte-verified against real recordings (a CC0 ViewPoint
survey and the liboculus raw fixtures); byte offsets quoted in
docstrings are into the network message, little endian throughout.
Sizes quoted are pinned by ``test_docstring_layout_sizes``.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .records import Record

# DataSizeType: bytes per image sample (0 = 8 bit through 3 = 32 bit).
_BYTES_PER_SAMPLE = {0: 1, 1: 2, 2: 3, 3: 4}


def bytes_per_sample(data_size: int) -> int | None:
    """Bytes per image sample for a DataSizeType word, None if unknown.

    The enum counts upward from 8-bit data: 0 is one byte, 1 two bytes,
    2 three bytes, 3 four bytes. Anything else is not a defined size.
    """
    return _BYTES_PER_SAMPLE.get(data_size)


@dataclass(frozen=True)
class OculusFileHeader(Record):
    """The one log-file header at offset zero (48 bytes).

    ViewPoint's log container opens with a magic word (0x11223344), the
    header's own byte size (``size_header``, 48 in every observed file,
    honored when larger so grown headers skip cleanly), a 16-byte
    source tag (the text "Oculus"), a format version word, an
    encryption word and key (0 in every observed file; nonzero values
    are refused loudly, nothing here decrypts), and the recording start
    time as a double of seconds since the Unix epoch.
    ``header_bytes`` carries the header verbatim.
    """

    magic: int
    size_header: int
    source_text: str
    version: int
    encryption: int
    key: int
    time: float
    header_bytes: bytes


@dataclass(frozen=True)
class OculusPing(Record):
    """One SimplePingResult message: an imaging ping with its settings.

    The message embeds the fire message that provoked it (the settings
    the topside asked for), then the sonar's own report of what it
    actually did, then the bearing table and the image.

    Identity and clocks: ``log_time`` is the log item header's double,
    seconds since the Unix epoch stamped by the recording PC; it is the
    reliable wall clock (None when the ping came from a bare message
    stream, which has no container clock). ``ping_id`` increments per
    ping.
    ``ping_start_time_s`` (version 2) is the sonar's own clock in
    seconds since power-up, proven against the log clock on the real
    capture (both tick at the ping interval); on version 1 messages
    the sonar clock is the raw 32-bit word ``ping_start_word``
    instead, carried verbatim: the one source that interprets it
    (as a 4-byte float of seconds since power-up) is contradicted by
    the real capture, where the values are junk as floats but tick
    uniformly as an unsigned counter (see the anchor errata).
    ``message_version`` is the header's msgVersion word verbatim: 2
    selects the version 2 layout, anything else (0 on the real
    version 1 capture) the version 1 layout.

    Fire settings (the request): ``master_mode`` selects the frequency
    (1 the low-frequency wide-fan mode, 2 the high-frequency mode; the
    S13 sources call 0 a factory "flexi" mode unavailable to third
    parties), ``range_setting`` is the demanded range, in meters when
    ``range_is_meters`` (flags bit 0) else as a percentage of the
    sonar's maximum (the version 2 sources name the field
    rangePercent; the flag still governs, and both real captures set
    it with meters that match ``n_ranges`` times the resolution),
    ``gain_percent`` the receiver gain, ``speed_of_sound_mps`` the
    demanded sound speed (0 means let the sonar calculate it from
    ``salinity_ppt``, parts per thousand, 0 fresh water, 35 typical
    sea water), ``gamma_correction`` the display gamma word (a
    display-shaping request, not image calibration; scaling per the
    S13 sources). ``flags`` rides verbatim next to
    the decoded per-bit properties (:attr:`range_is_meters` bit 0,
    :attr:`is_16bit_data` bit 1, :attr:`sends_gain` bit 2,
    :attr:`is_simple_return` bit 3, :attr:`gain_assistance` bit 4,
    :attr:`wants_512_beams` bit 6). ``ping_rate_raw`` and
    ``network_speed_raw`` are carried raw; the rate byte is never
    mapped to the demanded-rate enum, whose numeric values no
    permissive source publishes, and real captures put fill bytes
    there anyway (0xC3 and 0xA5 observed; see the anchor errata).
    ``ext_flags`` and ``fire_reserved`` are the version 2 extension
    words verbatim (bit 0x200 of ``ext_flags`` demands 32-bit data
    per the S13 sources; 0xA5 fill bytes observed in the reserved
    words), None on version 1.

    The sonar's report: ``frequency_hz`` is the acoustic frequency
    actually used, ``temperature_c`` the water temperature in Celsius
    and ``pressure_bar`` the pressure (version 1 hardware can leave
    both as garbage bit patterns, another anchor erratum),
    ``speed_of_sound_used_mps`` the sound speed applied to range
    decoding, and version 2 adds the sonar's attitude:
    ``heading_deg``, ``pitch_deg``, ``roll_deg`` (None on version 1).
    ``status`` is the device status word verbatim.

    Image geometry: the image is ``n_ranges`` rows of ``n_beams``
    samples, each :attr:`sample_size` bytes (``data_size`` is the raw
    DataSizeType word), row-major with the nearest range line first.
    ``range_resolution_m`` is the meters per range line, so row ``i``
    spans ranges ``i`` to ``i + 1`` times it and the outer edge of the
    image is :attr:`range_m`. ``bearings_raw`` is the bearing table:
    one signed int16 per beam in hundredths of a degree, negative to
    port; :attr:`bearings_deg` decodes it and :attr:`aperture_deg` is
    its span. ``gains`` carries the per-row gain words when the sonar
    sent them (:attr:`sends_gain`), else None. ``samples`` is the
    image with any gain prefixes stripped: exactly
    ``n_ranges * n_beams * sample_size`` bytes. ``image_offset``,
    ``image_size`` and ``message_size`` are the message's own layout
    words verbatim (the bytes between the bearing table and
    ``image_offset`` are unanchored filler, nonzero in real captures,
    and are deliberately not surfaced). ``header_bytes`` is the fixed
    part of the message verbatim (122 bytes version 1, 202 version 2).
    """

    src_device_id: int
    dst_device_id: int
    message_version: int
    log_time: float | None
    ping_id: int
    status: int
    master_mode: int
    ping_rate_raw: int
    network_speed_raw: int
    gamma_correction: int
    flags: int
    range_setting: float
    gain_percent: float
    speed_of_sound_mps: float
    salinity_ppt: float
    ext_flags: int | None
    fire_reserved: tuple[int, ...] | None
    frequency_hz: float
    temperature_c: float
    pressure_bar: float
    heading_deg: float | None
    pitch_deg: float | None
    roll_deg: float | None
    speed_of_sound_used_mps: float
    ping_start_time_s: float | None
    ping_start_word: int | None
    data_size: int
    range_resolution_m: float
    n_ranges: int
    n_beams: int
    image_offset: int
    image_size: int
    message_size: int
    bearings_raw: tuple[int, ...]
    gains: tuple[int, ...] | None
    samples: bytes
    header_bytes: bytes

    @property
    def range_is_meters(self) -> bool:
        """Flags bit 0: the fire range is meters (set) or percent."""
        return bool(self.flags & 0x01)

    @property
    def is_16bit_data(self) -> bool:
        """Flags bit 1: 16-bit image data was demanded (the sonar's
        actual sample size is ``data_size``, which governs decoding)."""
        return bool(self.flags & 0x02)

    @property
    def sends_gain(self) -> bool:
        """Flags bit 2: each image row starts with a 4-byte gain word
        (already split out into ``gains`` here)."""
        return bool(self.flags & 0x04)

    @property
    def is_simple_return(self) -> bool:
        """Flags bit 3: the sonar was asked for the simple return
        message, which every ping this module decodes is."""
        return bool(self.flags & 0x08)

    @property
    def gain_assistance(self) -> bool:
        """Flags bit 4: automatic gain assistance was demanded."""
        return bool(self.flags & 0x10)

    @property
    def wants_512_beams(self) -> bool:
        """Flags bit 6: 512 beams demanded instead of 256 (the actual
        beam count of the image is always ``n_beams``)."""
        return bool(self.flags & 0x40)

    @property
    def sample_size(self) -> int:
        """Bytes per image sample (from ``data_size``; decoding never
        reaches here with an unknown size word)."""
        return _BYTES_PER_SAMPLE[self.data_size]

    @property
    def range_m(self) -> float:
        """Outer edge of the image in meters: every range line spans
        ``range_resolution_m``, and there are ``n_ranges`` of them."""
        return self.n_ranges * self.range_resolution_m

    @property
    def bearings_deg(self) -> tuple[float, ...]:
        """The bearing table in degrees (stored value over 100)."""
        return tuple(raw / 100.0 for raw in self.bearings_raw)

    @property
    def aperture_deg(self) -> float:
        """Angular span of the fan: last bearing minus first, degrees.
        Data-derived per ping; never a model-table lookup."""
        if not self.bearings_raw:
            return 0.0
        return (max(self.bearings_raw) - min(self.bearings_raw)) / 100.0

    @property
    def is_high_frequency(self) -> bool:
        """True in the high-frequency mode (master mode 2)."""
        return self.master_mode == 2

    def rows(self) -> tuple[bytes, ...]:
        """The image as range rows, nearest first, ``n_beams`` samples
        of ``sample_size`` bytes each (gain prefixes already
        stripped)."""
        width = self.n_beams * self.sample_size
        if width <= 0:
            return ()
        count = len(self.samples) // width
        return tuple(
            self.samples[i * width:(i + 1) * width] for i in range(count)
        )

    def row_values(self, row: int) -> tuple[int, ...]:
        """One range row decoded to integers (little endian, unsigned),
        one value per beam."""
        if not 0 <= row < self.n_ranges:
            raise ValueError(f"row {row} outside 0..{self.n_ranges - 1}")
        size = self.sample_size
        width = self.n_beams * size
        raw = self.samples[row * width:(row + 1) * width]
        return tuple(
            int.from_bytes(raw[i:i + size], "little")
            for i in range(0, len(raw), size)
        )

    def beam_values(self, beam: int) -> tuple[int, ...]:
        """One beam's echo strength by range, nearest sample first."""
        if not 0 <= beam < self.n_beams:
            raise ValueError(f"beam {beam} outside 0..{self.n_beams - 1}")
        size = self.sample_size
        width = self.n_beams * size
        return tuple(
            int.from_bytes(self.samples[r * width + beam * size:
                                        r * width + (beam + 1) * size],
                           "little")
            for r in range(len(self.samples) // width)
        )


def unpack_values(fmt: str, buffer: bytes, offset: int = 0) -> tuple:
    """struct.unpack_from with the module's little endian convention."""
    return struct.unpack_from("<" + fmt, buffer, offset)
