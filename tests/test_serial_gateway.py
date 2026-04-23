import asyncio

from highend_server.domain.models import ControlMode, DeviceEnvelope, PortRole
from highend_server.protocol.frames import build_request_gain_frame, build_set_target_frame, decode_frame, decode_transport_payload
from highend_server.config import Settings
from highend_server.transport.serial_gateway import PySerialGateway, StubSerialGateway, build_gateway


def test_build_gateway_returns_stub_in_emulation_mode() -> None:
    settings = Settings(emulate_devices=True)
    gateway = build_gateway(settings)
    assert isinstance(gateway, StubSerialGateway)


def test_build_gateway_returns_real_serial_gateway_when_not_emulated() -> None:
    settings = Settings(emulate_devices=False)
    gateway = build_gateway(settings)
    assert isinstance(gateway, PySerialGateway)


def test_stub_gateway_emits_sensor_payload_for_target_command() -> None:
    async def scenario() -> None:
        received: list[DeviceEnvelope] = []
        settings = Settings(emulate_devices=True, emulate_tick_interval_sec=60.0)
        gateway = StubSerialGateway(settings)

        async def callback(envelope: DeviceEnvelope) -> None:
            received.append(envelope)

        gateway.set_device_callback(callback)
        await gateway.connect()
        try:
            frame = build_set_target_frame([2500, 2048, 2048, 2048], ControlMode.POSITION)
            await gateway.send_frame(PortRole.FRONT, frame)
        finally:
            await gateway.disconnect()

        assert received
        decoded = decode_transport_payload(received[0].payload)
        assert (decoded >> 58) & 0x3F == 5
        sensor = decode_frame(decoded)
        assert sensor is not None
        assert sensor.pressure >= 0

    asyncio.run(scenario())


def test_stub_gateway_emits_gain_response() -> None:
    async def scenario() -> None:
        received: list[DeviceEnvelope] = []
        settings = Settings(emulate_devices=True, emulate_tick_interval_sec=60.0)
        gateway = StubSerialGateway(settings)

        async def callback(envelope: DeviceEnvelope) -> None:
            received.append(envelope)

        gateway.set_device_callback(callback)
        await gateway.connect()
        try:
            await gateway.send_frame(PortRole.BACK, build_request_gain_frame(2))
        finally:
            await gateway.disconnect()

        assert received
        decoded = decode_transport_payload(received[-1].payload)
        assert (decoded >> 58) & 0x3F == 31

    asyncio.run(scenario())


def test_pyserial_gateway_read_chunk_falls_back_when_in_waiting_is_none() -> None:
    class DummySerial:
        in_waiting = None

        def __init__(self) -> None:
            self.requested_size: int | None = None

        def read(self, size: int) -> bytes:
            self.requested_size = size
            return b"x"

    connection = DummySerial()
    chunk = PySerialGateway._read_chunk(connection)  # type: ignore[arg-type]

    assert chunk == b"x"
    assert connection.requested_size == 1
