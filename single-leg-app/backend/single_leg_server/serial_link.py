from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress

import serial

from single_leg_server.config import Settings
from single_leg_server.models import ConnectionState, ControlMode
from single_leg_server.protocol import encode_transport_payload

FrameCallback = Callable[[bytes], Awaitable[None]]
StateCallback = Callable[[ConnectionState], Awaitable[None]]


class SerialLink:
    """One-port serial transport with quiet background reconnects."""

    def __init__(
        self,
        settings: Settings,
        on_frame: FrameCallback,
        on_state: StateCallback,
    ) -> None:
        self.settings = settings
        self.on_frame = on_frame
        self.on_state = on_state
        self.connection_state = ConnectionState.CONNECTING
        self.last_error: str | None = None
        self._connection: serial.Serial | None = None
        self._task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        await self._set_state(ConnectionState.CONNECTING)
        self._task = asyncio.create_task(self._run(), name="single-leg-serial")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._close_connection()
        await self._set_state(ConnectionState.DISCONNECTED)

    async def send_frame(self, frame: int) -> None:
        connection = self._connection
        if connection is None or not connection.is_open:
            raise ConnectionError("ESP32 is not connected")
        payload = encode_transport_payload(frame, byteorder="big")
        async with self._write_lock:
            try:
                await asyncio.to_thread(connection.write, payload)
            except (serial.SerialException, OSError) as exc:
                self.last_error = str(exc)
                await self._close_connection()
                await self._set_state(ConnectionState.CONNECTING)
                raise ConnectionError("ESP32 connection was lost") from exc

    async def _run(self) -> None:
        while not self._stopping:
            if self._connection is None:
                await self._try_connect()
                if self._connection is None:
                    await asyncio.sleep(self.settings.reconnect_interval_sec)
                    continue

            try:
                line = await asyncio.to_thread(self._connection.readline)
            except (serial.SerialException, OSError, AttributeError) as exc:
                self.last_error = str(exc)
                await self._close_connection()
                await self._set_state(ConnectionState.CONNECTING)
                continue

            token = line.strip()
            if token:
                await self.on_frame(token)

    async def _try_connect(self) -> None:
        try:
            connection = await asyncio.to_thread(
                serial.Serial,
                port=self.settings.port_name,
                baudrate=self.settings.serial_baudrate,
                timeout=self.settings.serial_timeout_sec,
                write_timeout=self.settings.serial_write_timeout_sec,
            )
        except (serial.SerialException, OSError, ValueError) as exc:
            self.last_error = str(exc)
            await self._set_state(ConnectionState.CONNECTING)
            return

        self._connection = connection
        self.last_error = None
        with suppress(serial.SerialException):
            await asyncio.to_thread(connection.reset_input_buffer)
        await self._set_state(ConnectionState.CONNECTED)

    async def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None and connection.is_open:
            with suppress(serial.SerialException):
                await asyncio.to_thread(connection.close)

    async def _set_state(self, state: ConnectionState) -> None:
        changed = state is not self.connection_state
        self.connection_state = state
        if changed:
            await self.on_state(state)


class EmulatedSerialLink:
    """One ESP32 emulator exposing only local actuator channels 0 and 1."""

    def __init__(
        self,
        settings: Settings,
        on_frame: FrameCallback,
        on_state: StateCallback,
    ) -> None:
        self.settings = settings
        self.on_frame = on_frame
        self.on_state = on_state
        self.connection_state = ConnectionState.EMULATED
        self.last_error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._position = [2048, 2048]
        self._target_position = [2048, 2048]
        self._command = [900, 900]
        self._target_command = [900, 900]
        self._gains = [[12, 2, 4], [12, 2, 4]]
        self._capture = [[900, 3200], [900, 3200]]

    async def start(self) -> None:
        self.connection_state = ConnectionState.EMULATED
        await self.on_state(self.connection_state)
        self._task = asyncio.create_task(self._telemetry_loop(), name="single-leg-emulator")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def send_frame(self, frame: int) -> None:
        format_value = (frame >> 58) & 0x3F
        if format_value == 63:
            mode = (
                ControlMode.COMMAND if ((frame >> 54) & 0xF) == 0b1111 else ControlMode.POSITION
            )
            fields = [
                (frame >> 42) & 0xFFF,
                (frame >> 30) & 0xFFF,
                (frame >> 18) & 0xFFF,
                (frame >> 6) & 0xFFF,
            ]
            if mode is ControlMode.POSITION:
                self._target_position[:] = fields[:2]
            else:
                self._target_command[:] = fields[:2]
            return

        if format_value == 1:
            request_mask = (frame >> 54) & 0xF
            save_mask = (frame >> 50) & 0xF
            mask = request_mask or save_mask
            if mask:
                await self._emit_gain(_mask_to_index(mask))
            return

        if format_value in (10, 20):
            index = {10: 0, 20: 1}[format_value]
            self._gains[index] = [
                (frame >> 50) & 0xFF,
                (frame >> 42) & 0xFF,
                (frame >> 34) & 0xFF,
            ]
            await self._emit_gain(index)
            return

        if format_value == 50:
            index = _mask_to_index((frame >> 54) & 0xF)
            if index > 1:
                return
            capture_type = (frame >> 52) & 0x3
            if capture_type == 0b01:
                self._capture[index][0] = self._position[index]
            elif capture_type == 0b10:
                self._capture[index][1] = self._position[index]
            await self._emit_gain(index)

    async def _telemetry_loop(self) -> None:
        while True:
            for index in range(2):
                self._position[index] = _approach(
                    self._position[index], self._target_position[index]
                )
                self._command[index] = _approach(
                    self._command[index], self._target_command[index]
                )
                voltage = 1200 + (self._position[index] % 520)
                pressure = min(4095, 700 + self._position[index] // 2)
                frame = (
                    ((5 + index) << 58)
                    | (self._position[index] << 46)
                    | (voltage << 34)
                    | (self._command[index] << 22)
                    | (pressure << 10)
                )
                await self.on_frame(encode_transport_payload(frame, byteorder="little").strip())
            await asyncio.sleep(self.settings.emulate_tick_interval_sec)

    async def _emit_gain(self, index: int) -> None:
        if index > 1:
            return
        p_gain, i_gain, d_gain = self._gains[index]
        capture_min, capture_max = self._capture[index]
        frame = (
            ({0: 11, 1: 21}[index] << 58)
            | (p_gain << 50)
            | (i_gain << 42)
            | (d_gain << 34)
            | (capture_max << 22)
            | (capture_min << 10)
        )
        await self.on_frame(encode_transport_payload(frame, byteorder="little").strip())


def build_serial_link(
    settings: Settings,
    on_frame: FrameCallback,
    on_state: StateCallback,
) -> SerialLink | EmulatedSerialLink:
    if settings.emulate_devices:
        return EmulatedSerialLink(settings, on_frame, on_state)
    return SerialLink(settings, on_frame, on_state)


def _mask_to_index(mask: int) -> int:
    return {0b1000: 0, 0b0100: 1, 0b0010: 2, 0b0001: 3}.get(mask, 3)


def _approach(current: int, target: int) -> int:
    delta = target - current
    if delta == 0:
        return current
    step = max(12, int(abs(delta) * 0.32))
    return min(current + step, target) if delta > 0 else max(current - step, target)
