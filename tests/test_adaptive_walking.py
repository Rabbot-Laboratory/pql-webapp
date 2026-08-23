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


# -- Stage 1/2 additions: flags, anti-windup, stance mask, cycles, gate ------

from highend_server.domain.models import (  # noqa: E402
    AdaptiveWalkMode,
    ContactLegState,
    LegId,
)


def test_rate_limit_and_saturation_flags_block_lead_learning() -> None:
    config = _config(max_target_rate=100.0)
    memory = AdaptiveWalkingMemory(
        phase_leads_s=(0.05, 0.05), last_targets=(2000.0, 4095.0)
    )
    result = compute_adaptive_walking_targets(
        nominal_targets=(2500.0, 4095.0),
        nominal_velocities=(500.0, 500.0),
        actual_positions=(2000.0, 4000.0),
        roll_error_deg=0.0,
        pitch_error_deg=0.0,
        roll_error_rate_dps=0.0,
        pitch_error_rate_dps=0.0,
        dt=0.04,
        memory=memory,
        config=config,
        mixing_matrix=((1.0, 0.0), (1.0, 0.0)),
    )
    assert result.rate_limited[0] is True
    assert result.saturated[1] is True
    assert result.memory.blocked == (True, True)

    # Next tick: both axes were blocked, so leads must not move even though
    # tracking error persists.
    followup = compute_adaptive_walking_targets(
        nominal_targets=(2500.0, 4095.0),
        nominal_velocities=(500.0, 500.0),
        actual_positions=(2000.0, 4000.0),
        roll_error_deg=0.0,
        pitch_error_deg=0.0,
        roll_error_rate_dps=0.0,
        pitch_error_rate_dps=0.0,
        dt=0.04,
        memory=result.memory,
        config=config,
        mixing_matrix=((1.0, 0.0), (1.0, 0.0)),
    )
    assert followup.memory.phase_leads_s == result.memory.phase_leads_s


def test_stance_mask_zeroes_attitude_on_swing_legs() -> None:
    result = compute_adaptive_walking_targets(
        nominal_targets=(2048.0, 2048.0),
        nominal_velocities=(0.0, 0.0),
        actual_positions=(2048.0, 2048.0),
        roll_error_deg=10.0,
        pitch_error_deg=0.0,
        roll_error_rate_dps=0.0,
        pitch_error_rate_dps=0.0,
        dt=0.04,
        memory=AdaptiveWalkingMemory(
            phase_leads_s=(0.0, 0.0), last_targets=(2048.0, 2048.0)
        ),
        config=_config(),
        mixing_matrix=((1.0, 0.0), (1.0, 0.0)),
        stance_mask=(True, False),
    )
    assert result.attitude_offsets[0] > 0.0
    assert result.attitude_offsets[1] == 0.0


def _write_walk_motion(tmp_path: Path) -> Path:
    motion_root = tmp_path / "Motion"
    fixed = motion_root / "Fixed Motion"
    fixed.mkdir(parents=True, exist_ok=True)
    (fixed / "rabbit_bound.csv").write_text(
        "# interval_sec=0.04\n# loop=true\n"
        "2048,2048,2048,2048,2048,2048,2048,2048\n"
        "2200,1900,1900,2200,2048,2048,2048,2048\n",
        encoding="utf-8",
    )
    return motion_root


def _contact(rear_supporting: bool, *, raw: int | None = 3000) -> list[ContactLegState]:
    return [
        ContactLegState(
            leg=leg,
            raw=raw,
            supporting=rear_supporting
            if leg in (LegId.REAR_RIGHT, LegId.REAR_LEFT)
            else False,
        )
        for leg in LegId
    ]


def _make_walk_controller(
    tmp_path: Path,
    *,
    contact_provider=None,
    recorder=None,
    **settings_overrides,
):
    settings = Settings(
        emulate_devices=True,
        motion_root_dir=str(_write_walk_motion(tmp_path)),
        adaptive_walk_lease_timeout_sec=2.0,
        adaptive_walk_motion_ramp_sec=0.05,
        stabilization_roll_sign=1,
        stabilization_pitch_sign=1,
        **settings_overrides,
    )
    events: list[TelemetryEvent] = []

    async def sink(event: TelemetryEvent) -> None:
        events.append(event)

    control = ControlService(
        settings=settings, gateway=StubSerialGateway(settings), event_sink=sink
    )
    clock = Clock()
    controller = AdaptiveWalkingController(
        settings=settings,
        control_service=control,
        attitude_provider=lambda: _attitude(clock),
        level_offsets_provider=lambda: (0.0, 0.0),
        event_sink=sink,
        stabilization_engaged=lambda: False,
        contact_provider=contact_provider,
        experiment_recorder=recorder,
        time_fn=clock,
    )
    return controller, control, clock, events


def test_cycle_target_auto_stops_after_full_amplitude_cycles(tmp_path: Path) -> None:
    asyncio.run(_cycle_target_scenario(tmp_path))


async def _cycle_target_scenario(tmp_path: Path) -> None:
    controller, control, clock, _events = _make_walk_controller(tmp_path)
    await control.connect()
    try:
        state = await controller.set_forward_pressed(
            True, safety_confirmed=True, cycles=1, mode=AdaptiveWalkMode.ADAPTIVE
        )
        assert state.target_cycles == 1
        # 2 frames x 0.04 s = 0.08 s cycle; ramp completes at 0.05 s wall time.
        for _ in range(10):
            clock.value += 0.04
            await controller._step()
            if not controller.get_state().active:
                break
        state = controller.get_state()
        assert state.active is False
        assert state.auto_stopped is True
        assert state.stopped_reason == "cycle target reached"
        assert state.cycle_count == 1
    finally:
        await controller.release()
        await control.shutdown()


def test_replay_mode_disables_every_adaptive_term(tmp_path: Path) -> None:
    asyncio.run(_replay_scenario(tmp_path))


async def _replay_scenario(tmp_path: Path) -> None:
    controller, control, clock, _events = _make_walk_controller(tmp_path)
    await control.connect()
    try:
        await controller.set_forward_pressed(
            True, safety_confirmed=True, mode=AdaptiveWalkMode.REPLAY
        )
        initial_leads = controller.get_state().learned_phase_lead_s
        for _ in range(5):
            clock.value += 0.04
            await controller._step()
        result = controller._last_result
        assert result is not None
        assert all(offset == 0.0 for offset in result.phase_offsets)
        assert all(offset == 0.0 for offset in result.attitude_offsets)
        assert controller.get_state().learned_phase_lead_s == initial_leads
        assert controller.get_state().mode is AdaptiveWalkMode.REPLAY
    finally:
        await controller.release()
        await control.shutdown()


def test_gate_holds_phase_until_rear_contact(tmp_path: Path) -> None:
    asyncio.run(_gate_contact_scenario(tmp_path))


async def _gate_contact_scenario(tmp_path: Path) -> None:
    rear_supporting = {"value": False}

    def contact_provider() -> list[ContactLegState]:
        return _contact(rear_supporting["value"])

    controller, control, clock, events = _make_walk_controller(
        tmp_path,
        contact_provider=contact_provider,
        adaptive_walk_use_contact=True,
        adaptive_walk_kick_gate_phase=0.5,
        adaptive_walk_gate_timeout_sec=1.0,
    )
    await control.connect()
    try:
        await controller.set_forward_pressed(True, safety_confirmed=True)
        clock.value += 0.04
        await controller._step()  # advances to the gate phase and holds
        state = controller.get_state()
        assert state.gate_waiting is True
        assert state.phase == pytest.approx(0.5)

        clock.value += 0.04
        await controller._step()  # still waiting
        assert controller.get_state().phase == pytest.approx(0.5)

        rear_supporting["value"] = True
        # Small increment so the resumed advance stays inside the same cycle
        # (the test motion's full cycle is only 0.08 s).
        clock.value += 0.01
        await controller._step()  # resumes past the gate
        state = controller.get_state()
        assert state.gate_waiting is False
        assert 0.5 < state.phase < 1.0
        statuses = [
            event.payload["status"]
            for event in events
            if event.type == "adaptive_walk_gate"
        ]
        assert statuses == ["waiting", "resumed"]
    finally:
        await controller.release()
        await control.shutdown()


def test_gate_timeout_resumes_walk(tmp_path: Path) -> None:
    asyncio.run(_gate_timeout_scenario(tmp_path))


async def _gate_timeout_scenario(tmp_path: Path) -> None:
    controller, control, clock, events = _make_walk_controller(
        tmp_path,
        contact_provider=lambda: _contact(False),
        adaptive_walk_use_contact=True,
        adaptive_walk_kick_gate_phase=0.5,
        adaptive_walk_gate_timeout_sec=0.02,
    )
    await control.connect()
    try:
        await controller.set_forward_pressed(True, safety_confirmed=True)
        clock.value += 0.04
        await controller._step()
        assert controller.get_state().gate_waiting is True

        clock.value += 0.03  # beyond the gate timeout, within the same cycle
        await controller._step()
        state = controller.get_state()
        assert state.gate_waiting is False
        assert 0.5 < state.phase < 1.0
        statuses = [
            event.payload["status"]
            for event in events
            if event.type == "adaptive_walk_gate"
        ]
        assert statuses == ["waiting", "timeout"]
    finally:
        await controller.release()
        await control.shutdown()


def test_missing_contact_data_bypasses_gate(tmp_path: Path) -> None:
    asyncio.run(_missing_contact_scenario(tmp_path))


async def _missing_contact_scenario(tmp_path: Path) -> None:
    # raw=None on every leg means the ADC is not delivering data: the gate and
    # stance mask must fall back to contact-free behaviour, never block.
    controller, control, clock, _events = _make_walk_controller(
        tmp_path,
        contact_provider=lambda: _contact(False, raw=None),
        adaptive_walk_use_contact=True,
        adaptive_walk_kick_gate_phase=0.5,
    )
    await control.connect()
    try:
        await controller.set_forward_pressed(True, safety_confirmed=True)
        clock.value += 0.04
        await controller._step()
        state = controller.get_state()
        assert state.gate_waiting is False
        assert state.phase == pytest.approx(0.5)
    finally:
        await controller.release()
        await control.shutdown()


class _FakeRecorder:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped = 0

    async def start(self, request) -> None:
        self.started.append(request.experiment_type)

    async def stop(self) -> None:
        self.stopped += 1


def test_cycle_runs_auto_record_an_experiment(tmp_path: Path) -> None:
    asyncio.run(_auto_record_scenario(tmp_path))


async def _auto_record_scenario(tmp_path: Path) -> None:
    recorder = _FakeRecorder()
    controller, control, clock, _events = _make_walk_controller(
        tmp_path, recorder=recorder
    )
    await control.connect()
    try:
        await controller.set_forward_pressed(
            True, safety_confirmed=True, cycles=1, mode=AdaptiveWalkMode.REPLAY
        )
        assert recorder.started == ["walk-replay-1cyc"]
        for _ in range(10):
            clock.value += 0.04
            await controller._step()
            if not controller.get_state().active:
                break
        assert recorder.stopped == 1
    finally:
        await controller.release()
        await control.shutdown()


def test_keepalive_cannot_restart_after_automatic_stop(tmp_path: Path) -> None:
    asyncio.run(_keepalive_restart_scenario(tmp_path))


async def _keepalive_restart_scenario(tmp_path: Path) -> None:
    controller, control, clock, _events = _make_walk_controller(tmp_path)
    await control.connect()
    try:
        await controller.set_forward_pressed(True, safety_confirmed=True, cycles=1)
        for _ in range(10):
            clock.value += 0.04
            await controller._step()
            if not controller.get_state().active:
                break
        assert controller.get_state().stopped_reason == "cycle target reached"

        # A still-held button keeps sending keepalives: they must NOT restart.
        with pytest.raises(RuntimeError, match="release the forward button"):
            await controller.set_forward_pressed(True, safety_confirmed=True)

        # After an explicit release, a fresh press starts a new walk.
        await controller.set_forward_pressed(False, safety_confirmed=False)
        state = await controller.set_forward_pressed(True, safety_confirmed=True)
        assert state.active is True
    finally:
        await controller.release()
        await control.shutdown()


def test_walk_motion_name_is_configurable(tmp_path: Path) -> None:
    asyncio.run(_motion_name_scenario(tmp_path))


async def _motion_name_scenario(tmp_path: Path) -> None:
    motion_root = _write_walk_motion(tmp_path)
    custom = motion_root / "Fixed Motion" / "walk_crawl.csv"
    custom.write_text(
        "# interval_sec=0.05\n# loop=true\n"
        "2048,2048,2048,2048,2048,2048,2048,2048\n"
        "2100,2000,2000,2100,2048,2048,2048,2048\n",
        encoding="utf-8",
    )
    controller, control, clock, _events = _make_walk_controller(
        tmp_path, adaptive_walk_motion_name="walk_crawl"
    )
    await control.connect()
    try:
        state = await controller.set_forward_pressed(True, safety_confirmed=True)
        assert state.active is True
        assert controller._interval_s == pytest.approx(0.05)
    finally:
        await controller.release()
        await control.shutdown()
