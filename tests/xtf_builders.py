"""Hand-assembled XTF byte builders shared by tests/test_xtf.py.

Every builder packs its structure field by field from the tables of the
Triton XTF specification, revision 41 (see hydroformats/xtf.py for the
citation), never via the parser under test; all values are fictional.
"""
import struct


def put(buffer: bytearray, offset: int, fmt: str, *values) -> None:
    struct.pack_into(fmt, buffer, offset, *values)


def chan_info(
    type_of_channel: int = 1,
    sub_channel: int = 0,
    correction_flags: int = 1,
    unipolar: int = 1,
    bytes_per_sample: int = 1,
    reserved_samples: int = 1024,
    name: bytes = b"Port 500",
    volt_scale: float = 5.0,
    frequency: float = 500.0,
    horizontal_beam_angle: float = 1.0,
    tilt_angle: float = 30.0,
    beam_width: float = 50.0,
    offsets: tuple = (0.25, -1.5, 0.75),
    rotations: tuple = (0.0, -2.0, 1.0),
    beams_per_array: int = 0,
    sample_format: int = 0,
) -> bytes:
    """One 128-byte CHANINFO structure (spec Table D)."""
    info = bytearray(128)
    put(info, 0, "<2B", type_of_channel, sub_channel)
    put(info, 2, "<3H", correction_flags, unipolar, bytes_per_sample)
    put(info, 8, "<I", reserved_samples)
    info[12:12 + len(name)] = name
    put(info, 28, "<5f", volt_scale, frequency, horizontal_beam_angle,
        tilt_angle, beam_width)
    put(info, 48, "<3f", *offsets)
    put(info, 60, "<3f", *rotations)
    put(info, 72, "<H", beams_per_array)
    put(info, 74, "<B", sample_format)
    return bytes(info)


def file_header(
    *chan_infos: bytes,
    bathy_channels: int = 0,
    nav_units: int = 3,
    sonar_type: int = 57,
    program: bytes = b"Isis",
    version: bytes = b"556",
    sonar_name: bytes = b"C31_SERV",
    note: bytes = b"synthetic reef line",
    file_name: bytes = b"LINE12-B.XTF",
    nav_latency_ms: int = 120,
    reference_height: float = 1.25,
) -> bytes:
    """The XTFFILEHEADER (spec Table C), grown in 1024-byte steps when the
    declared channels need more than the six built-in CHANINFO slots."""
    n_chan = len(chan_infos)
    size = 1024
    if n_chan > 6:
        size += 1024 * ((n_chan - 6 + 7) // 8)
    head = bytearray(size)
    put(head, 0, "<2B", 123, 1)
    head[2:2 + len(program)] = program
    head[10:10 + len(version)] = version
    head[18:18 + len(sonar_name)] = sonar_name
    put(head, 34, "<H", sonar_type)
    head[36:36 + len(note)] = note
    head[100:100 + len(file_name)] = file_name
    put(head, 164, "<3H", nav_units, n_chan - bathy_channels, bathy_channels)
    put(head, 170, "<2B", 0, 0)
    put(head, 172, "<H", 0)
    put(head, 174, "<B", 0)
    put(head, 178, "<f", reference_height)
    put(head, 204, "<i", nav_latency_ms)
    put(head, 216, "<4f", -1.5, 2.5, 0.5, 0.0)         # nav offsets Y X Z yaw
    put(head, 232, "<6f", 0.1, 0.2, 0.3, 0.0, 0.5, -0.5)  # MRU offsets
    for index, info in enumerate(chan_infos):
        head[256 + 128 * index:256 + 128 * index + 128] = info
    return bytes(head)


def prefix(buffer: bytearray, header_type: int, sub_channel: int = 0,
           num_chans: int = 0, num_bytes: int | None = None) -> None:
    """The 14-byte packet prefix every XTF packet starts with: magic
    0xFACE, header type, sub-channel, channels to follow, two reserved
    words, then the total packet byte count."""
    put(buffer, 0, "<HBBH", 0xFACE, header_type, sub_channel, num_chans)
    put(buffer, 10, "<I", num_bytes if num_bytes is not None else len(buffer))


def ping_header(
    header_type: int = 0,
    num_chans: int = 0,
    num_bytes: int | None = None,
    year: int = 2016,
    month: int = 9,
    day: int = 16,
    hour: int = 13,
    minute: int = 45,
    second: int = 30,
    hseconds: int = 25,
    julian_day: int = 260,
    event_number: int = 7,
    ping_number: int = 12345,
    sound_velocity: float = 750.0,
    ocean_tide: float = 0.4,
    ship_speed: float = 4.2,
    ship_gyro: float = 87.5,
    ship_y: float = 44.6512345,
    ship_x: float = -63.5734567,
    ship_altitude_dm: int = 15,
    ship_depth_dm: int = 32,
    sensor_speed: float = 3.9,
    sensor_y: float = 44.6512001,
    sensor_x: float = -63.5735002,
    range_to_fish_dm: int = 150,
    bearing_to_fish_cdeg: int = 18050,
    cable_out: int = 42,
    layback: float = 38.5,
    sensor_depth: float = 2.5,
    sensor_primary_altitude: float = 14.75,
    sensor_aux_altitude: float = 15.0,
    sensor_pitch: float = 1.5,
    sensor_roll: float = -0.75,
    sensor_heading: float = 88.25,
    heave: float = 0.12,
    yaw: float = 0.5,
    attitude_time_tag: int = 123456,
    fish_delta_x: int = -300,
    fish_delta_y: int = 150,
    cable_out_hundredths: int = 25,
) -> bytes:
    """One 256-byte XTFPINGHEADER (spec Table H); channel blocks append."""
    head = bytearray(256)
    prefix(head, header_type, num_chans=num_chans, num_bytes=num_bytes)
    put(head, 14, "<H", year)
    put(head, 16, "<6B", month, day, hour, minute, second, hseconds)
    put(head, 22, "<H", julian_day)
    put(head, 24, "<2I", event_number, ping_number)
    put(head, 32, "<2f", sound_velocity, ocean_tide)
    put(head, 44, "<8f", 2.5, 3.5, 4.5, 5.5, 3.1, 12.5, 14.7, 1481.0)
    put(head, 76, "<3f", 410.0, 415.0, 420.0)          # magnetometer mgauss
    put(head, 88, "<6f", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)  # aux values
    put(head, 112, "<2f", 3.8, 0.2)                    # speed log, turbidity
    put(head, 120, "<2f", ship_speed, ship_gyro)
    put(head, 128, "<2d", ship_y, ship_x)
    put(head, 144, "<2H", ship_altitude_dm, ship_depth_dm)
    put(head, 148, "<4B", 13, 45, 29, 75)              # fix time
    put(head, 152, "<2f", sensor_speed, 1.25)          # sensor speed, KP
    put(head, 160, "<2d", sensor_y, sensor_x)
    put(head, 176, "<4H", 3, range_to_fish_dm, bearing_to_fish_cdeg, cable_out)
    put(head, 184, "<2f", layback, 120.0)              # layback, cable tension
    put(head, 192, "<6f", sensor_depth, sensor_primary_altitude,
        sensor_aux_altitude, sensor_pitch, sensor_roll, sensor_heading)
    put(head, 216, "<2f", heave, yaw)
    put(head, 224, "<I", attitude_time_tag)
    put(head, 228, "<f", 3.25)                         # distance off track
    put(head, 232, "<I", 123400)                       # nav fix milliseconds
    put(head, 240, "<2h", fish_delta_x, fish_delta_y)
    put(head, 244, "<B", 0)
    put(head, 245, "<I", 0)                            # optional offset
    put(head, 249, "<B", cable_out_hundredths)
    return bytes(head)


def chan_header(
    channel_number: int = 0,
    downsample_method: int = 4,
    slant_range: float = 50.0,
    ground_range: float = 48.0,
    time_delay: float = 0.0,
    time_duration: float = 0.0667,
    seconds_per_ping: float = 0.0667,
    processing_flags: int = 0,
    frequency: int = 500,
    initial_gain: int = 12,
    gain: int = 20,
    bandwidth: int = 2,
    num_samples: int = 4,
    millivolt_scale: int = 0,
    fixed_vsop: float = 10.5,
    weight_factor: int = 2,
) -> bytes:
    """One 64-byte XTFPINGCHANHEADER (spec Table I)."""
    head = bytearray(64)
    put(head, 0, "<2H", channel_number, downsample_method)
    put(head, 4, "<5f", slant_range, ground_range, time_delay, time_duration,
        seconds_per_ping)
    put(head, 24, "<5H", processing_flags, frequency, initial_gain, gain,
        bandwidth)
    put(head, 42, "<I", num_samples)
    put(head, 46, "<H", millivolt_scale)
    put(head, 54, "<f", fixed_vsop)
    put(head, 58, "<h", weight_factor)
    return bytes(head)


def sonar_packet(*channels: bytes, header_type: int = 0, pad: int = 0,
                 num_chans: int | None = None, **fields) -> bytes:
    body = b"".join(channels) + b"\x00" * pad
    head = ping_header(
        header_type=header_type,
        num_chans=len(channels) if num_chans is None else num_chans,
        num_bytes=256 + len(body), **fields,
    )
    return head + body


def attitude_packet(
    pitch: float = 1.25,
    roll: float = -2.5,
    heave: float = 0.31,
    yaw: float = 0.75,
    heading: float = 91.5,
    time_tag: int = 98765,
    epoch_microseconds: int = 250000,
    source_epoch: int = 1474033530,
    year: int = 2016,
    month: int = 9,
    day: int = 16,
    milliseconds: int = 250,
) -> bytes:
    """One 64-byte XTFATTITUDEDATA packet (spec Table E)."""
    packet = bytearray(64)
    prefix(packet, 3)
    put(packet, 22, "<2I", epoch_microseconds, source_epoch)
    put(packet, 30, "<3f", pitch, roll, heave)
    put(packet, 42, "<f", yaw)
    put(packet, 46, "<I", time_tag)
    put(packet, 50, "<f", heading)
    put(packet, 54, "<H", year)
    put(packet, 56, "<5B", month, day, 13, 45, 30)
    put(packet, 61, "<H", milliseconds)
    return bytes(packet)


def notes_packet(text: bytes = b"line 12 start, calm seas",
                 sub_channel: int = 0) -> bytes:
    """One 256-byte XTFNOTESHEADER packet (spec Table F)."""
    packet = bytearray(256)
    prefix(packet, 1, sub_channel=sub_channel)
    put(packet, 14, "<H", 2016)
    put(packet, 16, "<5B", 9, 16, 13, 44, 55)
    packet[56:56 + len(text)] = text
    return bytes(packet)


def raw_serial_packet(text: bytes = b"$GPGGA,134530.00,4439.074,N*42",
                      serial_port: int = 2) -> bytes:
    """One XTFRAWSERIALHEADER packet (spec Table G), padded to 64 bytes."""
    size = max(64, 30 + len(text))
    packet = bytearray(size)
    prefix(packet, 6, sub_channel=serial_port)
    put(packet, 14, "<H", 2016)
    put(packet, 16, "<6B", 9, 16, 13, 45, 30, 25)
    put(packet, 22, "<H", 260)
    put(packet, 24, "<I", 123450)
    put(packet, 28, "<H", len(text))
    packet[30:30 + len(text)] = text
    return bytes(packet)


def snp0(beam_count: int = 2, ping_number: int = 4242) -> bytes:
    """One 74-byte SNP0 structure (spec Table N)."""
    block = bytearray(74)
    put(block, 0, "<I", 0x534E5030)
    put(block, 4, "<2H", 74, 0)
    put(block, 8, "<3I", ping_number, 1474033530, 250)
    put(block, 20, "<H", 15)                            # latency ms
    put(block, 22, "<2H", 0x1234, 0x5678)               # sonar id
    put(block, 26, "<5H", 8101, 455, 1500, 34482, 5000)
    put(block, 36, "<2H", 75, 8)                        # range, power
    put(block, 40, "<4H", 20, 120, 30, 60)
    put(block, 48, "<2H", 0, 300)
    put(block, 52, "<2H", 3, 2)                         # beam spacing 1.5 deg
    put(block, 56, "<h", -450)                          # projector angle
    put(block, 58, "<5H", 1, 100, 2, 90, 3)
    put(block, 68, "<2B", 0, 0)
    put(block, 70, "<h", 251)                           # head temp, 0.1 C
    put(block, 72, "<H", beam_count)
    return bytes(block)


def snp1(beam: int, fragment: bytes, ping_number: int = 4242) -> bytes:
    """One 24-byte SNP1 structure (spec Table O) plus its fragment bytes."""
    block = bytearray(24)
    put(block, 0, "<I", 0x534E5031)
    put(block, 4, "<2H", 24, len(fragment))
    put(block, 8, "<I", ping_number)
    put(block, 12, "<2H", beam, len(fragment) // 2)
    put(block, 16, "<2H", 500, 800)                     # gain, 0.01 dB steps
    put(block, 20, "<2H", 10, len(fragment) // 2)
    return bytes(block) + fragment


def snippet_packet(*beams: bytes, beam_count: int | None = None) -> bytes:
    body = snp0(beam_count=beam_count if beam_count is not None
                else len(beams)) + b"".join(beams)
    return sonar_packet(body, header_type=19, num_chans=0)


def bathy_packet(payload: bytes = b"\x01\x02\x03\x04vendor") -> bytes:
    return sonar_packet(payload, header_type=2, num_chans=0)


def unknown_packet(header_type: int = 200, size: int = 64) -> bytes:
    packet = bytearray(size)
    prefix(packet, header_type)
    return bytes(packet)


def stream(*parts: bytes) -> bytes:
    return b"".join(parts)


TWO_CHANNELS = (
    chan_info(type_of_channel=1, sub_channel=0, name=b"Port 500"),
    chan_info(type_of_channel=2, sub_channel=1, name=b"Stbd 500"),
)
