from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic

from highend_server.config import Settings
from highend_server.domain.models import (
    ConnectionState,
    HardwareConnectionState,
    HardwareDeviceStatus,
    HardwareStatus,
    PortRole,
    SensorConnectionState,
    TelemetryEvent,
)
from highend_server.sensors.sensor_service import SensorService
from highend_server.transport.serial_gateway import SerialGateway

EventSink = Callable[[TelemetryEvent], Awaitable[None]]

_READY_STATES = {
    HardwareConnectionState.CONNECTED,
    HardwareConnectionState.EMULATED,
}


class HardwareStatusService:
    """Aggregate required hardware without making application startup depend on it."""

    def __init__(
        self,
        *,
        settings: Settings,
        gateway: SerialGateway,
        sensor_service: SensorService,
        event_sink: EventSink,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.sensor_service = sensor_service
        self.event_sink = event_sink
        self._task: asyncio.Task[None] | None = None
        self._last_signature: tuple | None = None
        self._last_state = HardwareStatus()
        self._imu_sample_count = 0
        self._imu_progress_at = monotonic()
        self._imu_last_seen_at: datetime | None = None

    @property
    def state(self) -> HardwareStatus:
        self._last_state = self._build_state()
        return self._last_state.model_copy(deep=True)

    async def start(self) -> None:
        await self._publish_if_changed(force=True)
        self._task = asyncio.create_task(self._monitor_loop(), name="hardware-status")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.hardware_status_interval_sec)
            await self._publish_if_changed()

    async def _publish_if_changed(self, *, force: bool = False) -> None:
        state = self._build_state()
        signature = tuple(
            (
                device.device_id,
                device.connection_state.value,
                device.enabled,
                device.detail,
            )
            for device in state.devices
        )
        self._last_state = state
        if not force and signature == self._last_signature:
            return
        self._last_signature = signature
        await self.event_sink(
            TelemetryEvent(
                type="hardware_status",
                payload={"hardware": state.model_dump(mode="json")},
            )
        )

    def _build_state(self) -> HardwareStatus:
        devices = [*self._serial_devices(), self._imu_device(), self._contact_adc_device()]
        required = [device for device in devices if device.required]
        connected = sum(device.connection_state in _READY_STATES for device in required)
        return HardwareStatus(
            server_ok=True,
            robot_ready=connected == len(required),
            required_connected=connected,
            required_total=len(required),
            devices=devices,
        )

    def _serial_devices(self) -> list[HardwareDeviceStatus]:
        by_role = {state.port_role: state for state in self.gateway.port_states()}
        devices: list[HardwareDeviceStatus] = []
        for role, device_id in (
            (PortRole.FRONT, "esp32_front"),
            (PortRole.BACK, "esp32_back"),
        ):
            port = by_role.get(role)
            if self.settings.emulate_devices:
                connection_state = HardwareConnectionState.EMULATED
                detail = "emulated ESP32"
            elif port is None:
                connection_state = HardwareConnectionState.MISSING
                detail = "serial port state unavailable"
            elif port.connection_state is ConnectionState.CONNECTED:
                connection_state = HardwareConnectionState.CONNECTED
                detail = None
            elif port.connection_state is ConnectionState.CONNECTING:
                connection_state = HardwareConnectionState.MISSING
                detail = port.error or "serial device not found; retrying"
            elif port.connection_state is ConnectionState.ERROR:
                connection_state = HardwareConnectionState.ERROR
                detail = port.error or "serial connection error"
            else:
                connection_state = HardwareConnectionState.MISSING
                detail = port.error or "serial device disconnected"
            devices.append(
                HardwareDeviceStatus(
                    device_id=device_id,
                    label=f"{role.value} ESP32",
                    kind="serial",
                    required=True,
                    enabled=True,
                    connection_state=connection_state,
                    detail=detail,
                    path=port.path if port is not None else None,
                    last_seen_at=port.last_received_at if port is not None else None,
                    updated_at=port.updated_at if port is not None else datetime.now(UTC),
                )
            )
        return devices

    def _imu_device(self) -> HardwareDeviceStatus:
        imu = self.sensor_service.state.imu
        if imu.sample_count != self._imu_sample_count:
            self._imu_sample_count = imu.sample_count
            self._imu_progress_at = monotonic()
            self._imu_last_seen_at = datetime.now(UTC)

        enabled = self.settings.sensors_enabled or self.settings.emulate_devices
        if self.settings.emulate_devices and not self.settings.sensors_enabled:
            state = HardwareConnectionState.EMULATED
            detail = "emulated IMU"
        elif not enabled or imu.connection_state is SensorConnectionState.DISABLED:
            state = HardwareConnectionState.DISABLED
            detail = "sensor polling is disabled"
        elif imu.connection_state is SensorConnectionState.ERROR:
            state = HardwareConnectionState.ERROR
            detail = imu.error or "IMU error"
        elif imu.connection_state is SensorConnectionState.CONNECTING:
            state = HardwareConnectionState.CONNECTING
            detail = "waiting for fused IMU samples"
        elif monotonic() - self._imu_progress_at > self.settings.hardware_imu_stale_sec:
            state = HardwareConnectionState.STALE
            detail = "IMU sample counter is not advancing"
        else:
            state = HardwareConnectionState.CONNECTED
            detail = None
        return HardwareDeviceStatus(
            device_id="imu_bmx055",
            label="BMX055 IMU",
            kind="imu",
            required=True,
            enabled=enabled,
            connection_state=state,
            detail=detail,
            path=f"/dev/i2c-{self.settings.sensor_i2c_bus}",
            last_seen_at=self._imu_last_seen_at,
        )

    def _contact_adc_device(self) -> HardwareDeviceStatus:
        sensor_state = self.sensor_service.state
        bank = next((item for item in sensor_state.adc_banks if item.device == 0), None)
        enabled = self.settings.sensors_enabled or self.settings.emulate_devices
        if self.settings.emulate_devices and not self.settings.sensors_enabled:
            state = HardwareConnectionState.EMULATED
            detail = "emulated contact ADC"
        elif not enabled or bank is None or bank.connection_state is SensorConnectionState.DISABLED:
            state = (
                HardwareConnectionState.DISABLED
                if not enabled
                else HardwareConnectionState.MISSING
            )
            detail = "sensor polling is disabled" if not enabled else "MCP3208 state unavailable"
        elif bank.connection_state is SensorConnectionState.ERROR:
            state = HardwareConnectionState.ERROR
            detail = bank.error or "MCP3208 read error"
        elif bank.connection_state is SensorConnectionState.CONNECTING:
            state = HardwareConnectionState.CONNECTING
            detail = "waiting for MCP3208 samples"
        else:
            state = HardwareConnectionState.CONNECTED
            detail = None
        return HardwareDeviceStatus(
            device_id="contact_adc",
            label="Contact Sensors (MCP3208)",
            kind="contact_adc",
            required=True,
            enabled=enabled,
            connection_state=state,
            detail=detail,
            path=f"/dev/spidev{self.settings.adc_spi_bus}.0",
            last_seen_at=bank.updated_at if bank is not None and state in _READY_STATES else None,
        )
