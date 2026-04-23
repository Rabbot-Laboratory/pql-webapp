from highend_server.domain.models import ControlMode
from highend_server.protocol.frames import (
    build_request_capture_frame,
    build_request_gain_frame,
    build_set_gain_frame,
    build_set_target_frame,
    decode_frame,
)


def test_build_set_target_frame_round_trip_shape() -> None:
    frame = build_set_target_frame([1000, 2000, 3000, 4000], ControlMode.POSITION)
    assert (frame >> 58) & 0x3F == 63


def test_decode_gain_frame() -> None:
    frame = (11 << 58) | (12 << 50) | (34 << 42) | (56 << 34)
    decoded = decode_frame(frame)
    assert decoded is not None
    assert decoded.actuator_index == 0


def test_gain_and_capture_requests_use_expected_headers() -> None:
    assert (build_request_gain_frame(0) >> 58) & 0x3F == 1
    assert (build_request_capture_frame(1, "offset") >> 58) & 0x3F == 50
    assert (build_set_gain_frame(2, 1, 2, 3) >> 58) & 0x3F == 30


def test_decode_sensor_frame_with_pressure() -> None:
    frame = (5 << 58) | (3000 << 46) | (2500 << 34) | (1200 << 22) | (1800 << 10)
    decoded = decode_frame(frame)
    assert decoded is not None
    assert decoded.actuator_index == 0
    assert decoded.position == 3000
    assert decoded.voltage == 2500
    assert decoded.command == 1200
    assert decoded.pressure == 1800
