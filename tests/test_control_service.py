from __future__ import annotations

import asyncio
from time import monotonic

import pytest

from highend_server.application.control_service import (
    ControlService,
    build_rate_limited_position_rows,
)
from highend_server.config import Settings
from highend_server.domain.models import (
    CsvPlaybackRequest,
    PlaybackAdvanceMode,
    PortRole,
    TelemetryEvent,
)
from highend_server.sensors.attitude import EulerAngles, euler_to_quat
from highend_server.sensors.imu_bmx055 import Vector3
from highend_server.sensors.sensor_service import AttitudeState
from highend_server.transport.serial_gateway import StubSerialGateway


def _zero() -> Vector3:
    return Vector3(0.0, 0.0, 0.0)


def make_attitude(roll: float, pitch: float, timestamp: float) -> AttitudeState:
    return AttitudeState(
        quaternion=euler_to_quat(roll, pitch, 0.0),
        euler=EulerAngles(roll_deg=roll, pitch_deg=pitch, yaw_deg=0.0),
        gravity_g=Vector3(0.0, 0.0, 1.0),
        linear_accel_g=_zero(),
        accel_g=Vector3(0.0, 0.0, 1.0),
        gyro_dps=_zero(),
        raw_gyro_dps=_zero(),
        mag=_zero(),
        mag_valid=False,
        temperature_c=None,
        timestamp=timestamp,
        sample_count=1,
    )


def _settings(**overrides) -> Settings:
    base = dict(emulate_devices=True, actuator_count=8)
    base.update(overrides)
    return Settings(**base)


def _position_frames(gateway: StubSerialGateway) -> list[tuple[PortRole, list[int]]]:
    """Decode SET_TARGET position frames (format 63, mode bits 0) from the stub."""
    out: list[tuple[PortRole, list[int]]] = []
    for port_role, frame in gateway.sent_frames:
        if ((frame >> 58) & 0x3F) != 63:
            continue
        if ((frame >> 54) & 0xF) != 0:  # 0 == position, 0b1111 == command
            continue
        fields = [
            (frame >> 42) & 0xFFF,
            (frame >> 30) & 0xFFF,
            (frame >> 18) & 0xFFF,
            (frame >> 6) & 0xFFF,
        ]
        out.append((port_role, fields))
    return out


def _build_control(settings=None, attitude_provider=None, level_offsets_provider=None):
    settings = settings or _settings()
    events: list[TelemetryEvent] = []

    async def sink(event: TelemetryEvent) -> None:
        events.append(event)

    gateway = StubSerialGateway(settings)
    control = ControlService(
        settings=settings,
        gateway=gateway,
        event_sink=sink,
        attitude_provider=attitude_provider,
        level_offsets_provider=level_offsets_provider,
    )
    return control, gateway, events


def test_home_rows_reach_target_without_exceeding_rate() -> None:
    rows = build_rate_limited_position_rows(
        [0, 4095],
        [100, 3995],
        max_rate=50.0,
        interval_sec=0.1,
    )

    assert rows[-1] == ["100", "3995"]
    numeric = [[0, 4095], *[[int(value) for value in row] for row in rows]]
    assert all(
        abs(current[index] - previous[index]) <= 5
        for previous, current in zip(numeric, numeric[1:], strict=False)
        for index in range(2)
    )


# --------------------------------------------------------------------------
# CSV row targets flow through the same BASE-target path as `set_target`
# --------------------------------------------------------------------------


def test_apply_csv_row_disabled_stabilization_matches_base_only() -> None:
    """Stabilization inactive (zero corrections) => byte-identical to base-only."""

    async def scenario() -> None:
        control, gateway, _events = _build_control()
        row = [str(1000 + i * 10) for i in range(8)]

        await control._apply_csv_row(row)

        frames = _position_frames(gateway)
        assert len(frames) == 2  # one frame per port
        front = next(f for p, f in frames if p is PortRole.FRONT)
        back = next(f for p, f in frames if p is PortRole.BACK)
        assert front == [1000, 1010, 1020, 1030]
        assert back == [1040, 1050, 1060, 1070]

        # Base targets themselves were updated (visible to a later set_target/
        # apply_stabilization_corrections call), exactly like set_target does.
        assert [a.target_position for a in control._actuators] == [
            1000,
            1010,
            1020,
            1030,
            1040,
            1050,
            1060,
            1070,
        ]

    asyncio.run(scenario())


def test_apply_csv_row_composes_with_active_correction() -> None:
    """A CSV row's targets compose with an already-active stabilization correction."""

    async def scenario() -> None:
        control, gateway, _events = _build_control()

        corrections = [0.0] * 8
        corrections[0] = 25.0  # front actuator 0
        corrections[4] = -15.0  # rear actuator 0 (local index 0 of BACK)
        await control.apply_stabilization_corrections(corrections)
        gateway.sent_frames.clear()

        row = [str(2000) for _ in range(8)]
        await control._apply_csv_row(row)

        frames = _position_frames(gateway)
        front = next(f for p, f in frames if p is PortRole.FRONT)
        back = next(f for p, f in frames if p is PortRole.BACK)
        assert front[0] == 2025  # 2000 base + 25 correction
        assert front[1:] == [2000, 2000, 2000]
        assert back[0] == 1985  # 2000 base - 15 correction
        assert back[1:] == [2000, 2000, 2000]

    asyncio.run(scenario())


def test_apply_csv_row_marks_ports_position_driven_for_later_corrections() -> None:
    """After a CSV row, `apply_stabilization_corrections` must not skip either port."""

    async def scenario() -> None:
        control, gateway, _events = _build_control()
        await control._apply_csv_row([str(1500) for _ in range(8)])
        gateway.sent_frames.clear()

        corrections = [10.0] * 8
        await control.apply_stabilization_corrections(corrections)

        frames = _position_frames(gateway)
        ports = {p for p, _ in frames}
        assert ports == {PortRole.FRONT, PortRole.BACK}

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Attitude row-advance guard
# --------------------------------------------------------------------------


def _guarded_request(**overrides) -> CsvPlaybackRequest:
    base = dict(
        rows=[["2048"] * 8],
        advance_mode=PlaybackAdvanceMode.GUARDED,
        position_tolerance=4095,  # trivially satisfied: default telemetry position is 2048
        pressure_threshold=0,
        step_timeout_sec=0.15,
        settle_time_sec=0.02,
    )
    base.update(overrides)
    return CsvPlaybackRequest(**base)


def test_attitude_guard_disabled_by_default() -> None:
    holder = {"attitude": make_attitude(roll=45.0, pitch=45.0, timestamp=monotonic())}
    control, _gateway, _events = _build_control(attitude_provider=lambda: holder["attitude"])
    request = _guarded_request()  # no attitude_guard_deg set anywhere
    active_targets = [(i, 2048) for i in range(8)]

    assert control._row_ready(active_targets, request) is True


def test_attitude_guard_holds_while_tilted_and_releases_when_level() -> None:
    settings = _settings(playback_attitude_guard_deg=5.0)
    holder = {"attitude": make_attitude(roll=20.0, pitch=0.0, timestamp=monotonic())}
    control, _gateway, _events = _build_control(
        settings=settings,
        attitude_provider=lambda: holder["attitude"],
        level_offsets_provider=lambda: (0.0, 0.0),
    )
    request = _guarded_request()
    active_targets = [(i, 2048) for i in range(8)]

    assert control._row_ready(active_targets, request) is False

    holder["attitude"] = make_attitude(roll=2.0, pitch=0.0, timestamp=monotonic())
    assert control._row_ready(active_targets, request) is True


def test_attitude_guard_per_request_override_takes_precedence() -> None:
    # Server default disabled, but the request opts in explicitly.
    settings = _settings(playback_attitude_guard_deg=None)
    holder = {"attitude": make_attitude(roll=20.0, pitch=0.0, timestamp=monotonic())}
    control, _gateway, _events = _build_control(
        settings=settings, attitude_provider=lambda: holder["attitude"]
    )
    request = _guarded_request(attitude_guard_deg=5.0)
    active_targets = [(i, 2048) for i in range(8)]

    assert control._row_ready(active_targets, request) is False


def test_attitude_guard_noop_without_provider_even_if_threshold_set() -> None:
    settings = _settings(playback_attitude_guard_deg=1.0)
    control, _gateway, _events = _build_control(settings=settings, attitude_provider=None)
    request = _guarded_request()
    active_targets = [(i, 2048) for i in range(8)]

    assert control._row_ready(active_targets, request) is True


def test_wait_for_row_ready_releases_row_once_tilt_recedes() -> None:
    async def scenario() -> None:
        settings = _settings(playback_attitude_guard_deg=5.0, stabilization_max_staleness_sec=5.0)
        holder = {"attitude": make_attitude(roll=20.0, pitch=0.0, timestamp=monotonic())}
        control, _gateway, events = _build_control(
            settings=settings,
            attitude_provider=lambda: holder["attitude"],
            level_offsets_provider=lambda: (0.0, 0.0),
        )
        request = _guarded_request(step_timeout_sec=2.0, settle_time_sec=0.02)
        row = ["2048"] * 8

        async def recede() -> None:
            await asyncio.sleep(0.1)
            holder["attitude"] = make_attitude(roll=0.0, pitch=0.0, timestamp=monotonic())

        recede_task = asyncio.create_task(recede())
        start = asyncio.get_running_loop().time()
        await control._wait_for_row_ready(row, request)
        elapsed = asyncio.get_running_loop().time() - start
        await recede_task

        assert elapsed >= 0.1  # actually held for the tilt to recede
        assert elapsed < request.step_timeout_sec  # released by the guard, not the timeout
        assert not any(e.type == "playback_guard" for e in events)  # no timeout fired

    asyncio.run(scenario())


def test_wait_for_row_ready_timeout_reports_attitude_hold() -> None:
    async def scenario() -> None:
        settings = _settings(playback_attitude_guard_deg=5.0, stabilization_max_staleness_sec=5.0)
        holder = {"attitude": make_attitude(roll=20.0, pitch=1.0, timestamp=monotonic())}
        control, _gateway, events = _build_control(
            settings=settings,
            attitude_provider=lambda: holder["attitude"],
            level_offsets_provider=lambda: (0.0, 0.0),
        )
        request = _guarded_request(step_timeout_sec=0.1, settle_time_sec=0.02)
        row = ["2048"] * 8

        await control._wait_for_row_ready(row, request)

        guard_events = [e for e in events if e.type == "playback_guard"]
        assert len(guard_events) == 1
        payload = guard_events[0].payload
        assert payload["status"] == "timeout"
        assert payload["attitude_hold"] is True
        assert payload["roll_deg"] == pytest.approx(20.0)
        assert payload["pitch_deg"] == pytest.approx(1.0)

    asyncio.run(scenario())


def test_wait_for_row_ready_timeout_without_attitude_guard_unchanged() -> None:
    """Timeout path still fires (and now carries additive, inert attitude fields)
    when no attitude guard is configured at all -- the pre-Phase-3 position/
    pressure-only timeout behaviour is unaffected."""

    async def scenario() -> None:
        settings = _settings()
        control, _gateway, events = _build_control(settings=settings)
        # Position guard will never be satisfied: telemetry stays at default 2048.
        request = _guarded_request(
            position_tolerance=0, step_timeout_sec=0.05, settle_time_sec=0.02
        )
        row = ["4095"] * 8  # far from the default telemetry position (2048)

        await control._wait_for_row_ready(row, request)

        guard_events = [e for e in events if e.type == "playback_guard"]
        assert len(guard_events) == 1
        payload = guard_events[0].payload
        assert payload["status"] == "timeout"
        assert payload["attitude_hold"] is False
        assert payload["roll_deg"] is None
        assert payload["pitch_deg"] is None

    asyncio.run(scenario())
