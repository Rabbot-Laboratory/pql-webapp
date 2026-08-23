from __future__ import annotations

import asyncio
import time

import pytest

from highend_server.config import Settings
from highend_server.domain.models import GamepadSource, TelemetryEvent, WebGamepadUpdate
from highend_server.input.gamepad_service import GamepadService, normalize_axis


def test_normalize_axis_supports_sticks_and_triggers() -> None:
    assert normalize_axis(0, 0, 255) == pytest.approx(-1.0)
    assert normalize_axis(255, 0, 255) == pytest.approx(1.0)
    assert normalize_axis(0, 0, 255, trigger=True) == pytest.approx(0.0)
    assert normalize_axis(255, 0, 255, trigger=True) == pytest.approx(1.0)
    assert normalize_axis(10, 10, 10) == 0.0


def test_web_gamepad_maps_standard_axes_buttons_and_deadman() -> None:
    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = GamepadService(settings=Settings(), event_sink=sink)
        await service.update_web(
            WebGamepadUpdate(
                id="Logitech F710",
                mapping="standard",
                axes=[0.25, -0.5, 0.75, -1.0],
                buttons=[0, 0, 0, 0, 1, 0, 0.2, 0.8],
            )
        )

        state = service._select_state()
        assert state.source is GamepadSource.WEB
        assert state.connected is True
        assert state.stale is False
        assert state.axes == {
            "left_x": 0.25,
            "left_y": -0.5,
            "right_x": 0.75,
            "right_y": -1.0,
        }
        assert state.buttons["lb"] is True
        assert state.deadman is True
        assert state.raw_buttons["button_7"] == pytest.approx(0.8)

    asyncio.run(scenario())


def test_web_gamepad_becomes_stale_after_timeout() -> None:
    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        settings = Settings(gamepad_input_timeout_sec=0.01)
        service = GamepadService(settings=settings, event_sink=sink)
        await service.update_web(WebGamepadUpdate(buttons=[0, 0, 0, 0, 1]))
        time.sleep(0.02)

        state = service._select_state()
        assert state.source is GamepadSource.WEB
        assert state.connected is False
        assert state.stale is True
        assert state.deadman is False

    asyncio.run(scenario())


def test_disconnected_web_update_clears_source() -> None:
    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = GamepadService(settings=Settings(), event_sink=sink)
        await service.update_web(WebGamepadUpdate())
        await service.update_web(WebGamepadUpdate(connected=False))

        state = service._select_state()
        assert state.source is GamepadSource.NONE
        assert state.connected is False

    asyncio.run(scenario())


def test_publish_loop_emits_observation_without_control_output() -> None:
    async def scenario() -> None:
        events: list[TelemetryEvent] = []

        async def sink(event: TelemetryEvent) -> None:
            events.append(event)

        settings = Settings(gamepad_publish_interval_sec=0.005)
        service = GamepadService(settings=settings, event_sink=sink)
        await service.start()
        try:
            await service.update_web(WebGamepadUpdate(id="browser", axes=[0.1]))
            await asyncio.sleep(0.02)
        finally:
            await service.stop()

        assert events
        assert all(event.type == "gamepad_state" for event in events)
        assert events[-1].payload["gamepad"]["source"] == "web"

    asyncio.run(scenario())


def test_publish_loop_emits_final_disconnected_state() -> None:
    async def scenario() -> None:
        events: list[TelemetryEvent] = []

        async def sink(event: TelemetryEvent) -> None:
            events.append(event)

        settings = Settings(gamepad_publish_interval_sec=0.005)
        service = GamepadService(settings=settings, event_sink=sink)
        await service.start()
        try:
            await service.update_web(WebGamepadUpdate(id="browser"))
            await asyncio.sleep(0.04)
            await service.update_web(WebGamepadUpdate(connected=False))
            await asyncio.sleep(0.04)
        finally:
            await service.stop()

        assert events[-1].payload["gamepad"]["source"] == "none"
        assert events[-1].payload["gamepad"]["connected"] is False

    asyncio.run(scenario())
