from __future__ import annotations

import asyncio
import json

from highend_server.application.control_service import ControlService
from highend_server.application.stabilization import (
    DEFAULT_MIXING_MATRIX,
    AxisPid,
    StabilizationController,
    StabilizationPersistedConfig,
)
from highend_server.config import Settings
from highend_server.domain.models import (
    ControlMode,
    PortRole,
    SetTargetRequest,
    StabilizationGains,
    StabilizationRequest,
    TelemetryEvent,
)
from highend_server.sensors.attitude import EulerAngles, euler_to_quat
from highend_server.sensors.imu_bmx055 import Vector3
from highend_server.sensors.sensor_service import AttitudeState
from highend_server.transport.serial_gateway import StubSerialGateway

RIGHT_ACTUATORS = (0, 1, 4, 5)
LEFT_ACTUATORS = (2, 3, 6, 7)
FRONT_ACTUATORS = (0, 1, 2, 3)
REAR_ACTUATORS = (4, 5, 6, 7)


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


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


def _build(tmp_path, settings=None, attitude=None, gains=None, calibration_lock=None):
    settings = settings or _settings()
    events: list[TelemetryEvent] = []

    async def sink(event: TelemetryEvent) -> None:
        events.append(event)

    gateway = StubSerialGateway(settings)
    control = ControlService(settings=settings, gateway=gateway, event_sink=sink)

    holder = {"attitude": attitude}
    clock = Clock()
    if attitude is None:
        holder["attitude"] = make_attitude(0.0, 0.0, clock())

    controller = StabilizationController(
        settings=settings,
        control_service=control,
        attitude_provider=lambda: holder["attitude"],
        level_offsets_provider=lambda: (0.0, 0.0),
        event_sink=sink,
        config_path=tmp_path / "stabilization.json",
        time_fn=clock,
        calibration_lock=calibration_lock,
    )
    if gains is not None:
        controller._gains = gains
        controller._pid_roll.set_gains(gains.kp_roll, gains.ki_roll, gains.kd_roll)
        controller._pid_pitch.set_gains(gains.kp_pitch, gains.ki_pitch, gains.kd_pitch)
    return controller, control, gateway, holder, clock, events


async def _step_dt(controller: StabilizationController, clock: Clock, dt: float) -> None:
    controller._last_step_t = clock() - dt
    await controller._step()


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


# --------------------------------------------------------------------------
# Mixing matrix signs
# --------------------------------------------------------------------------


def test_mixing_matrix_positive_roll_lifts_right_side(tmp_path) -> None:
    async def scenario() -> None:
        gains = StabilizationGains(
            kp_roll=1.5, ki_roll=0.0, kd_roll=0.0, kp_pitch=1.5, ki_pitch=0.0, kd_pitch=0.0
        )
        controller, _control, _gw, holder, clock, _events = _build(tmp_path, gains=gains)
        controller._enable()
        holder["attitude"] = make_attitude(roll=10.0, pitch=0.0, timestamp=clock())
        await _step_dt(controller, clock, 1.0)

        corr = controller._corrections
        # right-side-down => extend right legs (positive), retract left (negative).
        for i in RIGHT_ACTUATORS:
            assert corr[i] > 0.0, f"actuator {i} should be positive, got {corr[i]}"
        for i in LEFT_ACTUATORS:
            assert corr[i] < 0.0, f"actuator {i} should be negative, got {corr[i]}"

    asyncio.run(scenario())


def test_mixing_matrix_positive_pitch_lifts_rear(tmp_path) -> None:
    async def scenario() -> None:
        gains = StabilizationGains(
            kp_roll=1.5, ki_roll=0.0, kd_roll=0.0, kp_pitch=1.5, ki_pitch=0.0, kd_pitch=0.0
        )
        controller, _control, _gw, holder, clock, _events = _build(tmp_path, gains=gains)
        controller._enable()
        holder["attitude"] = make_attitude(roll=0.0, pitch=10.0, timestamp=clock())
        await _step_dt(controller, clock, 1.0)

        corr = controller._corrections
        # nose-up => extend rear legs (positive), retract front (negative).
        for i in REAR_ACTUATORS:
            assert corr[i] > 0.0, f"rear actuator {i} should be positive, got {corr[i]}"
        for i in FRONT_ACTUATORS:
            assert corr[i] < 0.0, f"front actuator {i} should be negative, got {corr[i]}"

    asyncio.run(scenario())


def test_default_mixing_matrix_shape() -> None:
    assert len(DEFAULT_MIXING_MATRIX) == 8
    assert all(len(row) == 2 for row in DEFAULT_MIXING_MATRIX)


# --------------------------------------------------------------------------
# PID behaviour
# --------------------------------------------------------------------------


def test_larger_error_gives_larger_correction(tmp_path) -> None:
    async def scenario() -> None:
        gains = StabilizationGains(
            kp_roll=1.0, ki_roll=0.0, kd_roll=0.0, kp_pitch=1.0, ki_pitch=0.0, kd_pitch=0.0
        )
        controller, _c, _gw, holder, clock, _e = _build(tmp_path, gains=gains)
        controller._enable()
        holder["attitude"] = make_attitude(roll=5.0, pitch=0.0, timestamp=clock())
        await _step_dt(controller, clock, 1.0)
        small = controller._corrections[0]

        controller2, _c2, _gw2, holder2, clock2, _e2 = _build(tmp_path, gains=gains)
        controller2._enable()
        holder2["attitude"] = make_attitude(roll=15.0, pitch=0.0, timestamp=clock2())
        await _step_dt(controller2, clock2, 1.0)
        large = controller2._corrections[0]

        assert large > small > 0.0

    asyncio.run(scenario())


def test_axis_pid_anti_windup_clamps_integral() -> None:
    pid = AxisPid(kp=0.0, ki=1.0, kd=0.0, integral_limit=10.0, output_limit=1000.0)
    output = 0.0
    for _ in range(100):
        output = pid.step(error=100.0, dt=1.0)
    assert pid.integral == 10.0  # accumulator clamped
    assert output == 10.0        # ki * integral, not runaway


def test_axis_pid_output_saturates() -> None:
    pid = AxisPid(kp=100.0, ki=0.0, kd=0.0, integral_limit=10.0, output_limit=120.0)
    assert pid.step(error=50.0, dt=1.0) == 120.0
    assert pid.step(error=-50.0, dt=1.0) == -120.0


# --------------------------------------------------------------------------
# Clamp + rate limiter
# --------------------------------------------------------------------------


def test_correction_is_clamped_to_max(tmp_path) -> None:
    async def scenario() -> None:
        settings = _settings(
            stabilization_max_correction=120.0, stabilization_max_correction_rate=10000.0
        )
        gains = StabilizationGains(
            kp_roll=100.0, ki_roll=0.0, kd_roll=0.0, kp_pitch=100.0, ki_pitch=0.0, kd_pitch=0.0
        )
        controller, _c, _gw, holder, clock, _e = _build(tmp_path, settings=settings, gains=gains)
        controller._enable()
        holder["attitude"] = make_attitude(roll=29.0, pitch=0.0, timestamp=clock())
        await _step_dt(controller, clock, 1.0)
        for c in controller._corrections:
            assert abs(c) <= 120.0 + 1e-9
        assert max(abs(c) for c in controller._corrections) == 120.0

    asyncio.run(scenario())


def test_rate_limiter_bounds_per_tick_change(tmp_path) -> None:
    async def scenario() -> None:
        settings = _settings(
            stabilization_max_correction=120.0, stabilization_max_correction_rate=10.0
        )
        gains = StabilizationGains(
            kp_roll=100.0, kp_pitch=100.0, ki_roll=0.0, ki_pitch=0.0, kd_roll=0.0, kd_pitch=0.0
        )
        controller, _c, _gw, holder, clock, _e = _build(tmp_path, settings=settings, gains=gains)
        controller._enable()
        holder["attitude"] = make_attitude(roll=29.0, pitch=0.0, timestamp=clock())
        await _step_dt(controller, clock, 1.0)  # max_delta = 10 * 1.0 = 10
        for c in controller._corrections:
            assert abs(c) <= 10.0 + 1e-9

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Auto-disable
# --------------------------------------------------------------------------


def test_auto_disable_on_excessive_tilt(tmp_path) -> None:
    async def scenario() -> None:
        settings = _settings(stabilization_max_tilt_deg=30.0)
        controller, _c, _gw, holder, clock, _e = _build(tmp_path, settings=settings)
        controller._enable()
        holder["attitude"] = make_attitude(roll=40.0, pitch=0.0, timestamp=clock())
        await _step_dt(controller, clock, 1.0)
        state = controller.get_state()
        assert state.auto_disabled is True
        assert state.disabled_reason == "tilt exceeded"
        assert state.enabled is True      # user intent preserved
        assert state.active is False

    asyncio.run(scenario())


def test_auto_disable_on_stale_attitude(tmp_path) -> None:
    async def scenario() -> None:
        settings = _settings(stabilization_max_staleness_sec=0.2)
        controller, _c, _gw, holder, clock, _e = _build(tmp_path, settings=settings)
        controller._enable()
        holder["attitude"] = make_attitude(roll=5.0, pitch=0.0, timestamp=clock() - 0.5)
        await _step_dt(controller, clock, 1.0)
        state = controller.get_state()
        assert state.auto_disabled is True
        assert state.disabled_reason == "attitude stale"
        assert state.attitude_stale is True

    asyncio.run(scenario())


def test_auto_disable_on_repeated_serial_failures(tmp_path) -> None:
    class FailingGateway(StubSerialGateway):
        async def send_frame(self, port_role, frame) -> None:
            raise ConnectionError("boom")

    async def scenario() -> None:
        settings = _settings(stabilization_serial_failure_limit=3)
        events: list[TelemetryEvent] = []

        async def sink(event: TelemetryEvent) -> None:
            events.append(event)

        gateway = FailingGateway(settings)
        control = ControlService(settings=settings, gateway=gateway, event_sink=sink)
        clock = Clock()
        holder = {"attitude": make_attitude(roll=8.0, pitch=0.0, timestamp=clock())}
        controller = StabilizationController(
            settings=settings,
            control_service=control,
            attitude_provider=lambda: holder["attitude"],
            level_offsets_provider=lambda: (0.0, 0.0),
            event_sink=sink,
            config_path=tmp_path / "stab.json",
            time_fn=clock,
        )
        controller._enable()
        for _ in range(3):
            holder["attitude"] = make_attitude(roll=8.0, pitch=0.0, timestamp=clock())
            await _step_dt(controller, clock, 0.04)
        state = controller.get_state()
        assert state.auto_disabled is True
        assert state.disabled_reason == "serial send failures"

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Smooth ramp-down
# --------------------------------------------------------------------------


def test_disable_ramps_corrections_smoothly_to_zero(tmp_path) -> None:
    async def scenario() -> None:
        settings = _settings(
            stabilization_max_correction=120.0,
            stabilization_max_correction_rate=10000.0,
            stabilization_disable_ramp_sec=0.5,
        )
        gains = StabilizationGains(
            kp_roll=100.0, kp_pitch=100.0, ki_roll=0.0, ki_pitch=0.0, kd_roll=0.0, kd_pitch=0.0
        )
        controller, _c, _gw, holder, clock, _e = _build(tmp_path, settings=settings, gains=gains)
        controller._enable()
        holder["attitude"] = make_attitude(roll=25.0, pitch=0.0, timestamp=clock())
        await _step_dt(controller, clock, 0.05)
        peak = controller._corrections[0]
        assert peak > 0.0

        # Disable and ramp: decay per step = 120 / 0.5 * 0.05 = 12 units.
        controller._enabled = False
        seq = [peak]
        for _ in range(15):
            holder["attitude"] = make_attitude(roll=25.0, pitch=0.0, timestamp=clock())
            await _step_dt(controller, clock, 0.05)
            seq.append(controller._corrections[0])

        assert seq[1] < seq[0]          # not an instant snap to zero
        assert seq[1] > 0.0             # intermediate value present
        assert all(seq[i + 1] <= seq[i] + 1e-9 for i in range(len(seq) - 1))  # monotone decreasing
        assert abs(seq[-1]) < 1e-6      # eventually reaches zero

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Enable/disable + persistence
# --------------------------------------------------------------------------


def test_enable_disable_and_gain_persistence(tmp_path) -> None:
    async def scenario() -> None:
        config_path = tmp_path / "stabilization.json"
        controller, _c, _gw, _h, _clock, _e = _build(tmp_path)
        assert controller.get_state().enabled is False  # default disabled

        gains = StabilizationGains(
            kp_roll=2.5, ki_roll=0.05, kd_roll=0.4, kp_pitch=2.6, ki_pitch=0.06, kd_pitch=0.5
        )
        state = await controller.apply_request(StabilizationRequest(enabled=True, gains=gains))
        assert state.enabled is True
        assert state.gains.kp_roll == 2.5

        assert config_path.exists()
        saved = StabilizationPersistedConfig.model_validate_json(config_path.read_text())
        assert saved.gains.kp_roll == 2.5
        # enabled must NOT be persisted (never auto-enable on boot).
        assert "enabled" not in json.loads(config_path.read_text())

        state = await controller.apply_request(StabilizationRequest(enabled=False))
        assert state.enabled is False

    asyncio.run(scenario())


def test_never_enabled_on_construction_even_with_persisted_gains(tmp_path) -> None:
    config_path = tmp_path / "stabilization.json"
    StabilizationPersistedConfig(gains=StabilizationGains(kp_roll=3.0)).model_dump()
    config_path.write_text(
        json.dumps(StabilizationPersistedConfig(gains=StabilizationGains(kp_roll=3.0)).model_dump(mode="json"))
    )

    async def scenario() -> None:
        controller, _c, _gw, _h, _clock, _e = _build(tmp_path)
        state = controller.get_state()
        assert state.enabled is False
        assert state.gains.kp_roll == 3.0  # gains restored

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# ControlService composition layer
# --------------------------------------------------------------------------


def test_effective_target_is_base_plus_correction(tmp_path) -> None:
    async def scenario() -> None:
        controller, control, gateway, _h, _clock, _e = _build(tmp_path)
        await control.set_target(0, SetTargetRequest(mode=ControlMode.POSITION, value=2000))
        gateway.sent_frames.clear()

        corrections = [0.0] * 8
        corrections[0] = 50.0
        await control.apply_stabilization_corrections(corrections)

        frames = _position_frames(gateway)
        front = [f for p, f in frames if p is PortRole.FRONT]
        assert front, "expected a FRONT position frame"
        assert front[-1][0] == 2050  # base 2000 + correction 50

    asyncio.run(scenario())


def test_disabled_zero_correction_is_byte_identical(tmp_path) -> None:
    async def scenario() -> None:
        _controller, control, gateway, _h, _clock, _e = _build(tmp_path)
        # No base changes; apply all-zero corrections repeatedly.
        await control.apply_stabilization_corrections([0.0] * 8)
        await control.apply_stabilization_corrections([0.0] * 8)
        assert gateway.sent_frames == []  # nothing sent

    asyncio.run(scenario())


def test_deadband_suppresses_redundant_sends(tmp_path) -> None:
    async def scenario() -> None:
        settings = _settings(stabilization_correction_deadband=4.0)
        _controller, control, gateway, _h, _clock, _e = _build(tmp_path, settings=settings)

        c = [0.0] * 8
        c[0] = 30.0
        await control.apply_stabilization_corrections(c)
        assert len(_position_frames(gateway)) == 1  # first send

        await control.apply_stabilization_corrections(c)  # identical
        assert len(_position_frames(gateway)) == 1  # suppressed

        c[0] = 32.0  # +2 < deadband 4
        await control.apply_stabilization_corrections(c)
        assert len(_position_frames(gateway)) == 1  # still suppressed

        c[0] = 40.0  # +8 >= deadband
        await control.apply_stabilization_corrections(c)
        assert len(_position_frames(gateway)) == 2  # now re-sent

    asyncio.run(scenario())


def test_command_mode_port_is_not_clobbered(tmp_path) -> None:
    async def scenario() -> None:
        _controller, control, gateway, _h, _clock, _e = _build(tmp_path)
        # Drive FRONT port into command mode.
        await control.set_target(0, SetTargetRequest(mode=ControlMode.COMMAND, value=1000))
        gateway.sent_frames.clear()

        corrections = [0.0] * 8
        corrections[0] = 50.0   # front actuator correction
        corrections[4] = 50.0   # rear actuator correction
        await control.apply_stabilization_corrections(corrections)

        frames = _position_frames(gateway)
        ports = {p for p, _ in frames}
        assert PortRole.FRONT not in ports   # command-mode port skipped
        assert PortRole.BACK in ports        # position-mode port corrected

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# End-to-end emulated: tilt -> corrections flow to stub -> decay on level
# --------------------------------------------------------------------------


def test_e2e_tilt_produces_corrections_then_decays(tmp_path) -> None:
    async def scenario() -> None:
        settings = _settings(
            stabilization_max_correction=120.0,
            stabilization_max_correction_rate=2000.0,
            stabilization_disable_ramp_sec=0.5,
        )
        gains = StabilizationGains(
            kp_roll=3.0, kp_pitch=3.0, ki_roll=0.0, ki_pitch=0.0, kd_roll=0.0, kd_pitch=0.0
        )
        controller, control, gateway, holder, clock, _e = _build(
            tmp_path, settings=settings, gains=gains
        )
        await control.set_target(0, SetTargetRequest(mode=ControlMode.POSITION, value=2048))
        controller._enable()

        # Inject a steady right-side-down tilt for several ticks.
        for _ in range(6):
            holder["attitude"] = make_attitude(roll=8.0, pitch=0.0, timestamp=clock())
            await _step_dt(controller, clock, 0.04)
            clock.advance(0.04)

        # Right-side actuator 0 must have been driven above its base target.
        frames = _position_frames(gateway)
        front_id0 = [f[0] for p, f in frames if p is PortRole.FRONT]
        assert any(v > 2048 for v in front_id0), "expected right-side extension frames"
        assert controller._corrections[0] > 0.0

        # Return to level and keep ticking: corrections must decay back to ~0.
        for _ in range(40):
            holder["attitude"] = make_attitude(roll=0.0, pitch=0.0, timestamp=clock())
            await _step_dt(controller, clock, 0.04)
            clock.advance(0.04)

        assert all(abs(c) < 1.0 for c in controller._corrections)

    asyncio.run(scenario())


def test_loop_start_and_stop(tmp_path) -> None:
    async def scenario() -> None:
        settings = _settings(stabilization_rate_hz=100.0)
        controller, _control, _gw, _h, _clock, _e = _build(tmp_path, settings=settings)
        # Use real monotonic time for the live loop.
        import time as _time

        controller._time_fn = _time.monotonic
        await controller.start()
        await asyncio.sleep(0.05)
        assert controller._task is not None and not controller._task.done()
        await controller.stop()
        assert controller._task is None

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Non-finite hardening (CRITICAL: NaN must never defeat the safety guards)
# --------------------------------------------------------------------------


def test_clamp_non_finite_returns_zero() -> None:
    from math import inf, nan

    from highend_server.application.stabilization import _clamp

    # Any non-finite correction value is forced to exactly 0.0 (fail-safe).
    assert _clamp(nan, 120.0) == 0.0
    assert _clamp(inf, 120.0) == 0.0
    assert _clamp(-inf, 120.0) == 0.0
    # Finite values keep the normal clamp semantics.
    assert _clamp(50.0, 120.0) == 50.0
    assert _clamp(500.0, 120.0) == 120.0
    assert _clamp(-500.0, 120.0) == -120.0


def test_auto_disable_on_nan_attitude(tmp_path) -> None:
    from math import nan

    async def scenario() -> None:
        controller, _c, _gw, holder, clock, _e = _build(tmp_path)
        controller._enable()
        holder["attitude"] = make_attitude(roll=nan, pitch=0.0, timestamp=clock())
        await _step_dt(controller, clock, 0.04)
        state = controller.get_state()
        assert state.auto_disabled is True
        assert state.disabled_reason == "attitude invalid"
        assert state.active is False
        assert all(c == 0.0 for c in controller._corrections)

    asyncio.run(scenario())


def test_nan_pitch_also_triggers_auto_disable(tmp_path) -> None:
    from math import nan

    async def scenario() -> None:
        controller, _c, _gw, holder, clock, _e = _build(tmp_path)
        controller._enable()
        holder["attitude"] = make_attitude(roll=0.0, pitch=nan, timestamp=clock())
        await _step_dt(controller, clock, 0.04)
        assert controller.get_state().disabled_reason == "attitude invalid"

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Loop survival (CRITICAL: a crashing iteration must not latch corrections)
# --------------------------------------------------------------------------


def test_loop_survives_attitude_provider_exception(tmp_path) -> None:
    import time as _time

    async def scenario() -> None:
        settings = _settings(stabilization_rate_hz=200.0)
        events: list[TelemetryEvent] = []

        async def sink(event: TelemetryEvent) -> None:
            events.append(event)

        gateway = StubSerialGateway(settings)
        control = ControlService(settings=settings, gateway=gateway, event_sink=sink)
        mode = {"raise": True}

        def provider():
            if mode["raise"]:
                raise RuntimeError("attitude provider crashed")
            return make_attitude(0.0, 0.0, _time.monotonic())

        controller = StabilizationController(
            settings=settings,
            control_service=control,
            attitude_provider=provider,
            level_offsets_provider=lambda: (0.0, 0.0),
            event_sink=sink,
            config_path=tmp_path / "stab.json",
            time_fn=_time.monotonic,
        )
        controller._enable()
        # Simulate corrections latched from before the fault.
        controller._corrections = [50.0] * 8

        await controller.start()
        try:
            await asyncio.sleep(0.3)
            # Loop must still be alive despite every iteration raising.
            assert controller._task is not None and not controller._task.done()
            state = controller.get_state()
            assert state.auto_disabled is True
            assert state.disabled_reason == "internal error"
            # Persistent failures force corrections to zero (nothing latched).
            assert all(c == 0.0 for c in controller._corrections)

            # Fault clears -> a fresh enable must work again.
            mode["raise"] = False
            await controller.apply_request(StabilizationRequest(enabled=True))
            await asyncio.sleep(0.1)
            assert controller._task is not None and not controller._task.done()
            state = controller.get_state()
            assert state.auto_disabled is False
            assert state.active is True
        finally:
            await controller.stop()
        assert controller._task is None

    asyncio.run(scenario())


def test_stop_tolerates_already_dead_task(tmp_path) -> None:
    async def scenario() -> None:
        controller, _c, _gw, _h, _clock, _e = _build(tmp_path)

        async def crash() -> None:
            raise RuntimeError("task died unexpectedly")

        controller._task = asyncio.get_running_loop().create_task(crash())
        await asyncio.sleep(0)  # let the task die
        assert controller._task.done()
        await controller.stop()  # must not raise
        assert controller._task is None

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Staleness telemetry (evaluated every tick, even while disabled)
# --------------------------------------------------------------------------


def test_attitude_stale_telemetry_updates_while_disabled(tmp_path) -> None:
    async def scenario() -> None:
        settings = _settings(stabilization_max_staleness_sec=0.2)
        controller, _c, _gw, holder, clock, _e = _build(tmp_path, settings=settings)
        assert controller.get_state().enabled is False

        holder["attitude"] = make_attitude(0.0, 0.0, timestamp=clock() - 5.0)
        await _step_dt(controller, clock, 0.04)
        assert controller.get_state().attitude_stale is True

        holder["attitude"] = make_attitude(0.0, 0.0, timestamp=clock())
        await _step_dt(controller, clock, 0.04)
        assert controller.get_state().attitude_stale is False

    asyncio.run(scenario())


def test_enable_waits_for_in_flight_calibration(tmp_path) -> None:
    """Enabling must serialize against the shared calibration lock.

    Without this, POST /control/stabilization {enabled:true} can slip between a
    calibration route's idle pre-check and the calibration mutation, so the
    filter gets snapped while the loop is actively driving actuators.
    """

    async def scenario() -> None:
        lock = asyncio.Lock()
        controller, *_ = _build(tmp_path, calibration_lock=lock)

        await lock.acquire()  # simulate an in-flight calibration coroutine
        task = asyncio.create_task(
            controller.apply_request(StabilizationRequest(enabled=True))
        )
        await asyncio.sleep(0.02)
        assert controller.enabled is False  # blocked behind the calibration
        lock.release()
        state = await asyncio.wait_for(task, timeout=1.0)
        assert state.enabled is True

    asyncio.run(scenario())


def test_disable_does_not_block_on_calibration_lock(tmp_path) -> None:
    """Disable is the safety path: it must never wait behind a calibration."""

    async def scenario() -> None:
        lock = asyncio.Lock()
        controller, *_ = _build(tmp_path, calibration_lock=lock)
        controller._enable()

        await lock.acquire()  # calibration in flight (holding the shared lock)
        state = await asyncio.wait_for(
            controller.apply_request(StabilizationRequest(enabled=False)),
            timeout=0.5,
        )
        assert state.enabled is False
        lock.release()

    asyncio.run(scenario())


def test_actuator_count_beyond_default_mixing_matrix_raises(tmp_path) -> None:
    """actuator_count > 8 with no override must fail loudly at construction,
    not IndexError on every loop tick after silent slice truncation."""

    async def sink(event: TelemetryEvent) -> None:
        return None

    settings = _settings()
    gateway = StubSerialGateway(settings)
    control = ControlService(settings=settings, gateway=gateway, event_sink=sink)
    try:
        StabilizationController(
            settings=settings,
            control_service=control,
            attitude_provider=lambda: None,
            event_sink=sink,
            actuator_count=len(DEFAULT_MIXING_MATRIX) + 1,
            config_path=tmp_path / "stabilization.json",
        )
    except ValueError as exc:
        assert "mixing" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for oversized actuator_count")
