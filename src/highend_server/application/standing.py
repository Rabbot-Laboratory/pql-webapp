"""Standing hold: rise to the home pose, then actively keep it.

The startup flow becomes boot -> IMU calibration -> STAND (this controller)
-> walk. Holding is the small-error regime where the measured ESP32+valve
dead zone (~±300 units) defeats plain position targets, so the hold loop
"overdrives": axes whose error exceeds a tolerance are commanded PAST the
stand pose (gain * error, clamped) until they re-enter the tolerance band,
where they get the plain target again and the valves rest.

The numerical law is isolated in ``compute_standing_targets`` (no asyncio /
serial / sensor dependency) following ``compute_adaptive_walking_targets``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from math import isfinite
from time import monotonic

from highend_server.application.attitude_control_frame import AttitudeControlFrame
from highend_server.application.control_service import (
    ControlService,
    build_rate_limited_position_rows,
)
from highend_server.application.pql_a00_kinematics import model_derived_mixing_matrix
from highend_server.config import Settings
from highend_server.domain.models import (
    POSITION_MAX,
    POSITION_MIN,
    ConnectionState,
    MotionCategory,
    StandingPhase,
    StandingState,
    TelemetryEvent,
)
from highend_server.sensors.sensor_service import AttitudeState

logger = logging.getLogger(__name__)
EventSink = Callable[[TelemetryEvent], Awaitable[None]]
AttitudeProvider = Callable[[], AttitudeState | None]
LevelOffsetsProvider = Callable[[], tuple[float, float]]

OWNER_NAME = "standing"


@dataclass(frozen=True, slots=True)
class StandingConfig:
    hold_tolerance: float
    overdrive_gain: float
    max_overdrive: float
    max_target_rate: float
    attitude_kp: float
    attitude_kd: float
    max_attitude_correction: float


@dataclass(frozen=True, slots=True)
class StandingResult:
    targets: tuple[int, ...]
    axis_errors: tuple[int, ...]
    overdrive_active: tuple[bool, ...]
    last_sent: tuple[float, ...]


def compute_standing_targets(
    *,
    stand_targets: Sequence[float],
    actual_positions: Sequence[float],
    roll_error_deg: float,
    pitch_error_deg: float,
    roll_error_rate_dps: float,
    pitch_error_rate_dps: float,
    dt: float,
    last_sent: Sequence[float],
    config: StandingConfig,
    mixing_matrix: Sequence[Sequence[float]],
) -> StandingResult:
    """One bounded hold update: attitude trim + dead-zone overdrive."""
    count = len(stand_targets)
    if count == 0:
        raise ValueError("stand_targets must not be empty")
    if len(actual_positions) != count or len(mixing_matrix) != count:
        raise ValueError("all actuator vectors and the mixing matrix must have equal length")
    if dt <= 0.0 or not isfinite(dt):
        raise ValueError("dt must be finite and > 0")

    roll_output = config.attitude_kp * roll_error_deg + config.attitude_kd * roll_error_rate_dps
    pitch_output = (
        config.attitude_kp * pitch_error_deg + config.attitude_kd * pitch_error_rate_dps
    )

    previous = (
        last_sent
        if len(last_sent) == count
        else tuple(float(value) for value in actual_positions)
    )
    max_delta = config.max_target_rate * dt
    targets: list[int] = []
    sent: list[float] = []
    axis_errors: list[int] = []
    overdrive_flags: list[bool] = []
    for index in range(count):
        attitude_offset = _clamp(
            float(mixing_matrix[index][0]) * roll_output
            + float(mixing_matrix[index][1]) * pitch_output,
            config.max_attitude_correction,
        )
        desired_hold = float(stand_targets[index]) + attitude_offset
        error = desired_hold - float(actual_positions[index])
        axis_errors.append(int(round(error)))
        if abs(error) > config.hold_tolerance:
            # Exaggerate the command so the ESP32 PID output clears the valve
            # opening threshold; withdrawn once the axis re-enters the band.
            desired = desired_hold + _clamp(
                config.overdrive_gain * error, config.max_overdrive
            )
            overdrive_flags.append(True)
        else:
            desired = desired_hold
            overdrive_flags.append(False)
        limited = previous[index] + _clamp(desired - previous[index], max_delta)
        limited = max(POSITION_MIN, min(POSITION_MAX, limited))
        sent.append(limited)
        targets.append(int(round(limited)))

    return StandingResult(
        targets=tuple(targets),
        axis_errors=tuple(axis_errors),
        overdrive_active=tuple(overdrive_flags),
        last_sent=tuple(sent),
    )


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


class StandingController:
    """Toggle-style stand-and-hold loop with walking-grade safety guards."""

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
        self._config = StandingConfig(
            hold_tolerance=settings.standing_hold_tolerance,
            overdrive_gain=settings.standing_overdrive_gain,
            max_overdrive=settings.standing_max_overdrive,
            max_target_rate=settings.standing_max_target_rate,
            attitude_kp=settings.standing_attitude_kp,
            attitude_kd=settings.standing_attitude_kd,
            max_attitude_correction=settings.standing_max_attitude_correction,
        )
        self._task: asyncio.Task[None] | None = None
        self._enabled = False
        self._phase = StandingPhase.OFF
        self._auto_disabled = False
        self._disabled_reason: str | None = None
        self._stand_targets: tuple[float, ...] = ()
        self._rise_rows: list[list[float]] = []
        self._rise_index = 0
        self._last_sent: tuple[float, ...] = ()
        self._last_step_at: float | None = None
        self._roll_deg = 0.0
        self._pitch_deg = 0.0
        self._axis_errors: tuple[int, ...] = ()
        self._overdrive_active: tuple[bool, ...] = ()
        self._ok_since: float | None = None
        self._standing_ok = False
        self._last_publish_at = 0.0
        self._request_lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="standing")

    async def stop(self) -> None:
        if self._enabled:
            await self._disable("controller shutdown", automatic=False)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    # -- public state ----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def standing_ok(self) -> bool:
        return self._enabled and self._standing_ok

    def get_state(self) -> StandingState:
        return StandingState(
            enabled=self._enabled,
            phase=self._phase,
            standing_ok=self.standing_ok,
            walk_gate_enabled=self.settings.adaptive_walk_require_standing,
            auto_disabled=self._auto_disabled,
            disabled_reason=self._disabled_reason,
            roll_deg=self._roll_deg,
            pitch_deg=self._pitch_deg,
            axis_errors=list(self._axis_errors),
            overdrive_active=list(self._overdrive_active),
        )

    # -- enable / disable ------------------------------------------------------

    async def set_enabled(self, enabled: bool, *, safety_confirmed: bool) -> StandingState:
        async with self._request_lock:
            if not enabled:
                if self._enabled:
                    await self._disable("disabled by operator", automatic=False)
                return self.get_state()
            if self._enabled:
                return self.get_state()
            if not safety_confirmed:
                raise ValueError("safety confirmation is required")
            if self._stabilization_engaged():
                raise RuntimeError("disable standalone stabilization before standing")
            if self._control.system_status.connection_state is not ConnectionState.CONNECTED:
                raise RuntimeError("both actuator controllers must be connected before standing")
            if not self.settings.emulate_devices and not self._control.has_fresh_actuator_telemetry(
                self.settings.adaptive_walk_max_actuator_staleness_sec
            ):
                raise RuntimeError("fresh telemetry from all actuators is required before standing")
            now = self._time_fn()
            snapshot = self._require_healthy_attitude(now)
            roll, pitch = self._control_tilt(snapshot)
            if (
                abs(roll) > self.settings.standing_max_tilt_deg
                or abs(pitch) > self.settings.standing_max_tilt_deg
            ):
                raise RuntimeError("robot tilt exceeds the standing start limit")

            detail = self._control.get_motion_file(MotionCategory.FIXED, "home")
            if not detail.rows:
                raise RuntimeError("Fixed Motion/home.csv must contain a target row")
            target = [
                float(value) for value in detail.rows[-1][: self.settings.actuator_count]
            ]
            if len(target) != self.settings.actuator_count:
                raise RuntimeError("Fixed Motion/home.csv must contain all actuator targets")

            await self._control.claim_adaptive_walking(owner=OWNER_NAME)
            start = [
                float(item.telemetry.position) for item in self._control.list_actuators()
            ]
            rows = build_rate_limited_position_rows(
                [int(round(value)) for value in start],
                [int(round(value)) for value in target],
                max_rate=self.settings.standing_rise_rate,
                interval_sec=1.0 / self.settings.standing_rate_hz,
            )
            self._stand_targets = tuple(target)
            self._rise_rows = [[float(cell) for cell in row] for row in rows]
            self._rise_index = 0
            self._last_sent = tuple(start)
            self._last_step_at = None
            self._axis_errors = ()
            self._overdrive_active = ()
            self._ok_since = None
            self._standing_ok = False
            self._enabled = True
            self._auto_disabled = False
            self._disabled_reason = None
            self._phase = StandingPhase.RISING
            await self._publish(force=True)
            return self.get_state()

    async def release_for_handover(self) -> None:
        """Hand target ownership to the walking controller without moving."""
        if not self._enabled:
            return
        self._enabled = False
        self._phase = StandingPhase.OFF
        self._standing_ok = False
        self._ok_since = None
        self._disabled_reason = "handed over to walking"
        self._auto_disabled = False
        await self._control.release_adaptive_walking()
        await self._publish(force=True)

    async def _disable(self, reason: str, *, automatic: bool) -> None:
        was_enabled = self._enabled
        self._enabled = False
        self._phase = StandingPhase.OFF
        self._standing_ok = False
        self._ok_since = None
        if was_enabled or automatic:
            self._auto_disabled = automatic
            self._disabled_reason = reason
        await self._control.release_adaptive_walking()
        if was_enabled or automatic:
            await self._publish(force=True)

    # -- loop ------------------------------------------------------------------

    async def _run(self) -> None:
        period = 1.0 / self.settings.standing_rate_hz
        while True:
            await asyncio.sleep(period)
            if not self._enabled:
                continue
            try:
                await self._step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._enabled:
                    logger.exception("Standing hold stopped")
                    await self._disable(str(exc), automatic=True)

    async def _step(self) -> None:
        now = self._time_fn()
        if self._stabilization_engaged():
            await self._disable("standalone stabilization enabled", automatic=True)
            return
        if self._control.system_status.connection_state is not ConnectionState.CONNECTED:
            await self._disable("actuator controller connection lost", automatic=True)
            return
        if not self.settings.emulate_devices and not self._control.has_fresh_actuator_telemetry(
            self.settings.adaptive_walk_max_actuator_staleness_sec
        ):
            await self._disable("actuator telemetry stale", automatic=True)
            return
        snapshot = self._require_healthy_attitude(now)
        roll, pitch = self._control_tilt(snapshot)
        self._roll_deg = roll
        self._pitch_deg = pitch
        if (
            abs(roll) > self.settings.standing_max_tilt_deg
            or abs(pitch) > self.settings.standing_max_tilt_deg
        ):
            await self._disable("tilt limit exceeded", automatic=True)
            return

        dt = (
            1.0 / self.settings.standing_rate_hz
            if self._last_step_at is None
            else max(0.001, min(0.2, now - self._last_step_at))
        )
        self._last_step_at = now

        if self._phase is StandingPhase.RISING:
            if self._rise_index < len(self._rise_rows):
                row = self._rise_rows[self._rise_index]
                self._rise_index += 1
                self._last_sent = tuple(row)
                await self._control.apply_adaptive_walking_targets(
                    [int(round(value)) for value in row]
                )
                await self._publish()
                return
            self._phase = StandingPhase.HOLDING
            await self._publish(force=True)

        actual = tuple(
            float(item.telemetry.position) for item in self._control.list_actuators()
        )
        roll_rate, pitch_rate = self._attitude_frame.error_rates(
            gyro_x_dps=snapshot.gyro_dps.x,
            gyro_y_dps=snapshot.gyro_dps.y,
        )
        result = compute_standing_targets(
            stand_targets=self._stand_targets,
            actual_positions=actual,
            roll_error_deg=-roll,
            pitch_error_deg=-pitch,
            roll_error_rate_dps=roll_rate,
            pitch_error_rate_dps=pitch_rate,
            dt=dt,
            last_sent=self._last_sent,
            config=self._config,
            mixing_matrix=self._mixing,
        )
        self._last_sent = result.last_sent
        self._axis_errors = result.axis_errors
        self._overdrive_active = result.overdrive_active
        await self._control.apply_adaptive_walking_targets(list(result.targets))
        self._update_standing_ok(now, actual, roll, pitch)
        await self._publish()

    def _update_standing_ok(
        self,
        now: float,
        actual: tuple[float, ...],
        roll: float,
        pitch: float,
    ) -> None:
        within_pose = all(
            abs(float(target) - float(position)) <= self.settings.standing_ok_tolerance
            for target, position in zip(self._stand_targets, actual, strict=True)
        )
        level = (
            abs(roll) <= self.settings.standing_ok_max_tilt_deg
            and abs(pitch) <= self.settings.standing_ok_max_tilt_deg
        )
        if within_pose and level:
            if self._ok_since is None:
                self._ok_since = now
            became_ok = now - self._ok_since >= self.settings.standing_ok_hold_sec
            if became_ok and not self._standing_ok:
                self._standing_ok = True
                # Force a publish so the walk gate opens without waiting for
                # the next throttled update.
                self._last_publish_at = 0.0
        else:
            self._ok_since = None
            if self._standing_ok:
                self._standing_ok = False
                self._last_publish_at = 0.0

    # -- helpers (same conventions as the walking controller) -----------------

    def _require_healthy_attitude(self, now: float) -> AttitudeState:
        snapshot = self._attitude_provider()
        if snapshot is None:
            raise RuntimeError("IMU attitude unavailable")
        if now - snapshot.timestamp > self.settings.adaptive_walk_max_imu_staleness_sec:
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
                type="standing_state",
                payload={"standing": self.get_state().model_dump(mode="json")},
            )
        )
