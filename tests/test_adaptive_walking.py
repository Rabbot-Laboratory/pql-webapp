from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from highend_server.application.adaptive_walking import (
    AdaptiveWalkingConfig,
    AdaptiveWalkingController,
    AdaptiveWalkingMemory,
    compute_adaptive_walking_targets,
    smooth_amplitude_scale,
)
from highend_server.application.control_service import ControlService
from highend_server.config import Settings
from highend_server.domain.models import ConnectionState, TelemetryEvent
from highend_server.sensors.attitude import EulerAngles, euler_to_quat
from highend_server.sensors.imu_bmx055 import Vector3
from highend_server.sensors.sensor_service import AttitudeState
from highend_server.transport.serial_gateway import StubSerialGateway


def _config(**overrides: float) -> AdaptiveWalkingConfig:
    values = dict(
        learning_rate=1.0,
        feedback_gain=0.0,
        max_phase_lead_s=0.2,
        velocity_regularizer=100.0,
        max_phase_offset=100.0,
        attitude_kp=2.0,
        attitude_kd=0.0,
        trim_adaptation_rate=1.0,
        trim_leak_rate=0.0,
        max_trim=20.0,
        max_attitude_correction=50.0,
        max_target_rate=1000.0,
    )
    values.update(overrides)
    return AdaptiveWalkingConfig(**values)


def test_adaptive_function_learns_independent_phase_lead() -> None:
    memory = AdaptiveWalkingMemory(phase_leads_s=(0.0, 0.0), last_targets=(2000.0, 2000.0))

    result = compute_adaptive_walking_targets(
        nominal_targets=(2100.0, 2000.0),
        nominal_velocities=(500.0, 0.0),
        actual_positions=(2000.0, 2000.0),
        roll_error_deg=0.0,
        pitch_error_deg=0.0,
        roll_error_rate_dps=0.0,
        pitch_error_rate_dps=0.0,
        dt=0.04,
        memory=memory,
        config=_config(),
        mixing_matrix=((1.0, 0.0), (1.0, 0.0)),
    )

    assert result.memory.phase_leads_s[0] > 0.0
    assert result.memory.phase_leads_s[1] == 0.0
    assert result.phase_offsets[0] > 0.0


def test_adaptive_function_applies_bounded_imu_trim_and_rate_limit() -> None:
    result = compute_adaptive_walking_targets(
        nominal_targets=(2500.0, 2500.0),
        nominal_velocities=(0.0, 0.0),
        actual_positions=(2000.0, 2000.0),
        roll_error_deg=10.0,
        pitch_error_deg=0.0,
        roll_error_rate_dps=0.0,
        pitch_error_rate_dps=0.0,
        dt=0.1,
        memory=AdaptiveWalkingMemory(
            phase_leads_s=(0.0, 0.0), last_targets=(2000.0, 2000.0)
        ),
        config=_config(max_target_rate=100.0),
        mixing_matrix=((1.0, 0.0), (-1.0, 0.0)),
    )

    assert result.memory.roll_trim > 0.0
    assert result.attitude_offsets[0] > 0.0
    assert result.attitude_offsets[1] < 0.0
    assert result.targets == (2010, 2010)


def test_motion_amplitude_ramps_smoothly_from_zero_to_full() -> None:
    assert smooth_amplitude_scale(0.0, 5.0, 1.0) == (0.0, 0.0)
    scale, rate = smooth_amplitude_scale(2.5, 5.0, 1.0)
    assert scale == 0.5
    assert rate > 0.0
    assert smooth_amplitude_scale(5.0, 5.0, 1.0) == (1.0, 0.0)


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _attitude(clock: Clock) -> AttitudeState:
    zero = Vector3(0.0, 0.0, 0.0)
    return AttitudeState(
        quaternion=euler_to_quat(0.0, 0.0, 0.0),
        euler=EulerAngles(roll_deg=0.0, pitch_deg=0.0, yaw_deg=0.0),
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


def test_forward_lease_stops_target_updates_when_browser_keepalive_expires(
    tmp_path: Path,
) -> None:
    asyncio.run(_lease_expiry_scenario(tmp_path))


async def _lease_expiry_scenario(tmp_path: Path) -> None:
    motion_root = tmp_path / "Motion"
    fixed = motion_root / "Fixed Motion"
    fixed.mkdir(parents=True)
    (fixed / "rabbit_bound.csv").write_text(
        "# interval_sec=0.04\n# loop=true\n"
        "2048,2048,2048,2048,2048,2048,2048,2048\n"
        "2200,1900,1900,2200,2048,2048,2048,2048\n",
        encoding="utf-8",
    )
    settings = Settings(
        emulate_devices=True,
        motion_root_dir=str(motion_root),
        adaptive_walk_lease_timeout_sec=0.4,
        stabilization_roll_sign=1,
        stabilization_pitch_sign=1,
    )
    events: list[TelemetryEvent] = []

    async def sink(event: TelemetryEvent) -> None:
        events.append(event)

    control = ControlService(
        settings=settings,
        gateway=StubSerialGateway(settings),
        event_sink=sink,
    )
    clock = Clock()
    controller = AdaptiveWalkingController(
        settings=settings,
        control_service=control,
        attitude_provider=lambda: _attitude(clock),
        level_offsets_provider=lambda: (0.0, 0.0),
        event_sink=sink,
        stabilization_engaged=lambda: False,
        time_fn=clock,
    )

    await control.connect()
    try:
        state = await controller.set_forward_pressed(True, safety_confirmed=True)
        assert state.active is True
        await controller._step()

        clock.value += 0.41
        await controller._step()

        state = controller.get_state()
        assert state.active is False
        assert state.auto_stopped is True
        assert state.stopped_reason == "forward-button lease expired"
        assert any(event.type == "adaptive_walk_state" for event in events)

        # A delayed pointer-up from the browser must not erase the more useful
        # automatic-stop reason shown to the operator.
        state = await controller.set_forward_pressed(False, safety_confirmed=False)
        assert state.auto_stopped is True
        assert state.stopped_reason == "forward-button lease expired"
    finally:
        await controller.release()
        await control.shutdown()


def test_forward_requires_explicit_safety_confirmation(tmp_path: Path) -> None:
    asyncio.run(_confirmation_scenario(tmp_path))


async def _confirmation_scenario(tmp_path: Path) -> None:
    settings = Settings(emulate_devices=True, motion_root_dir=str(tmp_path / "Motion"))

    async def sink(_event: TelemetryEvent) -> None:
        return None

    clock = Clock()
    control = ControlService(
        settings=settings,
        gateway=StubSerialGateway(settings),
        event_sink=sink,
    )
    controller = AdaptiveWalkingController(
        settings=settings,
        control_service=control,
        attitude_provider=lambda: _attitude(clock),
        level_offsets_provider=lambda: (0.0, 0.0),
        event_sink=sink,
        stabilization_engaged=lambda: False,
        time_fn=clock,
    )

    with pytest.raises(ValueError, match="safety confirmation"):
        await controller.set_forward_pressed(True, safety_confirmed=False)


def test_physical_walk_requires_current_telemetry_from_every_actuator(
    tmp_path: Path,
) -> None:
    asyncio.run(_missing_physical_telemetry_scenario(tmp_path))


async def _missing_physical_telemetry_scenario(tmp_path: Path) -> None:
    settings = Settings(emulate_devices=False, motion_root_dir=str(tmp_path / "Motion"))

    async def sink(_event: TelemetryEvent) -> None:
        return None

    clock = Clock()
    gateway = StubSerialGateway(settings)
    gateway.connection_state = ConnectionState.CONNECTED
    control = ControlService(settings=settings, gateway=gateway, event_sink=sink)
    controller = AdaptiveWalkingController(
        settings=settings,
        control_service=control,
        attitude_provider=lambda: _attitude(clock),
        level_offsets_provider=lambda: (0.0, 0.0),
        event_sink=sink,
        stabilization_engaged=lambda: False,
        time_fn=clock,
    )

    with pytest.raises(RuntimeError, match="fresh telemetry from all actuators"):
        await controller.set_forward_pressed(True, safety_confirmed=True)
