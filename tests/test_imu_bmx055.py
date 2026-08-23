from __future__ import annotations

import highend_server.sensors.imu_bmx055 as imu_bmx055
from highend_server.sensors.imu_bmx055 import Bmx055Reader


class _FakeBus:
    def __init__(self, blocks: dict[tuple[int, int], list[int]]) -> None:
        self.writes: list[tuple[int, int, int]] = []
        self.blocks = blocks

    def write_byte_data(self, address: int, register: int, value: int) -> None:
        self.writes.append((address, register, value))

    def read_i2c_block_data(self, address: int, register: int, length: int) -> list[int]:
        return self.blocks[(address, register)][:length]

    def close(self) -> None:
        return None


def _reader_with_blocks(blocks: dict[tuple[int, int], list[int]]) -> Bmx055Reader:
    reader = Bmx055Reader()
    reader._bus = _FakeBus(blocks)  # inject fake bus, bypassing smbus2
    return reader


def test_initialization_writes_expected_registers(monkeypatch) -> None:
    delays: list[float] = []
    monkeypatch.setattr(imu_bmx055, "sleep", delays.append)
    reader = _reader_with_blocks({})
    reader._initialize_accel()
    reader._initialize_gyro()
    reader._initialize_mag()
    writes = reader._bus.writes  # type: ignore[attr-defined]

    # Accel: +/-2g range (0x03) and 125 Hz BW / 250 Hz ODR (0x0C).
    assert (0x18, 0x0F, 0x03) in writes
    assert (0x18, 0x10, 0x0C) in writes
    # Gyro: +/-250 dps (0x03) and 200 Hz ODR / 64 Hz BW (0x06).
    assert (0x68, 0x0F, 0x03) in writes
    assert (0x68, 0x10, 0x06) in writes
    # Mag: power on (0x01) and 30 Hz normal mode (0x38).
    assert (0x10, 0x4B, 0x01) in writes
    assert (0x10, 0x4C, 0x38) in writes
    assert delays == [0.003]


def test_read_scales_and_sign_extends() -> None:
    blocks = {
        # Accel: z = +1024 counts -> +1.0 g (scale 1024). x = y = 0.
        (0x18, 0x02): [0, 0, 0, 0, 0x00, 0x40],
        # Accel temperature: 2 counts * 0.5 + 23 = 24.0 C.
        (0x18, 0x08): [0x02],
        # Gyro: x = -1 count -> -1/131.2 dps (16-bit two's complement).
        (0x68, 0x02): [0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00],
        # Mag: x low/high encode a small positive value.
        (0x10, 0x42): [0x08, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
    }
    reader = _reader_with_blocks(blocks)
    reading = reader.read()

    assert abs(reading.accel_g.z - 1.0) < 1e-9
    assert abs(reading.accel_g.x) < 1e-9
    assert abs(reading.gyro_dps.x - (-1.0 / 131.2)) < 1e-9
    assert reading.temperature_c == 24.0
    # 13-bit mag x: (0x02 << 5) | (0x08 >> 3) = 64 | 1 = 65.
    assert reading.mag_raw.x == 65.0


def test_read_requires_open_bus() -> None:
    reader = Bmx055Reader()
    try:
        reader.read()
    except RuntimeError as exc:
        assert "not open" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError when bus is closed")


def test_reader_serializes_read_and_close() -> None:
    """close() must never interleave with an in-flight read() transaction."""
    import threading
    import time

    read_started = threading.Event()
    release_read = threading.Event()
    order: list[str] = []

    class _BlockingBus(_FakeBus):
        def read_i2c_block_data(self, address: int, register: int, length: int) -> list[int]:
            read_started.set()
            release_read.wait(timeout=2.0)
            return super().read_i2c_block_data(address, register, length)

        def close(self) -> None:
            order.append("close")

    blocks = {
        (0x18, 0x02): [0, 0, 0, 0, 0x00, 0x40],
        (0x18, 0x08): [0x02],
        (0x68, 0x02): [0, 0, 0, 0, 0, 0],
        (0x10, 0x42): [0, 0, 0, 0, 0, 0, 0, 0],
    }
    reader = Bmx055Reader()
    assert isinstance(reader._lock, type(threading.Lock()))
    reader._bus = _BlockingBus(blocks)

    def do_read() -> None:
        reader.read()
        order.append("read-done")

    read_thread = threading.Thread(target=do_read)
    read_thread.start()
    assert read_started.wait(timeout=2.0)

    close_thread = threading.Thread(target=reader.close)
    close_thread.start()
    # close() must block behind the in-flight read (lock held by the reader).
    time.sleep(0.05)
    assert order == []  # neither finished yet: close is waiting on the lock

    release_read.set()
    read_thread.join(timeout=2.0)
    close_thread.join(timeout=2.0)
    assert order == ["read-done", "close"]
    assert reader._bus is None
