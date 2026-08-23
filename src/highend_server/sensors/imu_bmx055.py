from __future__ import annotations

import threading
from dataclasses import dataclass
from time import sleep


def _sign_extend(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return (value ^ sign_bit) - sign_bit


@dataclass(frozen=True, slots=True)
class Vector3:
    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class Bmx055Reading:
    accel_g: Vector3
    gyro_dps: Vector3
    mag_raw: Vector3
    temperature_c: float | None = None


class Bmx055Reader:
    """BMX055 I2C reader for CJMCU-055 style breakout boards.

    BMX055 exposes accelerometer, gyroscope, and magnetometer as separate I2C
    devices. This reader initializes conservative ranges and returns scaled
    accel/gyro plus raw magnetometer values for first-stage validation.
    """

    def __init__(
        self,
        *,
        bus: int = 1,
        accel_address: int = 0x18,
        gyro_address: int = 0x68,
        mag_address: int = 0x10,
    ) -> None:
        self.bus_number = bus
        self.accel_address = accel_address
        self.gyro_address = gyro_address
        self.mag_address = mag_address
        self._bus = None
        # Serializes open/close/read transactions so a close() from the asyncio
        # shutdown path can never interleave with an in-flight read on the IMU
        # thread (which would close the fd mid-transaction).
        self._lock = threading.Lock()

    def open(self) -> None:
        try:
            from smbus2 import SMBus  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "smbus2 is not installed. Install with `pip install smbus2`."
            ) from exc

        with self._lock:
            self._bus = SMBus(self.bus_number)
            self._initialize_accel()
            self._initialize_gyro()
            self._initialize_mag()

    def close(self) -> None:
        with self._lock:
            if self._bus is not None:
                self._bus.close()
                self._bus = None

    def read(self) -> Bmx055Reading:
        with self._lock:
            if self._bus is None:
                raise RuntimeError("BMX055 I2C bus is not open")
            return Bmx055Reading(
                accel_g=self._read_accel(),
                gyro_dps=self._read_gyro(),
                mag_raw=self._read_mag_raw(),
                temperature_c=self._read_temperature(),
            )

    def _write_byte(self, address: int, register: int, value: int) -> None:
        if self._bus is None:
            raise RuntimeError("BMX055 I2C bus is not open")
        self._bus.write_byte_data(address, register, value)

    def _read_block(self, address: int, register: int, length: int) -> list[int]:
        if self._bus is None:
            raise RuntimeError("BMX055 I2C bus is not open")
        return list(self._bus.read_i2c_block_data(address, register, length))

    def _initialize_accel(self) -> None:
        # Accelerometer PMU config for the ~100 Hz control pipeline.
        # 0x0F (PMU_RANGE) = 0x03 -> +/-2 g full scale (LSB = 1/1024 g, see scale).
        self._write_byte(self.accel_address, 0x0F, 0x03)
        # 0x10 (PMU_BW) = 0x0C -> filter bandwidth 125 Hz, which corresponds to an
        # output data rate of 250 Hz (ODR = 2 x BW). That is >= 2x the 100 Hz
        # sampling loop, giving margin against aliasing while limiting noise.
        self._write_byte(self.accel_address, 0x10, 0x0C)

    def _initialize_gyro(self) -> None:
        # Gyroscope config for the ~100 Hz control pipeline.
        # 0x0F (RANGE) = 0x03 -> +/-250 dps full scale (LSB = 1/131.2 dps).
        self._write_byte(self.gyro_address, 0x0F, 0x03)
        # 0x10 (BW) = 0x06 -> 200 Hz ODR / 64 Hz filter bandwidth. Provides a
        # fresh gyro sample every ~5 ms (2x the 100 Hz loop) with a 64 Hz filter
        # that preserves the dynamics needed for attitude control.
        self._write_byte(self.gyro_address, 0x10, 0x06)

    def _initialize_mag(self) -> None:
        # BMM150 magnetometer. It cannot sustain 100 Hz, so it is read at a lower
        # rate by the pipeline; here we configure a data rate high enough to feed
        # those decimated reads with fresh samples.
        # 0x4B (power control) = 0x01 -> power on (leave suspend mode).
        self._write_byte(self.mag_address, 0x4B, 0x01)
        sleep(0.003)
        # 0x4C (op mode / ODR) = 0x38 -> data rate 30 Hz, opmode = normal.
        # bits[5:3]=111 (30 Hz), bits[2:1]=00 (normal). 30 Hz comfortably feeds a
        # ~20 Hz magnetometer read cadence.
        self._write_byte(self.mag_address, 0x4C, 0x38)
        # Repetition settings (noise vs. speed trade-off) from the datasheet
        # "regular" preset: 0x4E enables all axes, 0x51/0x52 set XY/Z reps.
        self._write_byte(self.mag_address, 0x4E, 0x84)
        self._write_byte(self.mag_address, 0x51, 0x04)
        self._write_byte(self.mag_address, 0x52, 0x16)

    def _read_accel(self) -> Vector3:
        data = self._read_block(self.accel_address, 0x02, 6)
        x = _sign_extend((data[1] << 4) | (data[0] >> 4), 12)
        y = _sign_extend((data[3] << 4) | (data[2] >> 4), 12)
        z = _sign_extend((data[5] << 4) | (data[4] >> 4), 12)
        scale = 1024.0
        return Vector3(x=x / scale, y=y / scale, z=z / scale)

    def _read_gyro(self) -> Vector3:
        data = self._read_block(self.gyro_address, 0x02, 6)
        x = _sign_extend((data[1] << 8) | data[0], 16)
        y = _sign_extend((data[3] << 8) | data[2], 16)
        z = _sign_extend((data[5] << 8) | data[4], 16)
        scale = 131.2
        return Vector3(x=x / scale, y=y / scale, z=z / scale)

    def _read_mag_raw(self) -> Vector3:
        data = self._read_block(self.mag_address, 0x42, 8)
        x = _sign_extend((data[1] << 5) | (data[0] >> 3), 13)
        y = _sign_extend((data[3] << 5) | (data[2] >> 3), 13)
        z = _sign_extend((data[5] << 7) | (data[4] >> 1), 15)
        return Vector3(x=float(x), y=float(y), z=float(z))

    def _read_temperature(self) -> float:
        # Accelerometer die temperature (register 0x08). The value is a signed
        # 8-bit count with 0.5 degC/LSB and a 23 degC offset at 0x00.
        data = self._read_block(self.accel_address, 0x08, 1)
        raw = _sign_extend(data[0], 8)
        return 23.0 + raw * 0.5
