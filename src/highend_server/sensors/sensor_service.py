from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from math import atan2, degrees, sqrt

from highend_server.config import Settings
from highend_server.domain.models import (
    AdcBankState,
    AdcChannelState,
    Bmx055State,
    ImuCalibration,
    ImuOrientation,
    ImuVector,
    SensorConnectionState,
    SensorState,
    TelemetryEvent,
)
from highend_server.sensors.adc_mcp3204 import Mcp3204Reader
from highend_server.sensors.imu_bmx055 import Bmx055Reader, Bmx055Reading, Vector3

EventSink = Callable[[TelemetryEvent], Awaitable[None]]
logger = logging.getLogger(__name__)


def _model_vector(vector: Vector3) -> ImuVector:
    return ImuVector(x=vector.x, y=vector.y, z=vector.z)


def _zero_vector() -> ImuVector:
    return ImuVector(x=0.0, y=0.0, z=0.0)


def _orientation_from_accel(accel: Vector3) -> ImuOrientation:
    roll = degrees(atan2(accel.y, accel.z))
    pitch = degrees(atan2(-accel.x, sqrt(accel.y * accel.y + accel.z * accel.z)))
    return ImuOrientation(roll_deg=roll, pitch_deg=pitch)


class SensorService:
    def __init__(self, settings: Settings, event_sink: EventSink) -> None:
        self.settings = settings
        self.event_sink = event_sink
        self._task: asyncio.Task[None] | None = None
        self._imu_reader: Bmx055Reader | None = None
        self._adc_readers: list[tuple[int, Mcp3204Reader]] = []
        self._imu_calibration = self._load_imu_calibration()
        self._state = self._disabled_state()

    @property
    def state(self) -> SensorState:
        return self._state.model_copy(deep=True)

    async def calibrate_level(self) -> SensorState:
        orientation = self._state.imu.raw_orientation
        if orientation is None:
            reading = await asyncio.to_thread(self._read_imu_direct)
            orientation = _orientation_from_accel(reading.accel_g)

        self._imu_calibration.level_roll_deg = orientation.roll_deg
        self._imu_calibration.level_pitch_deg = orientation.pitch_deg
        self._imu_calibration.updated_at = datetime.now(UTC)
        self._save_imu_calibration()
        self._state.imu.calibration = self._imu_calibration
        self._read_once()
        await self._publish()
        return self.state

    async def calibrate_gyro_zero(self, sample_count: int = 60) -> SensorState:
        total = _zero_vector()
        for _ in range(sample_count):
            reading = await asyncio.to_thread(self._read_imu_direct)
            total.x += reading.gyro_dps.x
            total.y += reading.gyro_dps.y
            total.z += reading.gyro_dps.z
            await asyncio.sleep(min(self.settings.sensor_poll_interval_sec, 0.02))

        self._imu_calibration.gyro_offset_dps = ImuVector(
            x=total.x / sample_count,
            y=total.y / sample_count,
            z=total.z / sample_count,
        )
        self._imu_calibration.updated_at = datetime.now(UTC)
        self._save_imu_calibration()
        self._state.imu.calibration = self._imu_calibration
        self._read_once()
        await self._publish()
        return self.state

    async def reset_imu_calibration(self) -> SensorState:
        self._imu_calibration = ImuCalibration()
        self._save_imu_calibration()
        self._state.imu.calibration = self._imu_calibration
        self._read_once()
        await self._publish()
        return self.state

    async def start(self) -> None:
        if not self.settings.sensors_enabled:
            self._state = self._disabled_state()
            return

        self._state.enabled = True
        self._state.imu.connection_state = SensorConnectionState.CONNECTING
        for bank in self._state.adc_banks:
            bank.connection_state = SensorConnectionState.CONNECTING

        self._open_devices()
        await self._publish()

        self._task = asyncio.create_task(self._poll_loop(), name="sensor-poll")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._close_devices()

    def _disabled_state(self) -> SensorState:
        adc_banks = [
            AdcBankState(
                bus=self.settings.adc_spi_bus,
                device=device,
                channels=[
                    AdcChannelState(bank=bank_index, channel=channel)
                    for channel in range(4)
                ],
            )
            for bank_index, device in enumerate(self._adc_devices())
        ]
        return SensorState(
            enabled=self.settings.sensors_enabled,
            imu=Bmx055State(calibration=self._imu_calibration),
            adc_banks=adc_banks,
        )

    def _load_imu_calibration(self) -> ImuCalibration:
        path = self.settings.imu_calibration_path
        if not path.exists():
            return ImuCalibration()
        try:
            return ImuCalibration.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load IMU calibration from %s", path)
            return ImuCalibration()

    def _save_imu_calibration(self) -> None:
        path = self.settings.imu_calibration_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._imu_calibration.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def _adc_devices(self) -> list[int]:
        devices: list[int] = []
        for item in self.settings.adc_spi_devices.split(","):
            stripped = item.strip()
            if stripped == "":
                continue
            devices.append(int(stripped))
        return devices

    def _open_devices(self) -> None:
        self._close_devices()
        self._imu_reader = Bmx055Reader(
            bus=self.settings.sensor_i2c_bus,
            accel_address=self.settings.bmx055_accel_address,
            gyro_address=self.settings.bmx055_gyro_address,
            mag_address=self.settings.bmx055_mag_address,
        )
        try:
            self._imu_reader.open()
        except Exception as exc:
            logger.exception("BMX055 initialization failed")
            self._imu_reader.close()
            self._imu_reader = None
            self._state.imu.connection_state = SensorConnectionState.ERROR
            self._state.imu.error = str(exc)

        self._adc_readers = []
        for bank_index, device in enumerate(self._adc_devices()):
            reader = Mcp3204Reader(
                bus=self.settings.adc_spi_bus,
                device=device,
                vref=self.settings.adc_vref,
                max_speed_hz=self.settings.adc_spi_max_speed_hz,
            )
            try:
                reader.open()
            except Exception as exc:
                logger.exception("MCP3204 initialization failed on SPI device %s", device)
                reader.close()
                if bank_index < len(self._state.adc_banks):
                    self._state.adc_banks[bank_index].connection_state = SensorConnectionState.ERROR
                    self._state.adc_banks[bank_index].error = str(exc)
                continue
            self._adc_readers.append((bank_index, reader))

    def _close_devices(self) -> None:
        if self._imu_reader is not None:
            self._imu_reader.close()
            self._imu_reader = None
        for _, reader in self._adc_readers:
            reader.close()
        self._adc_readers = []

    def _read_imu_direct(self) -> Bmx055Reading:
        if self._imu_reader is None:
            raise RuntimeError("BMX055 IMU is not connected")
        return self._imu_reader.read()

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.to_thread(self._read_once)
            await self._publish()
            await asyncio.sleep(self.settings.sensor_poll_interval_sec)

    def _read_once(self) -> None:
        now = datetime.now(UTC)
        self._state.enabled = True
        self._state.updated_at = now

        if self._imu_reader is not None:
            try:
                reading = self._read_imu_direct()
                raw_orientation = _orientation_from_accel(reading.accel_g)
                corrected_orientation = ImuOrientation(
                    roll_deg=raw_orientation.roll_deg - self._imu_calibration.level_roll_deg,
                    pitch_deg=raw_orientation.pitch_deg - self._imu_calibration.level_pitch_deg,
                )
                corrected_gyro = ImuVector(
                    x=reading.gyro_dps.x - self._imu_calibration.gyro_offset_dps.x,
                    y=reading.gyro_dps.y - self._imu_calibration.gyro_offset_dps.y,
                    z=reading.gyro_dps.z - self._imu_calibration.gyro_offset_dps.z,
                )
                self._state.imu = Bmx055State(
                    connection_state=SensorConnectionState.CONNECTED,
                    accel_g=_model_vector(reading.accel_g),
                    gyro_dps=corrected_gyro,
                    mag_raw=_model_vector(reading.mag_raw),
                    raw_orientation=raw_orientation,
                    orientation=corrected_orientation,
                    calibration=self._imu_calibration,
                    temperature_c=reading.temperature_c,
                    updated_at=now,
                )
            except Exception as exc:
                self._state.imu.connection_state = SensorConnectionState.ERROR
                self._state.imu.error = str(exc)
                self._state.imu.updated_at = now

        adc_banks = [bank.model_copy(deep=True) for bank in self._state.adc_banks]
        for bank_index, reader in self._adc_readers:
            try:
                readings = reader.read_all()
                adc_banks[bank_index] = (
                    AdcBankState(
                        bus=reader.bus,
                        device=reader.device,
                        connection_state=SensorConnectionState.CONNECTED,
                        channels=[
                            AdcChannelState(
                                bank=bank_index,
                                channel=item.channel,
                                raw=item.raw,
                                voltage=item.voltage,
                            )
                            for item in readings
                        ],
                        updated_at=now,
                    )
                )
            except Exception as exc:
                adc_banks[bank_index] = (
                    AdcBankState(
                        bus=reader.bus,
                        device=reader.device,
                        connection_state=SensorConnectionState.ERROR,
                        error=str(exc),
                        channels=[
                            AdcChannelState(bank=bank_index, channel=channel)
                            for channel in range(4)
                        ],
                        updated_at=now,
                    )
                )
        self._state.adc_banks = adc_banks

    async def _publish(self) -> None:
        await self.event_sink(
            TelemetryEvent(
                type="sensor_state",
                payload={"sensors": self._state.model_dump(mode="json")},
            )
        )
