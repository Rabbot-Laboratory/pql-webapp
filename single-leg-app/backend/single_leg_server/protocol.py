from __future__ import annotations

import base64
from dataclasses import dataclass

from single_leg_server.models import ControlMode

FORMAT_SENSOR_BASE = 5
FORMAT_REQUEST_GAIN = 1
FORMAT_REQUEST_CAPTURE = 50
FORMAT_SET_TARGET = 63


@dataclass(slots=True)
class SensorFrame:
    actuator_index: int
    position: int
    voltage: int
    command: int
    pressure: int


@dataclass(slots=True)
class GainFrame:
    actuator_index: int
    p_gain: int
    i_gain: int
    d_gain: int
    capture_max: int
    capture_min: int


def decode_transport_payload(raw_line: bytes, byteorder: str = "little") -> int:
    decoded = base64.b64decode(raw_line.decode("ascii").strip(), validate=True)
    if len(decoded) != 8:
        raise ValueError(f"Expected 8 bytes, received {len(decoded)} bytes")
    return int.from_bytes(decoded, byteorder=byteorder)


def encode_transport_payload(frame: int, byteorder: str = "big") -> bytes:
    return base64.b64encode(frame.to_bytes(8, byteorder=byteorder, signed=False)) + b"\n"


def decode_frame(frame: int) -> SensorFrame | GainFrame | None:
    format_value = (frame >> 58) & 0x3F
    if format_value in (11, 21, 31, 41):
        actuator_index = {11: 0, 21: 1, 31: 2, 41: 3}[format_value]
        return GainFrame(
            actuator_index=actuator_index,
            p_gain=(frame >> 50) & 0xFF,
            i_gain=(frame >> 42) & 0xFF,
            d_gain=(frame >> 34) & 0xFF,
            capture_max=(frame >> 22) & 0xFFF,
            capture_min=(frame >> 10) & 0xFFF,
        )
    if FORMAT_SENSOR_BASE <= format_value < FORMAT_SENSOR_BASE + 4:
        return SensorFrame(
            actuator_index=format_value - FORMAT_SENSOR_BASE,
            position=(frame >> 46) & 0xFFF,
            voltage=(frame >> 34) & 0xFFF,
            command=(frame >> 22) & 0xFFF,
            pressure=(frame >> 10) & 0xFFF,
        )
    return None


def build_set_target_frame(fields: list[int], mode: ControlMode) -> int:
    if len(fields) != 4:
        raise ValueError("Exactly four actuator fields are required by the ESP32 protocol")
    mode_bits = 0b0000 if mode is ControlMode.POSITION else 0b1111
    return (
        (FORMAT_SET_TARGET << 58)
        | (mode_bits << 54)
        | (fields[0] << 42)
        | (fields[1] << 30)
        | (fields[2] << 18)
        | (fields[3] << 6)
    )


def build_request_gain_frame(local_index: int, *, save: bool = False) -> int:
    mask = _actuator_mask(local_index)
    shift = 50 if save else 54
    return (FORMAT_REQUEST_GAIN << 58) | (mask << shift)


def build_request_capture_frame(local_index: int, capture: str) -> int:
    capture_bits = {"offset": 0b01, "stroke": 0b10}.get(capture)
    if capture_bits is None:
        raise ValueError("capture must be 'offset' or 'stroke'")
    return (
        (FORMAT_REQUEST_CAPTURE << 58)
        | (_actuator_mask(local_index) << 54)
        | (capture_bits << 52)
    )


def build_set_gain_frame(local_index: int, p: int, i: int, d: int) -> int:
    format_value = {0: 10, 1: 20, 2: 30, 3: 40}.get(local_index)
    if format_value is None:
        raise ValueError(f"Unsupported actuator index: {local_index}")
    return (format_value << 58) | (p << 50) | (i << 42) | (d << 34)


def _actuator_mask(local_index: int) -> int:
    mask = {0: 0b1000, 1: 0b0100, 2: 0b0010, 3: 0b0001}.get(local_index)
    if mask is None:
        raise ValueError(f"Unsupported actuator index: {local_index}")
    return mask

