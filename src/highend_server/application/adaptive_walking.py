"""Hardware-facing adaptive trot control with a short-lived browser lease.

The numerical control law is intentionally isolated in
``compute_adaptive_walking_targets``.  It has no asyncio, serial, FastAPI, or
sensor-driver dependency, so recorded real-hardware samples can be replayed in
unit tests and the exact same function can later be tuned offline.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from math import isfinite
from time import monotonic

from highend_server.application.attitude_control_frame import AttitudeControlFrame
from highend_server.application.control_service import ControlService
from highend_server.application.pql_a00_kinematics import model_derived_mixing_matrix
from highend_server.config import Settings
from highend_server.domain.models import (
    POSITION_MAX,
    POSITION_MIN,
    AdaptiveWalkState,
    ConnectionState,
    MotionCategory,
    TelemetryEvent,
)
from highend_server.sensors.sensor_service import AttitudeState

logger = logging.getLogger(__name__)
EventSink = Callable[[TelemetryEvent], Awaitable[None]]
AttitudeProvider = Callable[[], AttitudeState | None]
LevelOffsetsProvider = Callable[[], tuple[float, float]]


@dataclass(frozen=True, slots=True)
class AdaptiveWalkingConfig:
    learning_rate: float
    feedback_gain: float
    max_phase_lead_s: float
    velocity_regularizer: float
    max_phase_offset: float
    attitude_kp: float
    attitude_kd: float
    trim_adaptation_rate: float
    trim_leak_rate: float
    max_trim: float
    max_attitude_correction: float
    max_target_rate: float


@dataclass(frozen=True, slots=True)
class AdaptiveWalkingMemory:
    phase_leads_s: tuple[float, ...]
    roll_trim: float = 0.0
    pitch_trim: float = 0.0
    last_targets: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class AdaptiveWalkingResult:
    targets: tuple[int, ...]
    memory: AdaptiveWalkingMemory
    phase_offsets: tuple[float, ...]
    attitude_offsets: tuple[float, ...]


def compute_adaptive_walking_targets(
    *,
    nominal_targets: Sequence[float],
    nominal_velocities: Sequence[float],
    actual_positions: Sequence[float],
    roll_error_deg: float,
    pitch_error_deg: float,
    roll_error_rate_dps: float,
    pitch_error_rate_dps: float,
    dt: float,
    memory: AdaptiveWalkingMemory,
    config: AdaptiveWalkingConfig,
    mixing_matrix: Sequence[Sequence[float]],
) -> AdaptiveWalkingResult:
    """Return one bounded adaptive target update for all walking actuators.

    ``roll_error``/``pitch_error`` use the same control frame as the existing
    stabilizer.  Persistent errors update bounded gait trims; periodic tracking
    error learns a separate phase lead for each pneumatic actuator.
    """
    count = len(nominal_targets)
    if count == 0:
        raise ValueError("nominal_targets must not be empty")
    lengths = (
        len(nominal_velocities),
        len(actual_positions),
        len(memory.phase_leads_s),
        len(mixing_matrix),
    )
    if any(length != count for length in lengths):
        raise ValueError("all actuator vectors and the mixing matrix must have equal length")
    if dt <= 0.0 or not isfinite(dt):
        raise ValueError("dt must be finite and > 0")
    if not all(len(row) >= 2 for row in mixing_matrix):
        raise ValueError("each mixing row must contain roll and pitch coefficients")

    phase_leads: list[float] = []
    phase_offsets: list[float] = []
    for index in range(count):
        velocity = float(nominal_velocities[index])
        error = float(nominal_targets[index]) - float(actual_positions[index])
        normalized_gradient = (
            error * velocity
            / (velocity * velocity + config.velocity_regularizer)
        )
        lead = memory.phase_leads_s[index] + config.learning_rate * normalized_gradient * dt
        lead = _clamp(lead, config.max_phase_lead_s, lower=0.0)
        phase_leads.append(lead)
        phase_offset = lead * velocity + config.feedback_gain * error
        phase_offsets.append(_clamp(phase_offset, config.max_phase_offset))

    leak = max(0.0, 1.0 - config.trim_leak_rate * dt)
    roll_trim = memory.roll_trim * leak + config.trim_adaptation_rate * roll_error_deg * dt
    pitch_trim = memory.pitch_trim * leak + config.trim_adaptation_rate * pitch_error_deg * dt
    roll_trim = _clamp(roll_trim, config.max_trim)
    pitch_trim = _clamp(pitch_trim, config.max_trim)
    roll_output = (
        config.attitude_kp * roll_error_deg
        + config.attitude_kd * roll_error_rate_dps
        + roll_trim
    )
    pitch_output = (
        config.attitude_kp * pitch_error_deg
        + config.attitude_kd * pitch_error_rate_dps
        + pitch_trim
    )
    attitude_offsets = tuple(
        _clamp(
            float(mixing_matrix[index][0]) * roll_output
            + float(mixing_matrix[index][1]) * pitch_output,
            config.max_attitude_correction,
        )
        for index in range(count)
    )

    previous = (
        memory.last_targets
        if len(memory.last_targets) == count
        else tuple(float(value) for value in actual_positions)
    )
    max_delta = config.max_target_rate * dt
    targets: list[int] = []
    rate_limited: list[float] = []
    for index in range(count):
        desired = (
            float(nominal_targets[index])
            + phase_offsets[index]
            + attitude_offsets[index]
        )
        limited = previous[index] + _clamp(desired - previous[index], max_delta)
        limited = max(POSITION_MIN, min(POSITION_MAX, limited))
        rate_limited.append(limited)
        targets.append(int(round(limited)))

    next_memory = AdaptiveWalkingMemory(
        phase_leads_s=tuple(phase_leads),
        roll_trim=roll_trim,
        pitch_trim=pitch_trim,
        last_targets=tuple(rate_limited),
    )
    return AdaptiveWalkingResult(
        targets=tuple(targets),
        memory=next_memory,
        phase_offsets=tuple(phase_offsets),
        attitude_offsets=attitude_offsets,
    )


def _clamp(value: float, limit: float, *, lower: float | None = None) -> float:
    if lower is not None:
        return max(lower, min(limit, value))
    return max(-limit, min(limit, value))


def smooth_amplitude_scale(
    elapsed_s: float, ramp_s: float, full_scale: float
) -> tuple[float, float]:
    """Return smooth 0-to-full amplitude and its derivative per second."""
    progress = max(0.0, min(1.0, elapsed_s / ramp_s))
    amplitude = progress * progress * (3.0 - 2.0 * progress)
    rate = (
        0.0
        if progress in (0.0, 1.0)
        else 6.0 * progress * (1.0 - progress) / ramp_s
    )
    return full_scale * amplitude, full_scale * rate


class AdaptiveWalkingController:
    """Lease-controlled real-hardware walking loop.

    A browser must refresh the forward lease while the button is held.  Losing
    pointer-up, browser focus, network, fresh IMU data, or a serial write stops
    target updates and leaves the robot holding the last rate-limited posture.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        control_service: ControlService,
        attitude_provider: AttitudeProvider,
        level_offsets_provider: LevelOffsetsProvider,
        event_sink: EventSink,
        stabilization_engaged: Callable[[], bool],
        time_fn: Callable[[], float] = monotonic,
    ) -> None:
        self.settings = settings
        self._control = control_service
        self._attitude_provider = attitude_provider
        self._level_offsets_provider = level_offsets_provider
        self._event_sink = event_sink
        self._stabilization_engaged = stabilization_engaged
        self._time_fn = time_fn
        self._attitude_frame = AttitudeControlFrame(
            roll_sign=settings.stabilization_roll_sign,
            pitch_sign=settings.stabilization_pitch_sign,
        )
        self._mixing = model_derived_mixing_matrix()
        self._config = AdaptiveWalkingConfig(
            learning_rate=settings.adaptive_walk_learning_rate,
            feedback_gain=settings.adaptive_walk_feedback_gain,
            max_phase_lead_s=settings.adaptive_walk_max_phase_lead_s,
            velocity_regularizer=settings.adaptive_walk_velocity_regularizer,
            max_phase_offset=settings.adaptive_walk_max_phase_offset,
            attitude_kp=settings.adaptive_walk_attitude_kp,
            attitude_kd=settings.adaptive_walk_attitude_kd,
            trim_adaptation_rate=settings.adaptive_walk_trim_rate,
            trim_leak_rate=settings.adaptive_walk_trim_leak_rate,
            max_trim=settings.adaptive_walk_max_trim,
            max_attitude_correction=settings.adaptive_walk_max_attitude_correction,
            max_target_rate=settings.adaptive_walk_max_target_rate,
        )
        self._memory = AdaptiveWalkingMemory(
            phase_leads_s=(settings.adaptive_walk_initial_phase_lead_s,)
            * settings.actuator_count
        )
        self._task: asyncio.Task[None] | None = None
        self._active = False
        self._auto_stopped = False
        self._stopped_reason: str | None = None
        self._lease_deadline = 0.0
        self._walk_started_at = 0.0
        self._last_step_at: float | None = None
        self._phase = 0.0
        self._current_motion_scale = 0.0
        self._roll_deg = 0.0
        self._pitch_deg = 0.0
        self._imu_stale = True
        self._rows: tuple[tuple[float, ...], ...] = ()
        self._interval_s = 0.03
        self._last_publish_at = 0.0
        self._request_lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        return self._active

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="adaptive-walking")

    async def stop(self) -> None:
        await self.release("controller shutdown")
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def set_forward_pressed(
        self, pressed: bool, *, safety_confirmed: bool
    ) -> AdaptiveWalkState:
        async with self._request_lock:
            return await self._set_forward_pressed_locked(
                pressed,
                safety_confirmed=safety_confirmed,
            )

    async def _set_forward_pressed_locked(
        self, pressed: bool, *, safety_confirmed: bool
    ) -> AdaptiveWalkState:
        if not pressed:
            await self.release("button released")
            return self.get_state()
        if not safety_confirmed:
            raise ValueError("safety confirmation is required")

        now = self._time_fn()
        if self._active:
            self._lease_deadline = now + self.settings.adaptive_walk_lease_timeout_sec
            return self.get_state()
        if self._stabilization_engaged():
            raise RuntimeError("disable standalone stabilization before adaptive walking")
        if self._control.system_status.connection_state is not ConnectionState.CONNECTED:
            raise RuntimeError("both actuator controllers must be connected before walking")
        if not self.settings.emulate_devices and not self._control.has_fresh_actuator_telemetry(
            self.settings.adaptive_walk_max_actuator_staleness_sec
        ):
            raise RuntimeError("fresh telemetry from all actuators is required before walking")
        snapshot = self._require_healthy_attitude(now)
        roll, pitch = self._control_tilt(snapshot)
        if (
            abs(roll) > self.settings.adaptive_walk_max_tilt_deg
            or abs(pitch) > self.settings.adaptive_walk_max_tilt_deg
        ):
            raise RuntimeError("robot tilt exceeds adaptive walking start limit")

        detail = self._control.get_motion_file(MotionCategory.FIXED, "rabbit_bound")
        rows = tuple(
            tuple(float(value) for value in row[: self.settings.actuator_count])
            for row in detail.rows
        )
        if not rows or any(len(row) != self.settings.actuator_count for row in rows):
            raise RuntimeError("fixed rabbit_bound motion must contain all actuator targets")

        await self._control.claim_adaptive_walking()
        self._rows = rows
        self._interval_s = detail.item.interval_sec or 0.03
        actual = tuple(float(item.telemetry.position) for item in self._control.list_actuators())
        self._memory = AdaptiveWalkingMemory(
            phase_leads_s=(self.settings.adaptive_walk_initial_phase_lead_s,)
            * self.settings.actuator_count,
            last_targets=actual,
        )
        self._active = True
        self._auto_stopped = False
        self._stopped_reason = None
        self._lease_deadline = now + self.settings.adaptive_walk_lease_timeout_sec
        self._walk_started_at = now
        self._last_step_at = None
        self._current_motion_scale = 0.0
        await self._publish(force=True)
        return self.get_state()

    async def release(self, reason: str = "button released", *, automatic: bool = False) -> None:
        was_active = self._active
        self._active = False
        self._current_motion_scale = 0.0
        # A late pointer-up may arrive after an automatic stop. Preserve the
        # actionable safety reason instead of overwriting it with "released".
        if was_active or automatic:
            self._auto_stopped = automatic
            self._stopped_reason = reason
        await self._control.release_adaptive_walking()
        if was_active or automatic:
            await self._publish(force=True)

    def get_state(self) -> AdaptiveWalkState:
        now = self._time_fn()
        return AdaptiveWalkState(
            active=self._active,
            auto_stopped=self._auto_stopped,
            stopped_reason=self._stopped_reason,
            phase=self._phase,
            roll_deg=self._roll_deg,
            pitch_deg=self._pitch_deg,
            imu_stale=self._imu_stale,
            motion_scale=self._current_motion_scale,
            lease_remaining_ms=max(0, int(round((self._lease_deadline - now) * 1000)))
            if self._active
            else 0,
            roll_trim=self._memory.roll_trim,
            pitch_trim=self._memory.pitch_trim,
            learned_phase_lead_s=list(self._memory.phase_leads_s),
        )

    async def _run(self) -> None:
        period = 1.0 / self.settings.adaptive_walk_rate_hz
        while True:
            await asyncio.sleep(period)
            if not self._active:
                continue
            try:
                await self._step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The release endpoint can revoke ownership while a step is in
                # flight.  That expected race must not be reported as a fault.
                if self._active:
                    logger.exception("Adaptive walking stopped")
                    await self.release(str(exc), automatic=True)

    async def _step(self) -> None:
        now = self._time_fn()
        if now > self._lease_deadline:
            await self.release("forward-button lease expired", automatic=True)
            return
        if self._stabilization_engaged():
            await self.release("standalone stabilization enabled", automatic=True)
            return
        if self._control.system_status.connection_state is not ConnectionState.CONNECTED:
            await self.release("actuator controller connection lost", automatic=True)
            return
        if not self.settings.emulate_devices and not self._control.has_fresh_actuator_telemetry(
            self.settings.adaptive_walk_max_actuator_staleness_sec
        ):
            await self.release("actuator telemetry stale", automatic=True)
            return
        snapshot = self._require_healthy_attitude(now)
        roll, pitch = self._control_tilt(snapshot)
        self._roll_deg = roll
        self._pitch_deg = pitch
        if (
            abs(roll) > self.settings.adaptive_walk_max_tilt_deg
            or abs(pitch) > self.settings.adaptive_walk_max_tilt_deg
        ):
            await self.release("tilt limit exceeded", automatic=True)
            return

        dt = (
            1.0 / self.settings.adaptive_walk_rate_hz
            if self._last_step_at is None
            else max(0.001, min(0.2, now - self._last_step_at))
        )
        self._last_step_at = now
        elapsed = now - self._walk_started_at
        nominal, velocity, phase = self._trajectory_sample(elapsed)
        self._phase = phase
        actual = tuple(float(item.telemetry.position) for item in self._control.list_actuators())
        roll_rate, pitch_rate = self._attitude_frame.error_rates(
            gyro_x_dps=snapshot.gyro_dps.x,
            gyro_y_dps=snapshot.gyro_dps.y,
        )
        result = compute_adaptive_walking_targets(
            nominal_targets=nominal,
            nominal_velocities=velocity,
            actual_positions=actual,
            roll_error_deg=-roll,
            pitch_error_deg=-pitch,
            roll_error_rate_dps=roll_rate,
            pitch_error_rate_dps=pitch_rate,
            dt=dt,
            memory=self._memory,
            config=self._config,
            mixing_matrix=self._mixing,
        )
        self._memory = result.memory
        await self._control.apply_adaptive_walking_targets(list(result.targets))
        await self._publish()

    def _trajectory_sample(
        self, elapsed: float
    ) -> tuple[tuple[float, ...], tuple[float, ...], float]:
        frame_position = elapsed / self._interval_s
        frame_index = int(frame_position) % len(self._rows)
        next_index = (frame_index + 1) % len(self._rows)
        fraction = frame_position - int(frame_position)
        current = self._rows[frame_index]
        following = self._rows[next_index]
        raw_nominal = tuple(
            current[index] + (following[index] - current[index]) * fraction
            for index in range(len(current))
        )
        raw_velocity = tuple(
            (following[index] - current[index]) / self._interval_s
            for index in range(len(current))
        )
        scale, scale_rate = smooth_amplitude_scale(
            elapsed,
            self.settings.adaptive_walk_motion_ramp_sec,
            self.settings.adaptive_walk_motion_scale,
        )
        self._current_motion_scale = scale
        neutral = (POSITION_MIN + POSITION_MAX) / 2.0
        nominal = tuple(neutral + (value - neutral) * scale for value in raw_nominal)
        velocity = tuple(
            raw_velocity[index] * scale + (raw_nominal[index] - neutral) * scale_rate
            for index in range(len(raw_nominal))
        )
        return nominal, velocity, (frame_position % len(self._rows)) / len(self._rows)

    def _require_healthy_attitude(self, now: float) -> AttitudeState:
        snapshot = self._attitude_provider()
        self._imu_stale = (
            snapshot is None
            or now - snapshot.timestamp > self.settings.adaptive_walk_max_imu_staleness_sec
        )
        if snapshot is None:
            raise RuntimeError("IMU attitude unavailable")
        if self._imu_stale:
            raise RuntimeError("IMU attitude stale")
        values = (
            snapshot.euler.roll_deg,
            snapshot.euler.pitch_deg,
            snapshot.gyro_dps.x,
            snapshot.gyro_dps.y,
        )
        if not all(isfinite(value) for value in values):
            raise RuntimeError("IMU attitude invalid")
        return snapshot

    def _control_tilt(self, snapshot: AttitudeState) -> tuple[float, float]:
        level_roll, level_pitch = self._level_offsets_provider()
        return self._attitude_frame.tilt(
            raw_roll_deg=snapshot.euler.roll_deg,
            raw_pitch_deg=snapshot.euler.pitch_deg,
            level_roll_offset_deg=level_roll,
            level_pitch_offset_deg=level_pitch,
        )

    async def _publish(self, *, force: bool = False) -> None:
        now = self._time_fn()
        if not force and now - self._last_publish_at < 0.12:
            return
        self._last_publish_at = now
        await self._event_sink(
            TelemetryEvent(
                type="adaptive_walk_state",
                payload={"adaptive_walk": self.get_state().model_dump(mode="json")},
            )
        )
