from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from single_leg_server.config import Settings
from single_leg_server.models import (
    ActuatorState,
    ConnectionState,
    ControlMode,
    SystemStatus,
    TelemetryEvent,
    utc_now,
)
from single_leg_server.protocol import (
    GainFrame,
    SensorFrame,
    build_request_capture_frame,
    build_request_gain_frame,
    build_set_gain_frame,
    build_set_target_frame,
    decode_frame,
    decode_transport_payload,
)
from single_leg_server.serial_link import build_serial_link

EventSink = Callable[[TelemetryEvent], Awaitable[None]]


class SingleLegController:
    """Two-axis application service backed by exactly one ESP32 serial link."""

    def __init__(self, settings: Settings, event_sink: EventSink) -> None:
        self.settings = settings
        self.event_sink = event_sink
        self._lock = asyncio.Lock()
        self._actuators = [
            ActuatorState(actuator_id=0, label="hip", local_index=0),
            ActuatorState(actuator_id=1, label="knee", local_index=1),
        ]
        self._connection_state = (
            ConnectionState.EMULATED if settings.emulate_devices else ConnectionState.CONNECTING
        )
        self.link = build_serial_link(settings, self._handle_payload, self._handle_state)

    @property
    def status(self) -> SystemStatus:
        return SystemStatus(
            connection_state=self._connection_state,
            emulate_devices=self.settings.emulate_devices,
            esp32_path=(
                "emulated:single-leg"
                if self.settings.emulate_devices
                else self.settings.port_name
            ),
        )

    def list_actuators(self) -> list[ActuatorState]:
        return [item.model_copy(deep=True) for item in self._actuators]

    def get_actuator(self, actuator_id: int) -> ActuatorState:
        try:
            return self._actuators[actuator_id].model_copy(deep=True)
        except IndexError as exc:
            raise ValueError("Only hip (0) and knee (1) are available") from exc

    async def start(self) -> None:
        await self.link.start()

    async def stop(self) -> None:
        await self.link.stop()

    async def set_target(self, actuator_id: int, mode: ControlMode, value: int) -> ActuatorState:
        self._validate_actuator_id(actuator_id)
        async with self._lock:
            actuator = self._actuators[actuator_id]
            if mode is ControlMode.POSITION:
                actuator.target_position = value
            else:
                actuator.target_command = value
            actuator.updated_at = utc_now()
            fields = self._target_fields(mode)
            await self.link.send_frame(build_set_target_frame(fields, mode))
            snapshot = actuator.model_copy(deep=True)
        await self._emit("actuator_state", {"actuator": snapshot.model_dump(mode="json")})
        return snapshot

    async def set_gain(self, actuator_id: int, p: int, i: int, d: int) -> None:
        actuator = self._get_mutable(actuator_id)
        await self.link.send_frame(build_set_gain_frame(actuator.local_index, p, i, d))

    async def request_gain(self, actuator_id: int, *, save: bool = False) -> None:
        actuator = self._get_mutable(actuator_id)
        await self.link.send_frame(build_request_gain_frame(actuator.local_index, save=save))

    async def capture(self, actuator_id: int, capture: str) -> None:
        actuator = self._get_mutable(actuator_id)
        await self.link.send_frame(build_request_capture_frame(actuator.local_index, capture))

    async def publish_snapshot(self) -> None:
        await self._emit(
            "snapshot",
            {
                "system": self.status.model_dump(mode="json"),
                "actuators": [item.model_dump(mode="json") for item in self.list_actuators()],
            },
        )

    async def _handle_payload(self, payload: bytes) -> None:
        try:
            decoded = decode_frame(decode_transport_payload(payload, byteorder="little"))
        except (ValueError, UnicodeError):
            return
        if decoded is None or decoded.actuator_index > 1:
            return

        async with self._lock:
            actuator = self._actuators[decoded.actuator_index]
            if isinstance(decoded, SensorFrame):
                actuator.telemetry.position = decoded.position
                actuator.telemetry.voltage = decoded.voltage
                actuator.telemetry.command = decoded.command
                actuator.telemetry.pressure = decoded.pressure
            elif isinstance(decoded, GainFrame):
                actuator.gains.p = decoded.p_gain
                actuator.gains.i = decoded.i_gain
                actuator.gains.d = decoded.d_gain
                actuator.capture.min = decoded.capture_min
                actuator.capture.max = decoded.capture_max
            actuator.updated_at = utc_now()
            snapshot = actuator.model_copy(deep=True)

        event_type = "telemetry" if isinstance(decoded, SensorFrame) else "gain_response"
        await self._emit(event_type, {"actuator": snapshot.model_dump(mode="json")})

    async def _handle_state(self, state: ConnectionState) -> None:
        self._connection_state = state
        await self._emit("server_status", self.status.model_dump(mode="json"))

    def _target_fields(self, mode: ControlMode) -> list[int]:
        values = [
            item.target_position if mode is ControlMode.POSITION else item.target_command
            for item in self._actuators
        ]
        unused = (
            self.settings.unused_position
            if mode is ControlMode.POSITION
            else self.settings.unused_command
        )
        return [*values, unused, unused]

    def _get_mutable(self, actuator_id: int) -> ActuatorState:
        self._validate_actuator_id(actuator_id)
        return self._actuators[actuator_id]

    @staticmethod
    def _validate_actuator_id(actuator_id: int) -> None:
        if actuator_id not in (0, 1):
            raise ValueError("Only hip (0) and knee (1) are available")

    async def _emit(self, event_type: str, payload: dict) -> None:
        await self.event_sink(TelemetryEvent(type=event_type, payload=payload))
