import asyncio

from highend_server.config import Settings
from highend_server.domain.models import SensorConnectionState, TelemetryEvent
from highend_server.sensors.sensor_service import SensorService


def test_sensor_service_emits_demo_sensor_state_in_device_emulation_mode() -> None:
    async def scenario() -> None:
        events: list[TelemetryEvent] = []
        settings = Settings(
            emulate_devices=True,
            sensors_enabled=False,
            sensor_poll_interval_sec=0.01,
        )

        async def sink(event: TelemetryEvent) -> None:
            events.append(event)

        service = SensorService(settings=settings, event_sink=sink)

        await service.start()
        try:
            await asyncio.sleep(0.03)
        finally:
            await service.stop()

        state = service.state
        assert state.enabled is True
        assert state.imu.connection_state is SensorConnectionState.CONNECTED
        assert state.imu.accel_g is not None
        assert state.imu.gyro_dps is not None
        assert state.imu.mag_raw is not None
        assert state.imu.orientation is not None
        assert state.imu.orientation.yaw_deg is not None
        assert state.imu.quaternion is not None
        assert state.imu.gravity_g is not None
        assert state.imu.linear_accel_g is not None
        assert state.adc_banks
        assert all(
            bank.connection_state is SensorConnectionState.CONNECTED
            for bank in state.adc_banks
        )
        assert any(event.type == "sensor_state" for event in events)

    asyncio.run(scenario())


def test_sensor_service_stays_disabled_without_sensor_or_device_emulation() -> None:
    async def scenario() -> None:
        events: list[TelemetryEvent] = []
        settings = Settings(emulate_devices=False, sensors_enabled=False)

        async def sink(event: TelemetryEvent) -> None:
            events.append(event)

        service = SensorService(settings=settings, event_sink=sink)

        await service.start()
        await service.stop()

        state = service.state
        assert state.enabled is False
        assert state.imu.connection_state is SensorConnectionState.DISABLED
        assert events == []

    asyncio.run(scenario())
