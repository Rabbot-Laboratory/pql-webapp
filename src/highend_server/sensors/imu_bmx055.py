from __future__ import annotations

from dataclasses import dataclass


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

    def open(self) -> None:
        try:
            from smbus2 import SMBus  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "smbus2 is not installed. Install with `pip install smbus2`."
            ) from exc

        self._bus = SMBus(self.bus_number)
        self._initialize_accel()
        self._initialize_gyro()
        self._initialize_mag()

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()
            self._bus = None

    def read(self) -> Bmx055Reading:
        if self._bus is None:
            raise RuntimeError("BMX055 I2C bus is not open")
        return Bmx055Reading(
            accel_g=self._read_accel(),
            gyro_dps=self._read_gyro(),
            mag_raw=self._read_mag_raw(),
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
        # PMU range: +/-2g, bandwidth: 125Hz.
        self._write_byte(self.accel_address, 0x0F, 0x03)
        self._write_byte(self.accel_address, 0x10, 0x0C)

    def _initialize_gyro(self) -> None:
        # Range: +/-250 dps, bandwidth: 100Hz.
        self._write_byte(self.gyro_address, 0x0F, 0x03)
        self._write_byte(self.gyro_address, 0x10, 0x07)

    def _initialize_mag(self) -> None:
        # Power on BMM150 and enter normal measurement mode.
        self._write_byte(self.mag_address, 0x4B, 0x01)
        self._write_byte(self.mag_address, 0x4C, 0x00)
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
