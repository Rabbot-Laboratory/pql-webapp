from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Mcp3204Reading:
    channel: int
    raw: int
    voltage: float


class Mcp3204Reader:
    """Small MCP3204 SPI reader for Raspberry Pi spidev devices."""

    def __init__(
        self,
        *,
        bus: int,
        device: int,
        vref: float = 3.3,
        max_speed_hz: int = 1_000_000,
    ) -> None:
        self.bus = bus
        self.device = device
        self.vref = vref
        self.max_speed_hz = max_speed_hz
        self._spi = None

    def open(self) -> None:
        try:
            import spidev  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "spidev is not installed. Install with `pip install spidev`."
            ) from exc

        spi = spidev.SpiDev()
        spi.open(self.bus, self.device)
        spi.max_speed_hz = self.max_speed_hz
        spi.mode = 0
        self._spi = spi

    def close(self) -> None:
        if self._spi is not None:
            self._spi.close()
            self._spi = None

    def read_channel(self, channel: int) -> Mcp3204Reading:
        if not 0 <= channel <= 3:
            raise ValueError("MCP3204 channel must be 0..3")
        if self._spi is None:
            raise RuntimeError("MCP3204 SPI device is not open")

        # MCP3204 single-ended read: start bit, single-ended bit, channel bits.
        command = [0x06 | ((channel & 0x04) >> 2), (channel & 0x03) << 6, 0x00]
        response = self._spi.xfer2(command)
        raw = ((response[1] & 0x0F) << 8) | response[2]
        return Mcp3204Reading(channel=channel, raw=raw, voltage=(raw / 4095.0) * self.vref)

    def read_all(self) -> list[Mcp3204Reading]:
        return [self.read_channel(channel) for channel in range(4)]
