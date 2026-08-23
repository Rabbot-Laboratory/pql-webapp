from __future__ import annotations

import pytest

from highend_server.sensors.adc_mcp3208 import (
    MCP3208_ADC_DIVISOR,
    MCP3208_CHANNEL_COUNT,
    Mcp3208Reader,
)


class FakeSpi:
    def __init__(self, response: list[int] | None = None) -> None:
        self.response = response or [0x00, 0x0A, 0xBC]
        self.commands: list[list[int]] = []

    def xfer2(self, command: list[int]) -> list[int]:
        self.commands.append(command)
        return list(self.response)


def reader_with_spi(response: list[int] | None = None) -> tuple[Mcp3208Reader, FakeSpi]:
    reader = Mcp3208Reader(bus=0, device=0, vref=3.3)
    spi = FakeSpi(response)
    reader._spi = spi
    return reader, spi


def test_default_vref_matches_five_volt_adc_supply() -> None:
    reader = Mcp3208Reader(bus=0, device=0)

    assert reader.vref == 5.0


@pytest.mark.parametrize(
    ("channel", "expected_command"),
    [
        (0, [0x06, 0x00, 0x00]),
        (3, [0x06, 0xC0, 0x00]),
        (4, [0x07, 0x00, 0x00]),
        (7, [0x07, 0xC0, 0x00]),
    ],
)
def test_read_channel_uses_mcp3208_single_ended_command(
    channel: int, expected_command: list[int]
) -> None:
    reader, spi = reader_with_spi()

    reading = reader.read_channel(channel)

    assert spi.commands == [expected_command]
    assert reading.channel == channel
    assert reading.raw == 0xABC
    assert reading.voltage == pytest.approx((0xABC / MCP3208_ADC_DIVISOR) * 3.3)


def test_read_all_reads_all_eight_channels() -> None:
    reader, spi = reader_with_spi([0x00, 0x00, 0x01])

    readings = reader.read_all()

    assert MCP3208_CHANNEL_COUNT == 8
    assert [reading.channel for reading in readings] == list(range(8))
    assert len(spi.commands) == 8


@pytest.mark.parametrize("channel", [-1, 8])
def test_read_channel_rejects_out_of_range_channel(channel: int) -> None:
    reader, _ = reader_with_spi()

    with pytest.raises(ValueError, match=r"0\.\.7"):
        reader.read_channel(channel)


def test_read_channel_requires_open_device() -> None:
    reader = Mcp3208Reader(bus=0, device=0)

    with pytest.raises(RuntimeError, match="not open"):
        reader.read_channel(0)


def test_read_channel_rejects_short_spi_response() -> None:
    reader, _ = reader_with_spi([0x00, 0x00])

    with pytest.raises(RuntimeError, match="expected 3"):
        reader.read_channel(0)
