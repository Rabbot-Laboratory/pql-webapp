from single_leg_server.models import ControlMode
from single_leg_server.protocol import (
    GainFrame,
    SensorFrame,
    build_request_capture_frame,
    build_set_gain_frame,
    build_set_target_frame,
    decode_frame,
    decode_transport_payload,
    encode_transport_payload,
)


def test_target_frame_keeps_hip_and_knee_in_first_two_fields() -> None:
    frame = build_set_target_frame([1111, 2222, 2048, 2048], ControlMode.POSITION)

    assert (frame >> 58) & 0x3F == 63
    assert (frame >> 54) & 0xF == 0
    assert (frame >> 42) & 0xFFF == 1111
    assert (frame >> 30) & 0xFFF == 2222
    assert (frame >> 18) & 0xFFF == 2048
    assert (frame >> 6) & 0xFFF == 2048


def test_transport_round_trip_supports_esp32_receive_byte_order() -> None:
    frame = (5 << 58) | (1234 << 46) | (1500 << 34) | (900 << 22) | (777 << 10)
    payload = encode_transport_payload(frame, byteorder="little")
    decoded = decode_frame(decode_transport_payload(payload, byteorder="little"))

    assert isinstance(decoded, SensorFrame)
    assert decoded.actuator_index == 0
    assert decoded.position == 1234
    assert decoded.pressure == 777


def test_gain_and_capture_commands_are_limited_to_selected_local_axis() -> None:
    gain_frame = build_set_gain_frame(1, 10, 20, 30)
    capture_frame = build_request_capture_frame(0, "offset")

    assert (gain_frame >> 58) & 0x3F == 20
    assert (gain_frame >> 50) & 0xFF == 10
    assert (capture_frame >> 54) & 0xF == 0b1000
    assert (capture_frame >> 52) & 0x3 == 0b01


def test_gain_response_decodes() -> None:
    frame = (21 << 58) | (3 << 50) | (4 << 42) | (5 << 34) | (3000 << 22) | (900 << 10)
    decoded = decode_frame(frame)

    assert isinstance(decoded, GainFrame)
    assert decoded.actuator_index == 1
    assert decoded.capture_min == 900
    assert decoded.capture_max == 3000

