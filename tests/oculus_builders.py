"""Hand-assembled Oculus byte builders shared by tests/test_oculus.py.

Every builder packs its structure field by field from the S13 offset
tables (see hydroformats/oculus.py for the citations), never via the
parser under test; all values are fictional.
"""
import struct

from hydroformats.oculus import (
    FILE_MAGIC,
    ITEM_MAGIC,
    MESSAGE_SIMPLE_PING_RESULT,
    OCULUS_ID,
    SONAR_ITEM,
    V1_PING_STRUCT_SIZE,
    V2_PING_STRUCT_SIZE,
    bytes_per_sample,
)


def put(buffer: bytearray, offset: int, fmt: str, *values) -> None:
    struct.pack_into(fmt, buffer, offset, *values)


def file_header(
    magic: int = FILE_MAGIC,
    size_header: int = 48,
    source: bytes = b"Oculus",
    version: int = 1,
    encryption: int = 0,
    key: int = 0,
    time: float = 1_756_000_000.5,
) -> bytes:
    head = bytearray(48)
    put(head, 0, "<2I", magic, size_header)
    head[8:8 + len(source)] = source
    put(head, 24, "<2H", version, encryption)
    put(head, 32, "<q", key)
    put(head, 40, "<d", time)
    return bytes(head)


def item_header(
    payload_size: int,
    item_type: int = SONAR_ITEM,
    version: int = 2,
    time: float = 1_756_000_001.25,
    compression: int = 0,
    original_size: int | None = None,
    size_header: int = 40,
    magic: int = ITEM_MAGIC,
) -> bytes:
    head = bytearray(size_header)
    put(head, 0, "<2I", magic, size_header)
    put(head, 8, "<2H", item_type, version)
    put(head, 16, "<d", time)
    put(head, 24, "<H", compression)
    put(head, 28, "<2I",
        payload_size if original_size is None else original_size,
        payload_size)
    return bytes(head)


def msg_header(payload_size: int, msg_id: int = MESSAGE_SIMPLE_PING_RESULT,
               msg_version: int = 0, src: int = 4242, dst: int = 0) -> bytes:
    head = bytearray(16)
    put(head, 0, "<5H", OCULUS_ID, src, dst, msg_id, msg_version)
    put(head, 10, "<I", payload_size)
    return bytes(head)


def _fire(buffer: bytearray, master_mode: int, flags: int, range_setting: float,
          gain: float, sos: float, salinity: float) -> None:
    put(buffer, 16, "<5B", master_mode, 7, 255, 127, flags)
    put(buffer, 21, "<4d", range_setting, gain, sos, salinity)


def image_rows(n_ranges: int, n_beams: int, sample_size: int,
               gain_rows: bool) -> bytes:
    width = n_beams * sample_size
    rows = []
    for row in range(n_ranges):
        prefix = struct.pack("<I", 1000 + row) if gain_rows else b""
        rows.append(prefix + bytes((row * width + i) % 251
                                   for i in range(width)))
    return b"".join(rows)


def v1_ping(
    ping_id: int = 77,
    master_mode: int = 1,
    flags: int = 0x19,
    range_setting: float = 3.0,
    gain: float = 55.0,
    sos: float = 1490.0,
    salinity: float = 0.0,
    frequency: float = 750_000.0,
    temperature: float = 11.5,
    pressure: float = 0.25,
    sos_used: float = 1481.25,
    start_word: int = 123_456_789,
    data_size: int = 0,
    resolution: float = 0.01,
    n_ranges: int = 3,
    n_beams: int = 4,
    bearings: tuple[int, ...] | None = None,
    filler: bytes = b"\xa5" * 10,
    image: bytes | None = None,
    image_size: int | None = None,
    msg_version: int = 0,
) -> bytes:
    sample_size = bytes_per_sample(data_size) or 1
    gain_rows = bool(flags & 0x04)
    body = image if image is not None else image_rows(
        n_ranges, n_beams, sample_size, gain_rows)
    size = len(body) if image_size is None else image_size
    bearing_table = bearings if bearings is not None else tuple(
        -150 + 100 * i for i in range(n_beams))
    image_offset = V1_PING_STRUCT_SIZE + 2 * n_beams + len(filler)
    message_size = image_offset + len(body)
    message = bytearray(V1_PING_STRUCT_SIZE)
    message[:16] = msg_header(message_size - 16, msg_version=msg_version)
    _fire(message, master_mode, flags, range_setting, gain, sos, salinity)
    put(message, 53, "<2I", ping_id, 0)
    put(message, 61, "<4d", frequency, temperature, pressure, sos_used)
    put(message, 93, "<I", start_word)
    put(message, 97, "<B", data_size)
    put(message, 98, "<d", resolution)
    put(message, 106, "<2H", n_ranges, n_beams)
    put(message, 110, "<3I", image_offset, size, message_size)
    return (bytes(message) + struct.pack(f"<{n_beams}h", *bearing_table)
            + filler + body)


def v2_ping(
    ping_id: int = 88,
    master_mode: int = 2,
    flags: int = 0x19,
    range_setting: float = 7.5,
    gain: float = 92.0,
    sos: float = 1500.5,
    salinity: float = 35.0,
    ext_flags: int = 0x200,
    reserved: tuple[int, ...] = (0xA5A5A5A5,) * 8,
    frequency: float = 1_200_000.0,
    temperature: float = 26.5,
    pressure: float = 1.75,
    heading: float = 212.5,
    pitch: float = 8.25,
    roll: float = -3.5,
    sos_used: float = 1500.25,
    start_time: float = 2851.75,
    data_size: int = 0,
    resolution: float = 0.02,
    n_ranges: int = 3,
    n_beams: int = 4,
    bearings: tuple[int, ...] | None = None,
    filler: bytes = b"\xa5" * 10,
    image: bytes | None = None,
    image_size: int | None = None,
) -> bytes:
    sample_size = bytes_per_sample(data_size) or 1
    gain_rows = bool(flags & 0x04)
    body = image if image is not None else image_rows(
        n_ranges, n_beams, sample_size, gain_rows)
    size = len(body) if image_size is None else image_size
    bearing_table = bearings if bearings is not None else tuple(
        -150 + 100 * i for i in range(n_beams))
    image_offset = V2_PING_STRUCT_SIZE + 2 * n_beams + len(filler)
    message_size = image_offset + len(body)
    message = bytearray(V2_PING_STRUCT_SIZE)
    message[:16] = msg_header(message_size - 16, msg_version=2)
    _fire(message, master_mode, flags, range_setting, gain, sos, salinity)
    put(message, 53, "<9I", ext_flags, *reserved)
    put(message, 89, "<2I", ping_id, 0)
    put(message, 97, "<8d", frequency, temperature, pressure, heading,
        pitch, roll, sos_used, start_time)
    put(message, 161, "<B", data_size)
    put(message, 162, "<d", resolution)
    put(message, 170, "<2H", n_ranges, n_beams)
    put(message, 174, "<4I", 0, 0, 0, 0)
    put(message, 190, "<3I", image_offset, size, message_size)
    return (bytes(message) + struct.pack(f"<{n_beams}h", *bearing_table)
            + filler + body)


def container(*payloads: bytes, header: bytes | None = None,
              items: tuple[bytes, ...] | None = None) -> bytes:
    parts = [header if header is not None else file_header()]
    if items is not None:
        parts.extend(items)
    else:
        for index, payload in enumerate(payloads or (v1_ping(),)):
            parts.append(item_header(
                len(payload), time=1_756_000_001.25 + index / 4))
            parts.append(payload)
    return b"".join(parts)
