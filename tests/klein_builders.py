"""Hand-assembled SDF byte builders shared by tests/test_klein.py.

Every builder packs its structure field by field from the typedef and
tables of the Klein SDF data page specification, document 15300018
Rev 2.05 (see hydroformats/klein.py for the citations), never via the
parser under test; all values are fictional.
"""
import struct

MARKER = b"\xff\xff\xff\xff"


def put(buffer: bytearray, offset: int, fmt: str, *values) -> None:
    struct.pack_into(fmt, buffer, offset, *values)


def header(
    page_version: int = 3001,
    configuration: int = 0x1F,
    ping_number: int = 4242,
    num_samples: int = 4,
    beams_to_display: int = 0x3FF,
    error_flags: int = 0,
    range_m: int = 75,
    speed_fish_cms: int = 250,
    speed_sound_cms: int = 150000,
    res_mode: int = 1,
    tx_waveform: int = 0x0102,
    year: int = 2008,
    month: int = 4,
    day: int = 15,
    hour: int = 13,
    minute: int = 45,
    second: int = 30,
    h_second: int = 25,
    heading: float = 87.5,
    pitch: float = 1.5,
    roll: float = -0.75,
    depth: float = 12.25,
    altitude: float = 9.5,
    temperature: float = 14.5,
    ship_speed: float = 2.25,
    ship_heading: float = 91.25,
    ship_lat: float = 0.77,
    ship_lon: float = -1.11,
    fish_lat: float = 0.775,
    fish_lon: float = -1.115,
    tvg_page: int = 3,
    header_size: int | None = None,
    aux_pitch: float = 0.5,
    aux_roll: float = -0.25,
    aux_depth: float = 11.75,
    aux_altitude: float = 10.25,
    cable_out: float = 150.5,
    fseconds: float = 0.31,
    sample_freq_hz: int = 20000,
    raw_data_config: int = 0x00030003,
    wing_angle: float = 5.5,
    layback_fish_lat: float = 0.78,
    layback_fish_lon: float = -1.12,
    tpu_sw_version: int = 0x06160401,
    capability_mask: int = 0x2C,
    tx_version: int = 1,
    num_samples_extra: int = 16,
    frequency_khz: int = 455,
) -> bytes:
    """One SDF page header (spec section 2.1 typedef): 256 bytes for
    the version 3 page versions (3000/5000/7000), 512 for the version 4
    ones. number_bytes at offset 0 is patched by :func:`page`."""
    v3_only = page_version in (3000, 5000, 7000)
    size = 256 if v3_only else 512
    head = bytearray(size)
    put(head, 4, "<I", page_version)
    put(head, 8, "<3I", configuration, ping_number, num_samples)
    put(head, 20, "<2I", beams_to_display, error_flags)
    put(head, 28, "<3I", range_m, speed_fish_cms, speed_sound_cms)
    put(head, 40, "<2I", res_mode, tx_waveform)
    put(head, 48, "<2I", 5, 7)                       # respDiv, respFreq
    put(head, 56, "<3I", 1, 2, 1)                    # manual/despeckle/filter
    put(head, 68, "<7I", year, month, day, hour, minute, second, h_second)
    put(head, 96, "<2I", 13, 44)                     # fix time hour, minute
    put(head, 104, "<f", 58.5)                       # fix time second
    put(head, 108, "<6f", heading, pitch, roll, depth, altitude, temperature)
    put(head, 132, "<3f", ship_speed, ship_heading, -14.5)
    put(head, 144, "<4d", ship_lat, ship_lon, fish_lat, fish_lon)
    put(head, 176, "<2I", tvg_page,
        size if header_size is None else header_size)
    put(head, 184, "<3I", 2008, 4, 15)               # fix time year/month/day
    put(head, 196, "<4f", aux_pitch, aux_roll, aux_depth, aux_altitude)
    put(head, 212, "<2f", cable_out, fseconds)
    put(head, 220, "<2I", 1, sample_freq_hz)         # altimeter, sample freq
    put(head, 228, "<2I", 1, 3)                      # depressor, cable type
    put(head, 236, "<4f", 1.5, -2.5, 0.75, 3.25)     # sheave offsets, GPS h
    put(head, 252, "<I", raw_data_config)
    if v3_only:
        return bytes(head)
    put(head, 256, "<7I", 256, 2, 1, 0, 3, 10000, 4)  # SBP settings block
    put(head, 284, "<f", wing_angle)
    put(head, 288, "<2I", 1, 0)                       # emergency, layback
    put(head, 296, "<2d", layback_fish_lat, layback_fish_lon)
    put(head, 312, "<2f", 2.5, 1.25)                  # heading/pressure offs
    put(head, 320, "<4I", tpu_sw_version, capability_mask, tx_version,
        num_samples_extra)
    if page_version in (3501, 3502):
        put(head, 404, "<I", frequency_khz)
    return bytes(head)


def channel(*values: int, count_width: int = 2, sample_width: int = 2,
            signed: bool = False, count: int | None = None) -> bytes:
    """One count-prefixed channel array (spec section 2.2)."""
    count_code = "<H" if count_width == 2 else "<I"
    codes = {2: "h", 4: "i"} if signed else {2: "H", 4: "I"}
    n = len(values) if count is None else count
    return struct.pack(count_code, n) + struct.pack(
        f"<{len(values)}{codes[sample_width]}", *values)


def page(head: bytes, *chunks: bytes, number_bytes: int | None = None,
         marker: bytes = MARKER) -> bytes:
    """One marker-led data page: the marker, then the header with its
    number_bytes word patched to cover header plus channel data."""
    data = b"".join(chunks)
    body = bytearray(head + data)
    put(body, 0, "<I",
        len(body) if number_bytes is None else number_bytes)
    return marker + bytes(body)


def ping_3001(*, sbp: bytes | None = None, **fields) -> bytes:
    """A System 3000 header version 4 page: four side scan channels
    and a sub-bottom channel (4-byte count, 32-bit signed samples)."""
    if sbp is None:
        sbp = channel(100, -200, 300, count_width=4, sample_width=4,
                      signed=True)
    return page(
        header(page_version=3001, **fields),
        channel(10, 20, 30, 40),                     # port_lf
        channel(11, 21, 31, 41),                     # stbd_lf
        channel(12, 22, 32, 42),                     # port_hf
        channel(13, 23, 33, 43),                     # stbd_hf
        sbp,
    )


def stream(*parts: bytes) -> bytes:
    return b"".join(parts)
