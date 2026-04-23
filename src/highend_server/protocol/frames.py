from __future__ import annotations

import base64
from dataclasses import dataclass

from highend_server.domain.models import ControlMode
from highend_server.protocol.constants import (
    FORMAT_REQUEST_CAPTURE,
    FORMAT_REQUEST_GAIN,
    FORMAT_SENSOR_BASE,
    FORMAT_SET_TARGET,
)


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
    raw_b64 = raw_line.decode("ascii").strip()
    decoded = base64.b64decode(raw_b64)
    if len(decoded) != 8:
        raise ValueError(f"Expected 8 bytes, received {len(decoded)} bytes")
    return int.from_bytes(decoded, byteorder=byteorder)


def encode_transport_payload(frame: int, byteorder: str = "big") -> bytes:
    byte_data = frame.to_bytes(8, byteorder=byteorder, signed=False)
    return base64.b64encode(byte_data) + b"\n"


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
        raise ValueError("Exactly 4 actuator fields are required")
    bit_input_val = 0b0000 if mode is ControlMode.POSITION else 0b1111
    return (
        (FORMAT_SET_TARGET << 58)
        | (bit_input_val << 54)
        | (fields[0] << 42)
        | (fields[1] << 30)
        | (fields[2] << 18)
        | (fields[3] << 6)
    )


def build_request_gain_frame(local_index: int) -> int:
    actuator_masks = {0: 0b1000, 1: 0b0100, 2: 0b0010, 3: 0b0001}
    try:
        mask = actuator_masks[local_index]
    except KeyError as exc:
        raise ValueError(f"Unsupported actuator index: {local_index}") from exc
    return (FORMAT_REQUEST_GAIN << 58) | (mask << 54)


def build_request_gain_save_frame(local_index: int) -> int:
    actuator_masks = {0: 0b1000, 1: 0b0100, 2: 0b0010, 3: 0b0001}
    try:
        mask = actuator_masks[local_index]
    except KeyError as exc:
        raise ValueError(f"Unsupported actuator index: {local_index}") from exc
    return (FORMAT_REQUEST_GAIN << 58) | (mask << 50)


def build_request_capture_frame(local_index: int, capture: str) -> int:
    actuator_masks = {0: 0b1000, 1: 0b0100, 2: 0b0010, 3: 0b0001}
    capture_type_map = {"offset": 0b01, "stroke": 0b10}
    try:
        actuator_mask = actuator_masks[local_index]
        capture_bits = capture_type_map[capture]
    except KeyError as exc:
        raise ValueError(f"Unsupported capture request: index={local_index}, capture={capture}") from exc
    return (FORMAT_REQUEST_CAPTURE << 58) | (actuator_mask << 54) | (capture_bits << 52)


def build_set_gain_frame(local_index: int, p: float, i: float, d: float) -> int:
    format_value_map = {0: 10, 1: 20, 2: 30, 3: 40}
    try:
        format_value = format_value_map[local_index]
    except KeyError as exc:
        raise ValueError(f"Unsupported actuator index: {local_index}") from exc

    p_bin = max(0, min(255, int(p)))
    i_bin = max(0, min(255, int(i)))
    d_bin = max(0, min(255, int(d)))
    return (format_value << 58) | (p_bin << 50) | (i_bin << 42) | (d_bin << 34)
