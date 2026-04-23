from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import serial

from highend_server.config import Settings
from highend_server.domain.models import COMMAND_NEUTRAL, ConnectionState, DeviceEnvelope, PortRole
from highend_server.protocol.frames import encode_transport_payload
from highend_server.protocol.constants import FORMAT_SENSOR_BASE


DeviceCallback = Callable[[DeviceEnvelope], Awaitable[None]]
logger = logging.getLogger(__name__)


class SerialGateway:
    """Thin async boundary around the ESP32 transport."""

    def __init__(self) -> None:
        self._device_callback: DeviceCallback | None = None
        self.connection_state = ConnectionState.DISCONNECTED

    def set_device_callback(self, callback: DeviceCallback) -> None:
        self._device_callback = callback

    async def connect(self) -> None:
        self.connection_state = ConnectionState.CONNECTED

    async def disconnect(self) -> None:
        self.connection_state = ConnectionState.DISCONNECTED

    async def send_frame(self, port_role: PortRole, frame: int) -> None:
        raise NotImplementedError


@dataclass(slots=True)
class EmulatedActuator:
    position: int = 2048
    command: int = COMMAND_NEUTRAL
    voltage: int = 1200
    pressure: int = 2048
    target_position: int = 2048
    target_command: int = COMMAND_NEUTRAL
    p_gain: int = 40
    i_gain: int = 10
    d_gain: int = 5
    capture_max: int = 3200
    capture_min: int = 900


class PySerialGateway(SerialGateway):
    """Real serial transport for the Front/Back ESP32 devices."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()
        self._connection_lock = threading.Lock()
        self._connections: dict[PortRole, serial.Serial] = {}
        self._reader_threads: dict[PortRole, threading.Thread] = {}

    async def connect(self) -> None:
        if self.connection_state is ConnectionState.CONNECTED:
            return

        self.connection_state = ConnectionState.CONNECTING
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()

        failures: dict[PortRole, str] = {}
        for port_role, port_name in self._port_map().items():
            connection = self._open_connection(port_role)
            if connection is None:
                failures[port_role] = f"Unable to open {port_name}"
                logger.warning("Failed to open %s on %s during startup", port_role.value, port_name)
            else:
                self._set_connection(port_role, connection)
                logger.info("Connected %s ESP32 on %s", port_role.value, port_name)

            thread = threading.Thread(
                target=self._reader_loop,
                args=(port_role,),
                daemon=True,
                name=f"{port_role.value}SerialReader",
            )
            self._reader_threads[port_role] = thread
            thread.start()

        connected_count = len(self._connections)
        expected_count = len(self._port_map())
        if connected_count == 0 or (self.settings.require_all_ports and connected_count != expected_count):
            await self.disconnect()
            self.connection_state = ConnectionState.ERROR
            failure_text = ", ".join(f"{role.value}: {reason}" for role, reason in failures.items())
            raise ConnectionError(f"Unable to open required serial ports. {failure_text}".strip())

        self._refresh_connection_state()

    async def disconnect(self) -> None:
        self._stop_event.set()

        connections = list(self._connections.values())
        for connection in connections:
            try:
                if connection.is_open:
                    connection.close()
            except serial.SerialException:
                logger.exception("Failed to close serial connection")

        threads = list(self._reader_threads.values())
        for thread in threads:
            await asyncio.to_thread(thread.join, 1.0)

        self._connections.clear()
        self._reader_threads.clear()
        self.connection_state = ConnectionState.DISCONNECTED

    async def send_frame(self, port_role: PortRole, frame: int) -> None:
        connection = self._get_connection(port_role)
        if connection is None or not connection.is_open:
            raise ConnectionError(f"Serial port for {port_role.value} is not connected")

        payload = encode_transport_payload(frame, byteorder="big")
        await asyncio.to_thread(self._write_payload, connection, port_role, payload)

    def _port_map(self) -> dict[PortRole, str]:
        return {
            PortRole.FRONT: self.settings.front_port_name,
            PortRole.BACK: self.settings.back_port_name,
        }

    def _reader_loop(self, port_role: PortRole) -> None:
        buffer = bytearray()
        while not self._stop_event.is_set():
            connection = self._get_connection(port_role)
            if connection is None or not connection.is_open:
                self.connection_state = ConnectionState.CONNECTING
                if not self._recover_connection(port_role):
                    time.sleep(0.5)
                    continue
                buffer.clear()
                continue

            try:
                chunk = self._read_chunk(connection)
            except (serial.SerialException, OSError, TypeError, ValueError) as exc:
                logger.warning("Serial read glitch on %s: %s", port_role.value, exc)
                self._drop_connection(port_role, expected=connection)
                self.connection_state = ConnectionState.CONNECTING
                time.sleep(0.25)
                continue

            if not chunk or self._device_callback is None or self._loop is None:
                continue

            buffer.extend(chunk)
            while True:
                separator_indexes = [index for index, byte in enumerate(buffer) if byte in (10, 13)]
                if not separator_indexes:
                    break

                split_at = separator_indexes[0]
                token = bytes(buffer[:split_at]).strip()
                del buffer[: split_at + 1]

                if not token or not self._looks_like_transport_payload(token):
                    continue

                envelope = DeviceEnvelope(port_role=port_role, payload=token)
                future = asyncio.run_coroutine_threadsafe(self._device_callback(envelope), self._loop)
                future.add_done_callback(self._log_callback_failure)

    def _write_payload(self, connection: serial.Serial, port_role: PortRole, payload: bytes) -> None:
        with self._write_lock:
            try:
                connection.write(payload)
            except serial.SerialException as exc:
                self._drop_connection(port_role, expected=connection)
                self.connection_state = ConnectionState.CONNECTING
                logger.exception("Serial write error on %s: %s", port_role.value, exc)
                raise

    def _open_connection(self, port_role: PortRole) -> serial.Serial | None:
        port_name = self._port_map()[port_role]
        try:
            connection = serial.Serial(
                port=port_name,
                baudrate=self.settings.serial_baudrate,
                timeout=self.settings.serial_timeout_sec,
                write_timeout=self.settings.serial_write_timeout_sec,
            )
        except serial.SerialException as exc:
            logger.warning("Failed to open %s on %s: %s", port_role.value, port_name, exc)
            return None

        # Give ESP32-class devices a moment to settle after the port open/reset.
        time.sleep(0.25)
        try:
            connection.reset_input_buffer()
            connection.reset_output_buffer()
        except serial.SerialException:
            logger.debug("Unable to flush serial buffers for %s", port_role.value, exc_info=True)

        return connection

    def _recover_connection(self, port_role: PortRole) -> bool:
        connection = self._open_connection(port_role)
        if connection is None:
            return False

        self._set_connection(port_role, connection)
        self._refresh_connection_state()
        logger.info("Recovered %s serial connection", port_role.value)
        return True

    def _get_connection(self, port_role: PortRole) -> serial.Serial | None:
        with self._connection_lock:
            return self._connections.get(port_role)

    def _set_connection(self, port_role: PortRole, connection: serial.Serial) -> None:
        with self._connection_lock:
            self._connections[port_role] = connection

    def _drop_connection(self, port_role: PortRole, *, expected: serial.Serial | None = None) -> None:
        with self._connection_lock:
            current = self._connections.get(port_role)
            if expected is not None and current is not expected:
                current = expected
            else:
                self._connections.pop(port_role, None)

        if current is None:
            self._refresh_connection_state()
            return

        try:
            if current.is_open:
                current.close()
        except serial.SerialException:
            logger.debug("Failed to close dropped serial connection for %s", port_role.value, exc_info=True)

        if expected is not None:
            with self._connection_lock:
                if self._connections.get(port_role) is expected:
                    self._connections.pop(port_role, None)

        self._refresh_connection_state()

    def _refresh_connection_state(self) -> None:
        if self._stop_event.is_set():
            return

        with self._connection_lock:
            connected_count = sum(1 for connection in self._connections.values() if connection.is_open)

        expected_count = len(self._port_map())
        if connected_count == 0:
            self.connection_state = ConnectionState.ERROR
        elif self.settings.require_all_ports and connected_count != expected_count:
            self.connection_state = ConnectionState.CONNECTING
        else:
            self.connection_state = ConnectionState.CONNECTED

    @staticmethod
    def _read_chunk(connection: serial.Serial) -> bytes:
        pending = getattr(connection, "in_waiting", None)
        size = pending if isinstance(pending, int) and pending > 0 else 1
        return connection.read(size)

    @staticmethod
    def _log_callback_failure(future) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("Device callback failed")

    @staticmethod
    def _looks_like_transport_payload(token: bytes) -> bool:
        if len(token) != 12 or not token.endswith(b"="):
            return False
        return all(
            (65 <= byte <= 90)
            or (97 <= byte <= 122)
            or (48 <= byte <= 57)
            or byte in (43, 47, 61)
            for byte in token
        )


class StubSerialGateway(SerialGateway):
    """Development stub that behaves like a small dummy ESP32 pair."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self._telemetry_task: asyncio.Task[None] | None = None
        self._actuators: dict[PortRole, list[EmulatedActuator]] = {
            PortRole.FRONT: [EmulatedActuator() for _ in range(4)],
            PortRole.BACK: [EmulatedActuator() for _ in range(4)],
        }

    async def connect(self) -> None:
        self.connection_state = ConnectionState.CONNECTED
        if self._telemetry_task is None or self._telemetry_task.done():
            self._telemetry_task = asyncio.create_task(self._telemetry_loop(), name="stub-telemetry")

    async def disconnect(self) -> None:
        if self._telemetry_task and not self._telemetry_task.done():
            self._telemetry_task.cancel()
            try:
                await self._telemetry_task
            except asyncio.CancelledError:
                pass
        self._telemetry_task = None
        self.connection_state = ConnectionState.DISCONNECTED

    async def send_frame(self, port_role: PortRole, frame: int) -> None:
        await self._handle_outbound_frame(port_role, frame)

    async def _handle_outbound_frame(self, port_role: PortRole, frame: int) -> None:
        format_value = (frame >> 58) & 0x3F
        local_actuators = self._actuators[port_role]

        if format_value == 63:
            is_command_mode = ((frame >> 54) & 0xF) == 0b1111
            fields = [
                (frame >> 42) & 0xFFF,
                (frame >> 30) & 0xFFF,
                (frame >> 18) & 0xFFF,
                (frame >> 6) & 0xFFF,
            ]
            for index, value in enumerate(fields):
                actuator = local_actuators[index]
                if is_command_mode:
                    actuator.target_command = value
                else:
                    actuator.target_position = value
            await self._emit_sensor_batch(port_role)
            return

        if format_value == 1:
            data_request_mask = (frame >> 54) & 0xF
            save_request_mask = (frame >> 50) & 0xF
            if data_request_mask:
                local_index = self._mask_to_index(data_request_mask)
                await self._emit_gain(port_role, local_index)
            if save_request_mask:
                local_index = self._mask_to_index(save_request_mask)
                await self._emit_gain(port_role, local_index)
            return

        if format_value in (10, 20, 30, 40):
            local_index = {10: 0, 20: 1, 30: 2, 40: 3}[format_value]
            actuator = local_actuators[local_index]
            actuator.p_gain = (frame >> 50) & 0xFF
            actuator.i_gain = (frame >> 42) & 0xFF
            actuator.d_gain = (frame >> 34) & 0xFF
            await self._emit_gain(port_role, local_index)
            return

        if format_value == 50:
            local_index = self._mask_to_index((frame >> 54) & 0xF)
            capture_type = (frame >> 52) & 0x3
            actuator = local_actuators[local_index]
            if capture_type == 0b01:
                actuator.capture_min = actuator.position
            elif capture_type == 0b10:
                actuator.capture_max = actuator.position
            await self._emit_gain(port_role, local_index)

    async def _telemetry_loop(self) -> None:
        try:
            while True:
                for port_role, actuators in self._actuators.items():
                    for index, actuator in enumerate(actuators):
                        actuator.position = self._approach_smooth(
                            actuator.position,
                            actuator.target_position,
                            minimum_step=18,
                            response=0.42,
                        )
                        actuator.command = self._approach_smooth(
                            actuator.command,
                            actuator.target_command,
                            minimum_step=24,
                            response=0.5,
                        )
                        actuator.voltage = 1100 + ((actuator.position + index * 97) % 700)
                        actuator.pressure = max(
                            0,
                            min(
                                4095,
                                int(
                                    900
                                    + (actuator.position / 4095) * 2200
                                    + (abs(actuator.command - COMMAND_NEUTRAL) / COMMAND_NEUTRAL) * 700
                                ),
                            ),
                        )
                    await self._emit_sensor_batch(port_role)
                await asyncio.sleep(self.settings.emulate_tick_interval_sec)
        except asyncio.CancelledError:
            raise

    async def _emit_sensor_batch(self, port_role: PortRole) -> None:
        if self._device_callback is None:
            return
        for local_index, actuator in enumerate(self._actuators[port_role]):
            frame = (
                ((FORMAT_SENSOR_BASE + local_index) << 58)
                | (actuator.position << 46)
                | (actuator.voltage << 34)
                | (actuator.command << 22)
                | (actuator.pressure << 10)
            )
            payload = encode_transport_payload(frame, byteorder="little")
            await self._device_callback(DeviceEnvelope(port_role=port_role, payload=payload))

    async def _emit_gain(self, port_role: PortRole, local_index: int) -> None:
        if self._device_callback is None:
            return
        actuator = self._actuators[port_role][local_index]
        format_value = {0: 11, 1: 21, 2: 31, 3: 41}[local_index]
        frame = (
            (format_value << 58)
            | (actuator.p_gain << 50)
            | (actuator.i_gain << 42)
            | (actuator.d_gain << 34)
            | (actuator.capture_max << 22)
            | (actuator.capture_min << 10)
        )
        payload = encode_transport_payload(frame, byteorder="little")
        await self._device_callback(DeviceEnvelope(port_role=port_role, payload=payload))

    @staticmethod
    def _mask_to_index(mask: int) -> int:
        mapping = {0b1000: 0, 0b0100: 1, 0b0010: 2, 0b0001: 3}
        try:
            return mapping[mask]
        except KeyError as exc:
            raise ValueError(f"Unsupported actuator mask: {mask:04b}") from exc

    @staticmethod
    def _approach_smooth(current: int, target: int, *, minimum_step: int, response: float) -> int:
        delta = target - current
        if delta == 0:
            return current

        step = max(minimum_step, int(abs(delta) * response))
        if delta > 0:
            return min(current + step, target)
        if delta < 0:
            return max(current - step, target)
        return current


def build_gateway(settings: Settings) -> SerialGateway:
    if settings.emulate_devices:
        return StubSerialGateway(settings)
    return PySerialGateway(settings)
