"""Kongsberg KMALL reader (EM series multibeam logging, read only).

A ``.kmall`` file is the native recording format of the current
Kongsberg EM series multibeam echo sounders (EM 124, EM 304, EM 712,
EM 2040, EM 2042), the successor of the older .all format, logged by
SIS 5 or K-Controller. It is a stream of datagrams, each led by a
20-byte general header (u32 length, a type code of '#' plus three
letters, a per-type version byte, system and echo sounder ids, UTC
time) and closed by a repeated u32 length word; both length words
count the whole datagram, themselves included. Little endian with
4-byte alignment throughout. Water column data is usually logged to a
separate ``.kmwcd`` file of the same structure.

Every layout here is hand-built from the format owner's own
specification, the only source consulted (anchor S11 in
docs/FORMAT-SOURCES.md):

- *EM datagrams on \\*.kmall format*, Kongsberg document 410224
  revision J, 2023-09-15 (revision text J.02, 2025-08-20).
  https://www.kongsbergdiscovery.online/sis/kmall/index.html

No third-party parser code was consulted. Readings the specification
leaves open, and deliberate scope choices, are documented in the
relevant docstring and summarized here:

- The trailing length word is verified against the header length on
  every datagram; a mismatch is reported on the frame and counted,
  never raised.
- The format has no sync marker, but every type code starts with '#'
  (spec overview), so after a broken header the scan resynchronizes
  on the next byte position whose header parses sanely ('#' plus
  three capital letters, a length that fits the file, a matching
  trailing length word candidate is not required).
- Every variable block honors its own declared byte size (the
  numBytes... fields), so fields added by newer revisions are skipped
  faithfully; blocks shorter than the revision J layouts degrade to
  :class:`~hydroformats.records.MalformedRecord`.
- #MRZ datagrams split into partitions (only seen when logging raw
  UDP output directly; SIS merges them) are rejoined per the spec's
  multibeam data logging chapter: every partition repeats the general
  header and partition struct, plus the common body from #MRZ version
  3 (#MWC version 2), and each closes with its own length word. Parts
  are matched on header time, system and echo sounder id, plus ping
  counter and fan index when the common body is present. Incomplete
  sets degrade to MalformedRecord, never block the stream.
- #MWC water column datagrams are deliberately decoded header-only
  (general header, partition, common body): the sample payload is
  routinely gigabytes per survey and out of scope for this reader.
- #SKM delayed heave is read from the last 12 bytes of each sample
  when the declared sample size has room for it (the spec composes
  the sample as the 120-byte KM binary block then the 12-byte delayed
  heave block).
- Sensor text (raw position telegrams, installation and runtime
  parameter blobs) decodes latin-1 with trailing NULs stripped, never
  raises.

Unknown datagram types are skipped tolerantly and counted by type
code in :func:`load_swath`. Raw observables are preserved throughout:
each sounding keeps its two way travel time and beam angle beside the
processed x, y, z, and the seabed image samples stay raw 0.1 dB
integers next to a scaled accessor.
"""
from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from .kmall_records import (
    KmallAttitude,
    KmallAttitudeSample,
    KmallCompatibilityPosition,
    KmallHeave,
    KmallInstallation,
    KmallPing,
    KmallPosition,
    KmallRuntime,
    KmallSounding,
    KmallSvp,
    KmallTxSector,
    KmallWaterColumn,
    sounding_usable,
)
from .records import MalformedRecord, Record

__all__ = [
    "KmallCounters",
    "KmallFrame",
    "KmallGap",
    "KmallSwath",
    "iter_datagrams",
    "load_swath",
    "read_kmall",
    "sounding_usable",
]

MRZ = "#MRZ"
MWC = "#MWC"
SPO = "#SPO"
SKM = "#SKM"
SVP = "#SVP"
IIP = "#IIP"
IOP = "#IOP"
CPO = "#CPO"
CHE = "#CHE"

_TAGS = {
    MRZ: "MRZ", MWC: "MWC", SPO: "SPO", SKM: "SKM", SVP: "SVP",
    IIP: "IIP", IOP: "IOP", CPO: "CPO", CHE: "CHE",
}

_HEADER = struct.Struct("<I4sBBHII")          # 20 bytes, general header
_END = struct.Struct("<I")                    # trailing length word
_PARTITION = struct.Struct("<HH")             # 4 bytes
_MBODY = struct.Struct("<HH8B")               # 12 bytes
_PING_INFO = struct.Struct("<HHf8Bf6f4f2h2BHI3f2Hf2H6f4B2df")  # 144 bytes
_PING_INFO_V1 = struct.Struct("<fBBH")        # 8-byte version 1 tail
_TX_SECTOR = struct.Struct("<4B7f2BH")        # 36 bytes
_TX_SECTOR_V1 = struct.Struct("<3f")          # 12-byte version 1 tail
_RX_INFO = struct.Struct("<4H4f4H")           # 32 bytes
_EXTRA_CLASS = struct.Struct("<HbB")          # 4 bytes
_SOUNDING = struct.Struct("<H8BH6f2Hf7f4f6f4H")  # 120 bytes
_SCOMMON = struct.Struct("<4H")               # 8 bytes
_S_POSITION_BLOCK = struct.Struct("<2If2d3f")  # 40-byte fixed SPO/CPO part
_SKM_INFO = struct.Struct("<H2B4H")           # 12 bytes
_KM_BINARY = struct.Struct("<4sHH3I2df4f3f3f7f3f")  # 120 bytes
_DELAYED_HEAVE = struct.Struct("<2If")        # 12 bytes
_SVP_COMMON = struct.Struct("<HH4sI2d")       # 28 bytes
_SVP_POINT = struct.Struct("<2fI2f")          # 20 bytes
_I_FIXED = struct.Struct("<3H")               # 6 bytes before the text
_CHE_DATA = struct.Struct("<f")

_MIN_DGM = _HEADER.size + _END.size  # 24: empty body


# --------------------------------------------------------------------------
# datagram walking
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KmallFrame:
    """One framed datagram: the general header's fields plus the raw
    body (the bytes between the header and the trailing length word).

    ``end_length_ok`` is True when the trailing u32 repeats the
    header's length, the format's own integrity check; a mismatch is
    reported here and counted by :func:`load_swath`, never raised.
    """

    offset: int
    dgm_type: str
    dgm_version: int
    system_id: int
    echo_sounder_id: int
    time_sec: int
    time_nanosec: int
    payload: bytes
    end_length_ok: bool


@dataclass(frozen=True)
class KmallGap:
    """Bytes outside any well-framed datagram: garbage, a corrupt
    length, or a truncated tail. The scan resynchronizes on the next
    position whose header parses sanely (see :func:`iter_datagrams`)."""

    offset: int
    size: int


def _read_bytes(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source
    return Path(source).read_bytes()


def _header_at(data: bytes, position: int) -> tuple | None:
    """Header fields when a sane datagram starts here, else None.

    Sane: the declared length holds at least an empty body and fits
    the remaining file, and the type code is '#' plus three capital
    letters (the spec's naming rule; there is no dedicated sync
    marker).
    """
    (num_bytes, dgm_type, version, system_id, sounder_id, time_sec,
     time_nanosec) = _HEADER.unpack_from(data, position)
    if num_bytes < _MIN_DGM or num_bytes > len(data) - position:
        return None
    if dgm_type[0] != 0x23 or any(not 65 <= b <= 90 for b in dgm_type[1:]):
        return None
    return (num_bytes, dgm_type, version, system_id, sounder_id, time_sec,
            time_nanosec)


def iter_datagrams(source: str | Path | bytes) -> Iterator[KmallFrame | KmallGap]:
    """Walk the datagram stream; never raises on content.

    Yields :class:`KmallFrame` for every datagram whose header parses
    with a sane size and :class:`KmallGap` for every byte range that
    does not frame, in file order. After a broken header the scan
    resumes at the next candidate '#' type code, so a corrupt length
    cannot swallow the valid datagrams behind it. The trailing length
    word of each frame is verified and reported via
    :attr:`KmallFrame.end_length_ok`.
    """
    data = _read_bytes(source)
    n = len(data)
    position = 0
    gap_start = 0
    while position + _MIN_DGM <= n:
        fields = _header_at(data, position)
        if fields is None:
            # resynchronize: the type code's '#' sits 4 bytes into a
            # datagram, so scan for the next '#' past this candidate's
            found = data.find(b"#", position + 5)
            if found == -1:
                break
            position = found - 4
            continue
        num_bytes = fields[0]
        if position > gap_start:
            yield KmallGap(offset=gap_start, size=position - gap_start)
        (declared_end,) = _END.unpack_from(data, position + num_bytes - 4)
        yield KmallFrame(
            offset=position, dgm_type=fields[1].decode("ascii"),
            dgm_version=fields[2], system_id=fields[3],
            echo_sounder_id=fields[4], time_sec=fields[5],
            time_nanosec=fields[6],
            payload=data[position + _HEADER.size:position + num_bytes - 4],
            end_length_ok=declared_end == num_bytes,
        )
        position = gap_start = position + num_bytes
    if gap_start < n:
        yield KmallGap(offset=gap_start, size=n - gap_start)


# --------------------------------------------------------------------------
# per-type decoders (frame -> Record; layouts per the spec structs, S11)
# --------------------------------------------------------------------------


def _base(frame: KmallFrame, tag: str) -> dict:
    return {
        "tag": tag, "dgm_version": frame.dgm_version,
        "system_id": frame.system_id,
        "echo_sounder_id": frame.echo_sounder_id,
        "time_sec": frame.time_sec, "time_nanosec": frame.time_nanosec,
    }


def _text(raw: bytes) -> str:
    return raw.decode("latin-1").rstrip("\x00")


def _need(payload: bytes, size: int, what: str) -> None:
    if len(payload) < size:
        raise ValueError(f"{what} needs {size} bytes, got {len(payload)}")


def _partition(payload: bytes) -> tuple[int, int]:
    """The 4-byte partition words led by every M datagram body."""
    _need(payload, _PARTITION.size, "partition")
    num_of_dgms, dgm_num = _PARTITION.unpack_from(payload, 0)
    return num_of_dgms, dgm_num


def _mbody(payload: bytes, offset: int) -> tuple[dict, int]:
    """The common multibeam body at ``offset``: its fields and the
    offset just past it (per its own declared size)."""
    _need(payload, offset + _MBODY.size, "common multibeam body")
    values = _MBODY.unpack_from(payload, offset)
    num_bytes = values[0]
    if num_bytes < _MBODY.size or offset + num_bytes > len(payload):
        raise ValueError(f"common body of {num_bytes} bytes cannot hold "
                         f"the 12-byte layout")
    fields = {
        "ping_cnt": values[1], "rx_fans_per_ping": values[2],
        "rx_fan_index": values[3], "swaths_per_ping": values[4],
        "swath_along_position": values[5], "tx_transducer_ind": values[6],
        "rx_transducer_ind": values[7], "num_rx_transducers": values[8],
        "algorithm_type": values[9],
    }
    return fields, offset + num_bytes


def _decode_iip(frame: KmallFrame) -> Record:
    return _decode_text_blob(frame, KmallInstallation, "IIP")


def _decode_iop(frame: KmallFrame) -> Record:
    return _decode_text_blob(frame, KmallRuntime, "IOP")


def _decode_text_blob(frame: KmallFrame, cls: type, tag: str) -> Record:
    """#IIP and #IOP: a 6-byte fixed part then the parameter text.
    The leading numBytesCmnPart covers the fixed part and the text."""
    payload = frame.payload
    _need(payload, _I_FIXED.size, f"#{tag} fixed part")
    num_bytes, info, status = _I_FIXED.unpack_from(payload, 0)
    if num_bytes < _I_FIXED.size:
        raise ValueError(f"#{tag} body of {num_bytes} bytes cannot hold "
                         f"the 6-byte fixed part")
    end = min(num_bytes, len(payload))
    return cls(**_base(frame, tag), info=info, status=status,
               text=_text(payload[_I_FIXED.size:end]))


def _decode_position(frame: KmallFrame) -> Record:
    """#SPO and #CPO share one layout: common sensor part, 40-byte
    fixed block, then the raw sensor telegram as text."""
    tag = _TAGS[frame.dgm_type]
    cls = KmallPosition if frame.dgm_type == SPO else KmallCompatibilityPosition
    payload = frame.payload
    _need(payload, _SCOMMON.size, f"#{tag} common part")
    num_bytes_cmn, sensor_system, sensor_status, _ = _SCOMMON.unpack_from(
        payload, 0)
    if num_bytes_cmn < _SCOMMON.size:
        raise ValueError(f"#{tag} common part of {num_bytes_cmn} bytes "
                         f"cannot hold the 8-byte layout")
    block = num_bytes_cmn
    _need(payload, block + _S_POSITION_BLOCK.size, f"#{tag} data block")
    (sensor_sec, sensor_nanosec, fix_quality, lat, lon, speed, course,
     height) = _S_POSITION_BLOCK.unpack_from(payload, block)
    return cls(
        **_base(frame, tag), sensor_system=sensor_system,
        sensor_status=sensor_status, time_from_sensor_sec=sensor_sec,
        time_from_sensor_nanosec=sensor_nanosec, pos_fix_quality_m=fix_quality,
        corrected_lat_deg=lat, corrected_long_deg=lon,
        speed_over_ground_mps=speed, course_over_ground_deg=course,
        ellipsoid_height_re_ref_point_m=height,
        raw_text=_text(
            payload[block + _S_POSITION_BLOCK.size:]).rstrip("\r\n"),
    )


def _decode_skm(frame: KmallFrame) -> Record:
    payload = frame.payload
    _need(payload, _SKM_INFO.size, "#SKM info part")
    (num_bytes_info, sensor_system, sensor_status, input_format,
     num_samples, bytes_per_sample, contents) = _SKM_INFO.unpack_from(
        payload, 0)
    if num_bytes_info < _SKM_INFO.size or bytes_per_sample < _KM_BINARY.size:
        raise ValueError(
            f"#SKM info of {num_bytes_info} bytes / samples of "
            f"{bytes_per_sample} bytes cannot hold the layouts")
    _need(payload, num_bytes_info + num_samples * bytes_per_sample,
          f"{num_samples} samples of {bytes_per_sample} bytes")
    samples = tuple(
        _skm_sample(payload, num_bytes_info + i * bytes_per_sample,
                    bytes_per_sample)
        for i in range(num_samples)
    )
    return KmallAttitude(
        **_base(frame, "SKM"), sensor_system=sensor_system,
        sensor_status=sensor_status, sensor_input_format=input_format,
        sensor_data_contents=contents, samples=samples,
    )


def _skm_sample(payload: bytes, offset: int,
                bytes_per_sample: int) -> KmallAttitudeSample:
    """One sample: the KM binary block, then the delayed heave block
    read from the sample's last 12 bytes when the declared sample size
    has room for it (the spec composes the sample as KM binary then
    delayed heave; the KM binary block is 120 bytes in revision J)."""
    values = _KM_BINARY.unpack_from(payload, offset)
    delayed: dict = {}
    if bytes_per_sample >= _KM_BINARY.size + _DELAYED_HEAVE.size:
        heave_sec, heave_nanosec, heave_m = _DELAYED_HEAVE.unpack_from(
            payload, offset + bytes_per_sample - _DELAYED_HEAVE.size)
        delayed = {
            "delayed_heave_time_sec": heave_sec,
            "delayed_heave_time_nanosec": heave_nanosec,
            "delayed_heave_m": heave_m,
        }
    return KmallAttitudeSample(
        tag="KMB", time_sec=values[3], time_nanosec=values[4],
        status=values[5], latitude_deg=values[6], longitude_deg=values[7],
        ellipsoid_height_m=values[8], roll_deg=values[9],
        pitch_deg=values[10], heading_deg=values[11], heave_m=values[12],
        roll_rate=values[13], pitch_rate=values[14], yaw_rate=values[15],
        vel_north=values[16], vel_east=values[17], vel_down=values[18],
        latitude_error_m=values[19], longitude_error_m=values[20],
        ellipsoid_height_error_m=values[21], roll_error_deg=values[22],
        pitch_error_deg=values[23], heading_error_deg=values[24],
        heave_error_m=values[25], north_acceleration=values[26],
        east_acceleration=values[27], down_acceleration=values[28],
        **delayed,
    )


def _decode_svp(frame: KmallFrame) -> Record:
    payload = frame.payload
    _need(payload, _SVP_COMMON.size, "#SVP common part")
    (num_bytes_cmn, num_samples, sensor_format, time_sec, lat,
     lon) = _SVP_COMMON.unpack_from(payload, 0)
    if num_bytes_cmn < _SVP_COMMON.size:
        raise ValueError(f"#SVP common part of {num_bytes_cmn} bytes "
                         f"cannot hold the 28-byte layout")
    _need(payload, num_bytes_cmn + num_samples * _SVP_POINT.size,
          f"{num_samples} profile points")
    points = [
        _SVP_POINT.unpack_from(payload, num_bytes_cmn + i * _SVP_POINT.size)
        for i in range(num_samples)
    ]
    return KmallSvp(
        **_base(frame, "SVP"),
        sensor_format=_text(sensor_format),
        profile_time_sec=time_sec, latitude_deg=lat, longitude_deg=lon,
        depths_m=tuple(p[0] for p in points),
        sound_speeds_mps=tuple(p[1] for p in points),
        temperatures_c=tuple(p[3] for p in points),
        salinities=tuple(p[4] for p in points),
    )


def _decode_che(frame: KmallFrame) -> Record:
    fields, offset = _mbody(frame.payload, 0)
    _need(frame.payload, offset + _CHE_DATA.size, "#CHE heave")
    (heave,) = _CHE_DATA.unpack_from(frame.payload, offset)
    return KmallHeave(**_base(frame, "CHE"), **fields, heave_m=heave)


def _decode_mwc(frame: KmallFrame) -> Record:
    """#MWC decodes header-only by design: the water column sample
    payload is deliberately skipped (see the module docstring)."""
    num_of_dgms, dgm_num = _partition(frame.payload)
    fields, _ = _mbody(frame.payload, _PARTITION.size)
    return KmallWaterColumn(
        **_base(frame, "MWC"), **fields,
        num_of_dgms=num_of_dgms, dgm_num=dgm_num,
        num_bytes=len(frame.payload) + _MIN_DGM,
    )


def _decode_mrz(frame: KmallFrame) -> Record:
    payload = frame.payload
    num_of_dgms, _ = _partition(payload)
    fields, offset = _mbody(payload, _PARTITION.size)

    _need(payload, offset + 2, "#MRZ ping info size")
    (info_len,) = struct.unpack_from("<H", payload, offset)
    if info_len < _PING_INFO.size or offset + info_len > len(payload):
        raise ValueError(f"ping info of {info_len} bytes cannot hold the "
                         f"{_PING_INFO.size}-byte layout")
    info = _PING_INFO.unpack_from(payload, offset)
    v1: dict = {}
    if info_len >= _PING_INFO.size + _PING_INFO_V1.size:
        bs_offset, lamberts, ice, active = _PING_INFO_V1.unpack_from(
            payload, offset + _PING_INFO.size)
        v1 = {
            "bs_correction_offset_db": bs_offset,
            "lamberts_law_applied": lamberts, "ice_window": ice,
            "active_modes": active,
        }
    offset += info_len

    num_tx_sectors, bytes_per_sector = info[34], info[35]
    if bytes_per_sector < _TX_SECTOR.size:
        raise ValueError(f"tx sectors of {bytes_per_sector} bytes cannot "
                         f"hold the {_TX_SECTOR.size}-byte layout")
    _need(payload, offset + num_tx_sectors * bytes_per_sector,
          f"{num_tx_sectors} tx sectors")
    sectors = tuple(
        _tx_sector(payload, offset + i * bytes_per_sector, bytes_per_sector)
        for i in range(num_tx_sectors)
    )
    offset += num_tx_sectors * bytes_per_sector

    _need(payload, offset + _RX_INFO.size, "#MRZ rx info")
    rx = _RX_INFO.unpack_from(payload, offset)
    rx_len = rx[0]
    if rx_len < _RX_INFO.size or offset + rx_len > len(payload):
        raise ValueError(f"rx info of {rx_len} bytes cannot hold the "
                         f"{_RX_INFO.size}-byte layout")
    offset += rx_len

    num_classes, bytes_per_class = rx[10], rx[11]
    if num_classes and bytes_per_class < _EXTRA_CLASS.size:
        raise ValueError(f"extra detection classes of {bytes_per_class} "
                         f"bytes cannot hold the 4-byte layout")
    _need(payload, offset + num_classes * bytes_per_class,
          f"{num_classes} extra detection classes")
    classes = tuple(
        (entry[0], entry[2]) for entry in (
            _EXTRA_CLASS.unpack_from(payload, offset + i * bytes_per_class)
            for i in range(num_classes)
        )
    )
    offset += num_classes * bytes_per_class

    num_soundings = rx[1] + rx[9]
    bytes_per_sounding = rx[3]
    if bytes_per_sounding < _SOUNDING.size:
        raise ValueError(f"soundings of {bytes_per_sounding} bytes cannot "
                         f"hold the {_SOUNDING.size}-byte layout")
    _need(payload, offset + num_soundings * bytes_per_sounding,
          f"{num_soundings} soundings")
    soundings = tuple(
        _sounding(payload, offset + i * bytes_per_sounding)
        for i in range(num_soundings)
    )
    offset += num_soundings * bytes_per_sounding

    total_si = sum(entry.si_num_samples for entry in soundings)
    _need(payload, offset + 2 * total_si, f"{total_si} seabed image samples")
    si_samples = struct.unpack_from(f"<{total_si}h", payload, offset)

    return KmallPing(
        **_base(frame, "MRZ"), **fields, num_partitions=num_of_dgms,
        ping_rate_hz=info[2], beam_spacing=info[3], depth_mode=info[4],
        sub_depth_mode=info[5], distance_btw_swath=info[6],
        detection_mode=info[7], pulse_form=info[8],
        fixed_gain_control=info[9], frequency_mode_hz=info[11],
        freq_range_low_lim_hz=info[12], freq_range_high_lim_hz=info[13],
        max_total_tx_pulse_length_sec=info[14],
        max_eff_tx_pulse_length_sec=info[15],
        max_eff_tx_bandwidth_hz=info[16], abs_coeff_db_per_km=info[17],
        port_sector_edge_deg=info[18], starb_sector_edge_deg=info[19],
        port_mean_cov_deg=info[20], starb_mean_cov_deg=info[21],
        port_mean_cov_m=info[22], starb_mean_cov_m=info[23],
        mode_and_stabilisation=info[24], runtime_filter1=info[25],
        runtime_filter2=info[26], pipe_tracking_status=info[27],
        transmit_array_size_used_deg=info[28],
        receive_array_size_used_deg=info[29], transmit_power_db=info[30],
        sl_ramp_up_time_remaining=info[31], yaw_angle_deg=info[33],
        heading_vessel_deg=info[36], sound_speed_at_tx_depth_mps=info[37],
        tx_transducer_depth_m=info[38],
        z_water_level_re_ref_point_m=info[39], x_kmall_to_all_m=info[40],
        y_kmall_to_all_m=info[41], lat_long_info=info[42],
        pos_sensor_status=info[43], attitude_sensor_status=info[44],
        latitude_deg=info[46], longitude_deg=info[47],
        ellipsoid_height_re_ref_point_m=info[48],
        tx_sectors=sectors, num_soundings_max_main=rx[1],
        num_soundings_valid_main=rx[2], wc_sample_rate_hz=rx[4],
        seabed_image_sample_rate_hz=rx[5], bs_normal_db=rx[6],
        bs_oblique_db=rx[7], extra_detection_alarm_flag=rx[8],
        num_extra_detections=rx[9], extra_detection_classes=classes,
        soundings=soundings, si_samples=si_samples, **v1,
    )


def _tx_sector(payload: bytes, offset: int, size: int) -> KmallTxSector:
    values = _TX_SECTOR.unpack_from(payload, offset)
    v1: dict = {}
    if size >= _TX_SECTOR.size + _TX_SECTOR_V1.size:
        high_voltage, tracking, effective = _TX_SECTOR_V1.unpack_from(
            payload, offset + _TX_SECTOR.size)
        v1 = {
            "high_voltage_level_db": high_voltage,
            "sector_tracking_corr_db": tracking,
            "effective_signal_length_sec": effective,
        }
    return KmallTxSector(
        tag="TXS", tx_sector_numb=values[0], tx_arr_number=values[1],
        tx_sub_array=values[2], sector_transmit_delay_sec=values[4],
        tilt_angle_re_tx_deg=values[5], tx_nominal_source_level_db=values[6],
        tx_focus_range_m=values[7], centre_freq_hz=values[8],
        signal_bandwidth_hz=values[9], total_signal_length_sec=values[10],
        pulse_shading=values[11], signal_wave_form=values[12], **v1,
    )


def _sounding(payload: bytes, offset: int) -> KmallSounding:
    values = _SOUNDING.unpack_from(payload, offset)
    return KmallSounding(
        tag="SND", sounding_index=values[0], tx_sector_numb=values[1],
        detection_type=values[2], detection_method=values[3],
        rejection_info1=values[4], rejection_info2=values[5],
        post_processing_info=values[6], detection_class=values[7],
        detection_confidence_level=values[8], range_factor=values[10],
        quality_factor=values[11], detection_uncertainty_ver_m=values[12],
        detection_uncertainty_hor_m=values[13],
        detection_window_length_sec=values[14], echo_length_sec=values[15],
        wc_beam_numb=values[16], wc_range_samples=values[17],
        wc_nom_beam_angle_across_deg=values[18],
        mean_abs_coeff_db_per_km=values[19], reflectivity1_db=values[20],
        reflectivity2_db=values[21],
        receiver_sensitivity_applied_db=values[22],
        source_level_applied_db=values[23], bs_calibration_db=values[24],
        tvg_db=values[25], beam_angle_re_rx_deg=values[26],
        beam_angle_correction_deg=values[27],
        two_way_travel_time_sec=values[28],
        two_way_travel_time_correction_sec=values[29],
        delta_latitude_deg=values[30], delta_longitude_deg=values[31],
        z_re_ref_point_m=values[32], y_re_ref_point_m=values[33],
        x_re_ref_point_m=values[34], beam_inc_angle_adj_deg=values[35],
        real_time_clean_info=values[36], si_start_range_samples=values[37],
        si_centre_sample=values[38], si_num_samples=values[39],
    )


_DECODERS = {
    MRZ: _decode_mrz,
    MWC: _decode_mwc,
    SPO: _decode_position,
    CPO: _decode_position,
    SKM: _decode_skm,
    SVP: _decode_svp,
    IIP: _decode_iip,
    IOP: _decode_iop,
    CHE: _decode_che,
}


def _decode(frame: KmallFrame) -> Record | None:
    """Typed record for a known datagram type, None for an unknown one."""
    decoder = _DECODERS.get(frame.dgm_type)
    if decoder is None:
        return None
    try:
        return decoder(frame)
    except (struct.error, ValueError) as error:
        return _malformed(frame, f"truncated or undecodable payload: {error}")


def _malformed(frame: KmallFrame, error: str) -> MalformedRecord:
    return MalformedRecord(
        tag=_TAGS[frame.dgm_type],
        fields=(
            f"dgm_type={frame.dgm_type}",
            f"offset={frame.offset}",
            f"payload_size={len(frame.payload)}",
        ),
        error=error,
    )


# --------------------------------------------------------------------------
# #MRZ partition reassembly (spec chapter: multibeam data logging)
# --------------------------------------------------------------------------


class _MrzAssembler:
    """Rejoins partitioned #MRZ datagrams into single frames.

    Partitions only occur when raw UDP output is logged directly; SIS
    and K-Controller merge them before writing, so .kmall files
    normally carry numOfDgms = 1 throughout. Per the spec's multibeam
    data logging chapter, every partition repeats the general header
    and the partition struct, and from #MRZ version 3 also the common
    body; each partition closes with its own length word (stripped by
    the framing already). Rejoining keeps the first partition whole
    and appends each later partition's bytes after stripping its
    repeated partition struct (and common body when present).

    Parts are matched on header time, system and echo sounder id,
    plus ping counter and fan identity when the common body is
    present; before version 3 the header key alone must serve (the
    spec added the body to every partition precisely because dual
    head, dual swath systems need it).
    """

    def __init__(self) -> None:
        self._pending: dict[tuple, dict[int, KmallFrame]] = {}
        self._expected: dict[tuple, int] = {}
        self.parts_joined = 0

    def _key(self, frame: KmallFrame) -> tuple:
        key: tuple = (frame.time_sec, frame.time_nanosec, frame.system_id,
                      frame.echo_sounder_id)
        if frame.dgm_version >= 3:
            try:
                body, _ = _mbody(frame.payload, _PARTITION.size)
            except (struct.error, ValueError):
                return key
            key += (body["ping_cnt"], body["rx_fan_index"],
                    body["swath_along_position"])
        return key

    def add(self, frame: KmallFrame,
            num_of_dgms: int, dgm_num: int) -> KmallFrame | None:
        """Buffer one partition; the rejoined frame once all arrive."""
        key = self._key(frame)
        parts = self._pending.setdefault(key, {})
        parts[dgm_num] = frame
        self._expected[key] = num_of_dgms
        if len(parts) < num_of_dgms or set(parts) != set(
                range(1, num_of_dgms + 1)):
            return None
        del self._pending[key]
        del self._expected[key]
        self.parts_joined += num_of_dgms
        return self._join([parts[i] for i in range(1, num_of_dgms + 1)])

    @staticmethod
    def _join(parts: list[KmallFrame]) -> KmallFrame:
        pieces = [parts[0].payload]
        for part in parts[1:]:
            strip = _PARTITION.size
            if part.dgm_version >= 3:
                try:
                    _, strip = _mbody(part.payload, _PARTITION.size)
                except (struct.error, ValueError):
                    strip = _PARTITION.size
            pieces.append(part.payload[strip:])
        return replace(
            parts[0], payload=b"".join(pieces),
            end_length_ok=all(part.end_length_ok for part in parts),
        )

    def pending_frames(self) -> tuple[KmallFrame, ...]:
        """Parts of sets that never completed, in arrival order."""
        return tuple(
            frame
            for parts in self._pending.values()
            for _, frame in sorted(parts.items())
        )


def read_kmall(source: str | Path | bytes) -> Iterator[Record]:
    """Stream typed records from a .kmall file (path or bytes), in
    file order.

    Datagrams with unknown type codes are skipped (use
    :func:`iter_datagrams` to see them, or :func:`load_swath` to count
    them); known types whose payload does not satisfy the spec layout
    yield :class:`~hydroformats.records.MalformedRecord`. Partitioned
    #MRZ datagrams are rejoined and yield one record when the last
    part arrives; parts of sets that never complete degrade to
    MalformedRecord at the end of the stream. Never raises on content.
    """
    assembler = _MrzAssembler()
    for event in iter_datagrams(source):
        if not isinstance(event, KmallFrame):
            continue
        record = _decode_with_partitions(event, assembler)
        if record is not None:
            yield record
    for frame in assembler.pending_frames():
        yield _malformed(frame, "incomplete partition set: not all parts of "
                                "this multi-part datagram arrived")


def _decode_with_partitions(frame: KmallFrame,
                            assembler: _MrzAssembler) -> Record | None:
    """Decode one frame, routing #MRZ partitions through the
    assembler and dropping #MWC continuation parts (their first part
    already carries everything the header-only record reports)."""
    if frame.dgm_type in (MRZ, MWC) and len(frame.payload) >= _PARTITION.size:
        num_of_dgms, dgm_num = _partition(frame.payload)
        if frame.dgm_type == MWC and dgm_num > 1:
            return None
        if frame.dgm_type == MRZ and num_of_dgms > 1:
            merged = assembler.add(frame, num_of_dgms, dgm_num)
            if merged is None:
                return None
            frame = merged
    return _decode(frame)


# --------------------------------------------------------------------------
# swath loading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KmallCounters:
    """Stream accounting from one :func:`load_swath` pass.

    ``datagrams`` counts every intact frame, decoded or not (each
    partition part counts once). ``unknown_dgm_types`` are (type code,
    count) pairs in ascending code order. ``end_length_mismatches``
    counts frames whose trailing length word does not repeat the
    header length. ``bytes_skipped`` counts only bytes outside any
    intact frame. ``mrz_parts_joined`` counts partition parts merged
    into pings; ``mrz_parts_dropped`` counts parts of sets that never
    completed; ``mwc_continuations`` counts #MWC partition parts
    beyond the first (reported once per datagram, at part 1)."""

    datagrams: int
    unknown_dgm_types: tuple[tuple[str, int], ...]
    end_length_mismatches: int
    bytes_skipped: int
    mrz_parts_joined: int
    mrz_parts_dropped: int
    mwc_continuations: int


@dataclass(frozen=True)
class KmallSwath:
    """One materialized .kmall file, split into its working series.

    ``installation`` is the first #IIP seen (a file leads with one);
    ``runtime`` keeps every #IOP since the operator can change
    settings mid-line. ``water_column`` holds the header-only #MWC
    records (see :class:`~hydroformats.kmall_records.KmallWaterColumn`).
    Units are meters, seconds, decimal degrees and dB throughout; z is
    positive down from the vessel reference point in the surface
    coordinate system. Malformed records are dropped here but still
    counted in ``counters.datagrams``; use :func:`read_kmall` to see
    them.
    """

    installation: KmallInstallation | None
    runtime: tuple[KmallRuntime, ...]
    pings: tuple[KmallPing, ...]
    water_column: tuple[KmallWaterColumn, ...]
    positions: tuple[KmallPosition, ...]
    compatibility_positions: tuple[KmallCompatibilityPosition, ...]
    attitude: tuple[KmallAttitude, ...]
    svps: tuple[KmallSvp, ...]
    heave: tuple[KmallHeave, ...]
    counters: KmallCounters


def load_swath(source: str | Path | bytes) -> KmallSwath:
    """Materialize a whole .kmall file into series (small files, tests).

    Preserves the raw observables the ping records carry: two way
    travel times and beam angles ride alongside the processed x, y, z
    so the soundings can be re-reduced under a corrected sound velocity
    profile, and reflectivity keeps the applied source level, receiver
    sensitivity and TVG beside it so the backscatter can be re-derived.
    """
    assembler = _MrzAssembler()
    installation: KmallInstallation | None = None
    runtime: list[KmallRuntime] = []
    pings: list[KmallPing] = []
    water_column: list[KmallWaterColumn] = []
    positions: list[KmallPosition] = []
    compat_positions: list[KmallCompatibilityPosition] = []
    attitude: list[KmallAttitude] = []
    svps: list[KmallSvp] = []
    heave: list[KmallHeave] = []
    unknown: dict[str, int] = {}
    datagrams = mismatches = skipped = continuations = 0
    for event in iter_datagrams(source):
        if isinstance(event, KmallGap):
            skipped += event.size
            continue
        datagrams += 1
        if not event.end_length_ok:
            mismatches += 1
        if (event.dgm_type == MWC
                and len(event.payload) >= _PARTITION.size
                and _partition(event.payload)[1] > 1):
            continuations += 1
            continue
        record = _decode_with_partitions(event, assembler)
        if record is None:
            if event.dgm_type not in _DECODERS:
                unknown[event.dgm_type] = unknown.get(event.dgm_type, 0) + 1
            continue
        if isinstance(record, KmallPing):
            pings.append(record)
        elif isinstance(record, KmallWaterColumn):
            water_column.append(record)
        elif isinstance(record, KmallCompatibilityPosition):
            compat_positions.append(record)
        elif isinstance(record, KmallPosition):
            positions.append(record)
        elif isinstance(record, KmallAttitude):
            attitude.append(record)
        elif isinstance(record, KmallSvp):
            svps.append(record)
        elif isinstance(record, KmallHeave):
            heave.append(record)
        elif isinstance(record, KmallRuntime):
            runtime.append(record)
        elif isinstance(record, KmallInstallation) and installation is None:
            installation = record
    return KmallSwath(
        installation=installation, runtime=tuple(runtime),
        pings=tuple(pings), water_column=tuple(water_column),
        positions=tuple(positions),
        compatibility_positions=tuple(compat_positions),
        attitude=tuple(attitude), svps=tuple(svps), heave=tuple(heave),
        counters=KmallCounters(
            datagrams=datagrams,
            unknown_dgm_types=tuple(sorted(unknown.items())),
            end_length_mismatches=mismatches,
            bytes_skipped=skipped,
            mrz_parts_joined=assembler.parts_joined,
            mrz_parts_dropped=len(assembler.pending_frames()),
            mwc_continuations=continuations,
        ),
    )
