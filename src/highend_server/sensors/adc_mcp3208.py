from __future__ import annotations

from dataclasses import dataclass

MCP3208_CHANNEL_COUNT = 8
MCP3208_ADC_DIVISOR = 4096.0


@dataclass(slots=True)
class Mcp3208Reading:
    channel: int
    raw: int
    voltage: float


class Mcp3208Reader:
    """MCP3208 single-ended SPI reader for Raspberry Pi spidev devices."""

    def __init__(
        self,
        *,
        bus: int,
        device: int,
        vref: float = 5.0,
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

    def read_channel(self, channel: int) -> Mcp3208Reading:
        if not 0 <= channel < MCP3208_CHANNEL_COUNT:
            raise ValueError("MCP3208 channel must be 0..7")
        if self._spi is None:
            raise RuntimeError("MCP3208 SPI device is not open")

        # MCP3208 single-ended transfer: start + SGL/DIFF + channel bit 2,
        # followed by channel bits 1..0 and one dummy byte.
        command = [0x06 | (channel >> 2), (channel & 0x03) << 6, 0x00]
        response = self._spi.xfer2(command)
        if len(response) != 3:
            raise RuntimeError(f"MCP3208 returned {len(response)} bytes; expected 3")

        raw = ((response[1] & 0x0F) << 8) | response[2]
        voltage = (raw / MCP3208_ADC_DIVISOR) * self.vref
        return Mcp3208Reading(channel=channel, raw=raw, voltage=voltage)

    def read_all(self) -> list[Mcp3208Reading]:
        return [self.read_channel(channel) for channel in range(MCP3208_CHANNEL_COUNT)]
