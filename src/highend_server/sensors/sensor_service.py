from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from math import atan2, cos, degrees, pi, radians, sin, sqrt
from time import monotonic

from highend_server.config import Settings
from highend_server.domain.models import (
    AdcBankState,
    AdcChannelState,
    Bmx055State,
    ImuCalibration,
    ImuOrientation,
    ImuQuaternion,
    ImuVector,
    SensorConnectionState,
    SensorState,
    TelemetryEvent,
)
from highend_server.sensors.adc_mcp3204 import Mcp3204Reader
from highend_server.sensors.attitude import (
    DEG_TO_RAD,
    FusedAttitude,
    MahonyMARG,
    Quaternion,
    attitude_from_quaternion,
    euler_to_quat,
)
from highend_server.sensors.imu_bmx055 import Bmx055Reader, Bmx055Reading, Vector3

EventSink = Callable[[TelemetryEvent], Awaitable[None]]
logger = logging.getLogger(__name__)


def _model_vector(vector: Vector3) -> ImuVector:
    return ImuVector(x=vector.x, y=vector.y, z=vector.z)


def _model_quaternion(quaternion: Quaternion) -> ImuQuaternion:
    return ImuQuaternion(w=quaternion.w, x=quaternion.x, y=quaternion.y, z=quaternion.z)


def _zero_vector() -> ImuVector:
    return ImuVector(x=0.0, y=0.0, z=0.0)


def _orientation_from_accel(accel: Vector3) -> ImuOrientation:
    roll = degrees(atan2(accel.y, accel.z))
    pitch = degrees(atan2(-accel.x, sqrt(accel.y * accel.y + accel.z * accel.z)))
    return ImuOrientation(roll_deg=roll, pitch_deg=pitch)


def _orientation_from_attitude(
    attitude: FusedAttitude,
    calibration: ImuCalibration | None = None,
) -> ImuOrientation:
    roll_deg = attitude.euler.roll_deg
    pitch_deg = attitude.euler.pitch_deg
    if calibration is not None:
        roll_deg -= calibration.level_roll_deg
        pitch_deg -= calibration.level_pitch_deg
    return ImuOrientation(
        roll_deg=roll_deg,
        pitch_deg=pitch_deg,
        yaw_deg=attitude.euler.yaw_deg,
    )


class SensorService:
    def __init__(self, settings: Settings, event_sink: EventSink) -> None:
        self.settings = settings
        self.event_sink = event_sink
        self._task: asyncio.Task[None] | None = None
        self._imu_reader: Bmx055Reader | None = None
        self._adc_readers: list[tuple[int, Mcp3204Reader]] = []
        self._imu_calibration = self._load_imu_calibration()
        self._state = self._disabled_state()
        self._demo_started_at = monotonic()
        self._attitude_filter = MahonyMARG()
        self._last_imu_read_at = monotonic()

    @property
    def _use_emulated_sensors(self) -> bool:
        return self.settings.emulate_devices and not self.settings.sensors_enabled

    @property
    def state(self) -> SensorState:
        return self._state.model_copy(deep=True)

    async def calibrate_level(self) -> SensorState:
        orientation = self._state.imu.raw_orientation
        if orientation is None and self._use_emulated_sensors:
            self._read_once()
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
        if self._use_emulated_sensors:
            self._imu_calibration.gyro_offset_dps = self._state.imu.gyro_dps or _zero_vector()
            self._imu_calibration.updated_at = datetime.now(UTC)
            self._save_imu_calibration()
            self._state.imu.calibration = self._imu_calibration
            self._read_once()
            await self._publish()
            return self.state

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
        self._reset_attitude_filter()
        self._read_once()
        await self._publish()
        return self.state

    async def reset_imu_calibration(self) -> SensorState:
        self._imu_calibration = ImuCalibration()
        self._save_imu_calibration()
        self._state.imu.calibration = self._imu_calibration
        self._reset_attitude_filter()
        self._read_once()
        await self._publish()
        return self.state

    async def start(self) -> None:
        if not self.settings.sensors_enabled and not self._use_emulated_sensors:
            self._state = self._disabled_state()
            return

        self._state.enabled = True
        self._state.imu.connection_state = SensorConnectionState.CONNECTING
        for bank in self._state.adc_banks:
            bank.connection_state = SensorConnectionState.CONNECTING

        if self._use_emulated_sensors:
            self._demo_started_at = monotonic()
            self._reset_attitude_filter()
        else:
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
            self._reset_attitude_filter()
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

    def _reset_attitude_filter(self) -> None:
        self._attitude_filter = MahonyMARG()
        self._last_imu_read_at = monotonic()

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.to_thread(self._read_once)
            await self._publish()
            await asyncio.sleep(self.settings.sensor_poll_interval_sec)

    def _read_once(self) -> None:
        if self._use_emulated_sensors:
            self._read_emulated_once()
            return

        now = datetime.now(UTC)
        self._state.enabled = True
        self._state.updated_at = now

        if self._imu_reader is not None:
            try:
                reading = self._read_imu_direct()
                corrected_gyro = ImuVector(
                    x=reading.gyro_dps.x - self._imu_calibration.gyro_offset_dps.x,
                    y=reading.gyro_dps.y - self._imu_calibration.gyro_offset_dps.y,
                    z=reading.gyro_dps.z - self._imu_calibration.gyro_offset_dps.z,
                )
                current_time = monotonic()
                dt = max(0.001, min(0.2, current_time - self._last_imu_read_at))
                self._last_imu_read_at = current_time
                attitude = self._attitude_filter.update(
                    gyro_rad=Vector3(
                        corrected_gyro.x * DEG_TO_RAD,
                        corrected_gyro.y * DEG_TO_RAD,
                        corrected_gyro.z * DEG_TO_RAD,
                    ),
                    accel_g=reading.accel_g,
                    mag_raw=reading.mag_raw,
                    dt=dt,
                )
                raw_orientation = _orientation_from_attitude(attitude)
                corrected_orientation = _orientation_from_attitude(attitude, self._imu_calibration)
                self._state.imu = Bmx055State(
                    connection_state=SensorConnectionState.CONNECTED,
                    quaternion=_model_quaternion(attitude.quaternion),
                    accel_g=_model_vector(reading.accel_g),
                    gyro_dps=corrected_gyro,
                    mag_raw=_model_vector(reading.mag_raw),
                    gravity_g=_model_vector(attitude.gravity_g),
                    linear_accel_g=_model_vector(attitude.linear_accel_g),
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

    def _read_emulated_once(self) -> None:
        now = datetime.now(UTC)
        elapsed = monotonic() - self._demo_started_at

        roll_deg = 12.0 * sin(elapsed * 0.85)
        pitch_deg = 8.0 * sin(elapsed * 0.57 + 0.9)
        yaw_deg = (elapsed * 20.0) % 360.0
        roll = radians(roll_deg)
        pitch = radians(pitch_deg)

        quaternion = euler_to_quat(roll_deg, pitch_deg, yaw_deg)
        measured_accel = Vector3(
            x=-sin(pitch) + 0.015 * sin(elapsed * 4.2),
            y=sin(roll) * cos(pitch) + 0.012 * sin(elapsed * 3.1),
            z=cos(roll) * cos(pitch) + 0.01 * sin(elapsed * 2.3),
        )
        attitude = attitude_from_quaternion(
            quaternion,
            measured_accel,
        )
        raw_orientation = _orientation_from_attitude(attitude)
        corrected_orientation = _orientation_from_attitude(attitude, self._imu_calibration)
        gyro = ImuVector(
            x=10.2 * cos(elapsed * 0.85),
            y=4.6 * cos(elapsed * 0.57 + 0.9),
            z=6.0 * sin(elapsed * 0.33),
        )
        corrected_gyro = ImuVector(
            x=gyro.x - self._imu_calibration.gyro_offset_dps.x,
            y=gyro.y - self._imu_calibration.gyro_offset_dps.y,
            z=gyro.z - self._imu_calibration.gyro_offset_dps.z,
        )

        self._state.enabled = True
        self._state.updated_at = now
        self._state.imu = Bmx055State(
            connection_state=SensorConnectionState.CONNECTED,
            quaternion=_model_quaternion(attitude.quaternion),
            accel_g=_model_vector(measured_accel),
            gyro_dps=corrected_gyro,
            mag_raw=ImuVector(
                x=32.0 * cos(radians(yaw_deg)),
                y=32.0 * sin(radians(yaw_deg)),
                z=-7.0 + 2.0 * sin(elapsed * 0.21),
            ),
            gravity_g=_model_vector(attitude.gravity_g),
            linear_accel_g=_model_vector(attitude.linear_accel_g),
            raw_orientation=raw_orientation,
            orientation=corrected_orientation,
            calibration=self._imu_calibration,
            temperature_c=31.0 + 1.5 * sin(elapsed * 0.12),
            updated_at=now,
        )

        adc_banks: list[AdcBankState] = []
        for bank_index, device in enumerate(self._adc_devices()):
            channels: list[AdcChannelState] = []
            for channel in range(4):
                phase = elapsed * (0.45 + channel * 0.08) + bank_index * pi * 0.5 + channel
                raw = int(max(0, min(4095, 1900 + 850 * sin(phase) + 180 * sin(phase * 2.7))))
                channels.append(
                    AdcChannelState(
                        bank=bank_index,
                        channel=channel,
                        raw=raw,
                        voltage=(raw / 4095.0) * self.settings.adc_vref,
                    )
                )
            adc_banks.append(
                AdcBankState(
                    bus=self.settings.adc_spi_bus,
                    device=device,
                    connection_state=SensorConnectionState.CONNECTED,
                    channels=channels,
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
