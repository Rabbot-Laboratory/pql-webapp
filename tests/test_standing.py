"""Tests for the standing hold controller (rise -> hold -> standing_ok)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from highend_server.application.control_service import ControlService
from highend_server.application.standing import (
    StandingConfig,
    StandingController,
    compute_standing_targets,
)
from highend_server.config import Settings
from highend_server.domain.models import StandingPhase, TelemetryEvent
from highend_server.sensors.attitude import EulerAngles, euler_to_quat
from highend_server.sensors.imu_bmx055 import Vector3
from highend_server.sensors.sensor_service import AttitudeState
from highend_server.transport.serial_gateway import StubSerialGateway


def _config(**overrides: float) -> StandingConfig:
    values = dict(
        hold_tolerance=150.0,
        overdrive_gain=1.0,
        max_overdrive=400.0,
        max_target_rate=10_000.0,
        attitude_kp=0.0,
        attitude_kd=0.0,
        max_attitude_correction=40.0,
    )
    values.update(overrides)
    return StandingConfig(**values)


_MIX = ((1.0, 0.0), (-1.0, 0.0))


def _compute(**kwargs):
    defaults = dict(
        stand_targets=(2048.0, 2048.0),
        actual_positions=(2048.0, 2048.0),
        roll_error_deg=0.0,
        pitch_error_deg=0.0,
        roll_error_rate_dps=0.0,
        pitch_error_rate_dps=0.0,
        dt=0.04,
        last_sent=(2048.0, 2048.0),
        config=_config(),
        mixing_matrix=_MIX,
    )
    defaults.update(kwargs)
    return compute_standing_targets(**defaults)


def test_within_tolerance_sends_plain_target() -> None:
    result = _compute(actual_positions=(2148.0, 1948.0))  # errors -100/+100 < 150
    assert result.targets == (2048, 2048)
    assert result.overdrive_active == (False, False)
    assert result.axis_errors == (-100, 100)


def test_overdrive_engages_only_beyond_tolerance() -> None:
    result = _compute(actual_positions=(1648.0, 2048.0))  # error +400 on axis 0
    assert result.overdrive_active == (True, False)
    # target = stand + gain*error = 2048 + 400 = 2448
    assert result.targets[0] == 2448
    assert result.targets[1] == 2048


def test_overdrive_is_clamped() -> None:
    result = _compute(
        actual_positions=(48.0, 2048.0),  # error +2000
        config=_config(max_overdrive=400.0),
    )
    assert result.targets[0] == 2048 + 400


def test_attitude_offsets_use_mixing_signs() -> None:
    result = _compute(
        roll_error_deg=10.0,
        config=_config(attitude_kp=2.0),
    )
    # axis0 mixing +1 -> +20, axis1 mixing -1 -> -20
    assert result.targets == (2068, 2028)


def test_rate_limit_applies() -> None:
    result = _compute(
        actual_positions=(1048.0, 2048.0),
        last_sent=(2048.0, 2048.0),
        dt=0.04,
        config=_config(max_target_rate=500.0),  # 20 units per tick
    )
    assert result.targets[0] == 2068  # 2048 + 500*0.04


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _attitude(clock: Clock, roll: float = 0.0, pitch: float = 0.0) -> AttitudeState:
    zero = Vector3(0.0, 0.0, 0.0)
    return AttitudeState(
        quaternion=euler_to_quat(0.0, 0.0, 0.0),
        euler=EulerAngles(roll_deg=roll, pitch_deg=pitch, yaw_deg=0.0),
        gravity_g=Vector3(0.0, 0.0, 1.0),
        linear_accel_g=zero,
        accel_g=Vector3(0.0, 0.0, 1.0),
        gyro_dps=zero,
        raw_gyro_dps=zero,
        mag=zero,
        mag_valid=False,
        temperature_c=None,
        timestamp=clock.value,
        sample_count=1,
    )


def _write_home(tmp_path: Path) -> Path:
    motion_root = tmp_path / "Motion"
    fixed = motion_root / "Fixed Motion"
    fixed.mkdir(parents=True, exist_ok=True)
    (fixed / "home.csv").write_text(
        "# interval_sec=0.04\n2048,2048,2048,2048,2048,2048,2048,2048\n",
        encoding="utf-8",
    )
    return motion_root


def _make_standing(tmp_path: Path, *, stabilization_engaged=lambda: False, **overrides):
    settings = Settings(
        emulate_devices=True,
        motion_root_dir=str(_write_home(tmp_path)),
        standing_ok_hold_sec=0.1,
        stabilization_roll_sign=1,
        stabilization_pitch_sign=1,
        **overrides,
    )
    events: list[TelemetryEvent] = []

    async def sink(event: TelemetryEvent) -> None:
        events.append(event)

    control = ControlService(
        settings=settings, gateway=StubSerialGateway(settings), event_sink=sink
    )
    clock = Clock()
    attitude = {"roll": 0.0, "pitch": 0.0}
    controller = StandingController(
        settings=settings,
        control_service=control,
        attitude_provider=lambda: _attitude(clock, attitude["roll"], attitude["pitch"]),
        level_offsets_provider=lambda: (0.0, 0.0),
        event_sink=sink,
        stabilization_engaged=stabilization_engaged,
        time_fn=clock,
    )
    return controller, control, clock, attitude, events


def test_rises_then_holds_then_reports_ok(tmp_path: Path) -> None:
    asyncio.run(_rise_hold_scenario(tmp_path))


async def _rise_hold_scenario(tmp_path: Path) -> None:
    controller, control, clock, _attitude_state, _events = _make_standing(tmp_path)
    await control.connect()
    try:
        state = await controller.set_enabled(True, safety_confirmed=True)
        assert state.enabled is True
        assert state.phase is StandingPhase.RISING

        # Stub actuators start at 2048 == the stand pose, so the ramp is a
        # single row and the hold begins immediately after it.
        for _ in range(12):
            clock.value += 0.04
            await controller._step()
        state = controller.get_state()
        assert state.phase is StandingPhase.HOLDING
        assert state.standing_ok is True  # 0.1 s ok-hold elapsed
        assert all(abs(err) < 200 for err in state.axis_errors)
    finally:
        await controller.set_enabled(False, safety_confirmed=False)
        await control.shutdown()


def test_tilt_auto_disables_with_reason(tmp_path: Path) -> None:
    asyncio.run(_tilt_scenario(tmp_path))


async def _tilt_scenario(tmp_path: Path) -> None:
    controller, control, clock, attitude, _events = _make_standing(tmp_path)
    await control.connect()
    try:
        await controller.set_enabled(True, safety_confirmed=True)
        clock.value += 0.04
        await controller._step()
        attitude["roll"] = 20.0  # beyond standing_max_tilt_deg=15
        clock.value += 0.04
        await controller._step()
        state = controller.get_state()
        assert state.enabled is False
        assert state.auto_disabled is True
        assert state.disabled_reason == "tilt limit exceeded"
    finally:
        await control.shutdown()


def test_enable_requires_confirmation_and_no_stabilization(tmp_path: Path) -> None:
    asyncio.run(_guards_scenario(tmp_path))


async def _guards_scenario(tmp_path: Path) -> None:
    controller, control, _clock, _attitude_state, _events = _make_standing(
        tmp_path, stabilization_engaged=lambda: True
    )
    await control.connect()
    try:
        with pytest.raises(ValueError, match="safety confirmation"):
            await controller.set_enabled(True, safety_confirmed=False)
        with pytest.raises(RuntimeError, match="stabilization"):
            await controller.set_enabled(True, safety_confirmed=True)
    finally:
        await control.shutdown()


def test_handover_releases_ownership_without_moving(tmp_path: Path) -> None:
    asyncio.run(_handover_scenario(tmp_path))


async def _handover_scenario(tmp_path: Path) -> None:
    controller, control, clock, _attitude_state, _events = _make_standing(tmp_path)
    await control.connect()
    try:
        await controller.set_enabled(True, safety_confirmed=True)
        clock.value += 0.04
        await controller._step()
        await controller.release_for_handover()
        state = controller.get_state()
        assert state.enabled is False
        assert state.disabled_reason == "handed over to walking"
        # Ownership is free again: another claim must succeed.
        await control.claim_adaptive_walking()
        await control.release_adaptive_walking()
    finally:
        await control.shutdown()
