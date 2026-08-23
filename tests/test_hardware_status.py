from __future__ import annotations

import asyncio

from highend_server.application.hardware_status import HardwareStatusService
from highend_server.config import Settings
from highend_server.domain.models import HardwareConnectionState, TelemetryEvent
from highend_server.sensors.sensor_service import SensorService
from highend_server.transport.serial_gateway import StubSerialGateway


def test_physical_mode_reports_disabled_required_sensors_without_stopping() -> None:
    async def scenario() -> None:
        async def sink(_event: TelemetryEvent) -> None:
            return None

        settings = Settings(emulate_devices=False, sensors_enabled=False)
        gateway = StubSerialGateway(settings)
        sensors = SensorService(settings=settings, event_sink=sink)
        await gateway.connect()
        await sensors.start()
        try:
            service = HardwareStatusService(
                settings=settings,
                gateway=gateway,
                sensor_service=sensors,
                event_sink=sink,
            )
            state = service.state
            by_id = {device.device_id: device for device in state.devices}
            assert state.server_ok is True
            assert state.robot_ready is False
            assert state.required_connected == 2
            assert by_id["imu_bmx055"].connection_state is HardwareConnectionState.DISABLED
            assert by_id["contact_adc"].connection_state is HardwareConnectionState.DISABLED
        finally:
            await sensors.stop()
            await gateway.disconnect()

    asyncio.run(scenario())


def test_demo_mode_marks_all_required_devices_as_emulated_and_ready() -> None:
    async def scenario() -> None:
        async def sink(_event: TelemetryEvent) -> None:
            return None

        settings = Settings(emulate_devices=True, sensors_enabled=False)
        gateway = StubSerialGateway(settings)
        sensors = SensorService(settings=settings, event_sink=sink)
        await gateway.connect()
        await sensors.start()
        try:
            service = HardwareStatusService(
                settings=settings,
                gateway=gateway,
                sensor_service=sensors,
                event_sink=sink,
            )
            state = service.state
            assert state.robot_ready is True
            assert state.required_connected == state.required_total == 4
            assert all(
                device.connection_state is HardwareConnectionState.EMULATED
                for device in state.devices
            )
        finally:
            await sensors.stop()
            await gateway.disconnect()

    asyncio.run(scenario())
