"""Attitude feedback stabilization (Phase 2).

Closes a slow outer loop around the IMU: it reads the fused roll/pitch, runs a
per-axis PID, maps the PID output through a leg-geometry *mixing matrix* into
per-actuator position-target corrections, and hands those corrections to
:class:`~highend_server.application.control_service.ControlService`, which adds
them on top of the user/CSV base targets before sending serial frames.

The loop is DISABLED by default and never auto-enables on boot; enabling
requires an explicit API call. Every safety mechanism the plan mandates lives
here: correction clamp, output rate limiter, smooth ramp-to-zero on disable, and
auto-disable on excessive tilt, stale attitude, or repeated serial failures.

Coordinate / sign convention (single source of truth is
``sensors/attitude.py``)
=========================================================================
IMU body frame: +X forward, +Y left, +Z up (right-handed).

    * roll  > 0  => right side down   (rotation about body +X)
    * pitch > 0  => nose up / front up (rotation about body +Y)

Actuator <-> leg map (from ControlService labels, confirmed against the URDF
joint origins in ``pql-a00_description``):

    id 0,1 = Front-Right  (right, front)
    id 2,3 = Front-Left   (left,  front)
    id 4,5 = Rear-Right   (right, rear)
    id 6,7 = Rear-Left    (left,  rear)

Correction sign convention: ``+correction`` *extends* that leg (raises that
corner of the body); ``-correction`` retracts it (lowers that corner).
Corrections are position-target offsets in the 0..4095 unit space.

PID error is ``e = setpoint(0) - measured`` so:

    * right-side-down (roll>0)   -> e_roll  < 0 -> u_roll  < 0
    * nose-up        (pitch>0)   -> e_pitch < 0 -> u_pitch < 0

To right the body we must EXTEND the low corners. The mixing matrix columns are
therefore signed so that:

    * a right-side-down tilt (u_roll<0) yields +correction (extend) on the RIGHT
      legs and -correction (retract) on the LEFT legs
      => right legs roll coeff = -1, left legs roll coeff = +1
    * a nose-up tilt (u_pitch<0) yields +correction (extend) on the REAR legs and
      -correction (retract) on the FRONT legs
      => front legs pitch coeff = +1, rear legs pitch coeff = -1

The default matrix below is a best-effort derivation. The true actuator sign
depends on pneumatic plumbing that cannot be confirmed from code, so it is
overridable via ``config/stabilization.json`` and must be verified on-robot
(with tiny gains) before trusting the signs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import monotonic

from pydantic import BaseModel, Field

from highend_server.application.control_service import ControlService
from highend_server.config import Settings
from highend_server.domain.models import (
    StabilizationCorrection,
    StabilizationGains,
    StabilizationRequest,
    StabilizationState,
    TelemetryEvent,
    utc_now,
)
from highend_server.sensors.sensor_service import AttitudeState

logger = logging.getLogger(__name__)

EventSink = Callable[[TelemetryEvent], Awaitable[None]]
AttitudeProvider = Callable[[], AttitudeState | None]
LevelOffsetsProvider = Callable[[], tuple[float, float]]

# If the control loop body raises this many times in a row, force every
# correction to zero via a best-effort direct call and keep the loop alive but
# idle. The loop must never die and latch the last corrections on hardware.
MAX_CONSECUTIVE_STEP_FAILURES = 5

# Columns are [roll, pitch]; rows are indexed by actuator id (see module docstring).
DEFAULT_MIXING_MATRIX: list[list[float]] = [
    [-1.0, +1.0],  # 0 Front-Right hip
    [-1.0, +1.0],  # 1 Front-Right knee
    [+1.0, +1.0],  # 2 Front-Left  hip
    [+1.0, +1.0],  # 3 Front-Left  knee
    [-1.0, -1.0],  # 4 Rear-Right  hip
    [-1.0, -1.0],  # 5 Rear-Right  knee
    [+1.0, -1.0],  # 6 Rear-Left   hip
    [+1.0, -1.0],  # 7 Rear-Left   knee
]


def _clamp(value: float, limit: float) -> float:
    # A NaN value satisfies neither comparison below and would pass through
    # unclamped. Treat any non-finite correction as zero (fail-safe): a NaN or
    # Inf must never reach an actuator target.
    if not isfinite(value):
        return 0.0
    if value > limit:
        return limit
    if value < -limit:
        return -limit
    return value


class StabilizationPersistedConfig(BaseModel):
    """Serialized to ``config/stabilization.json`` (imu_calibration.json pattern).

    ``enabled`` is intentionally NOT persisted: stabilization must never come up
    active after a restart.
    """

    gains: StabilizationGains = Field(default_factory=StabilizationGains)
    mixing_matrix: list[list[float]] | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class AxisPid:
    """Single-axis PID with integral anti-windup and output saturation."""

    def __init__(
        self,
        *,
        kp: float,
        ki: float,
        kd: float,
        integral_limit: float,
        output_limit: float,
    ) -> None:
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.integral_limit = integral_limit
        self.output_limit = output_limit
        self._integral = 0.0
        self._prev_error: float | None = None

    @property
    def integral(self) -> float:
        return self._integral

    def reset(self) -> None:
        self._integral = 0.0
        self._prev_error = None

    def set_gains(self, kp: float, ki: float, kd: float) -> None:
        self.kp = kp
        self.ki = ki
        self.kd = kd

    def step(self, error: float, dt: float, rate: float | None = None) -> float:
        """Run one PID update.

        ``rate``, when given, is ``d(error)/dt`` supplied directly by the
        caller (e.g. derived from the gyro) instead of finite-differencing
        ``error`` here. ``_prev_error`` is still updated in that case so
        switching back to finite-difference mode mid-flight (rate becomes
        None on a later call) is seamless rather than producing one bogus
        derivative spike from a stale ``_prev_error``.
        """
        if self.ki > 0.0 and dt > 0.0:
            self._integral += error * dt
            # Anti-windup: clamp the accumulator so it can never run away while
            # the output is saturated or the error persists.
            self._integral = _clamp(self._integral, self.integral_limit)
        else:
            self._integral = 0.0

        if rate is not None:
            derivative = rate
        else:
            derivative = 0.0
            if self._prev_error is not None and dt > 0.0:
                derivative = (error - self._prev_error) / dt
        self._prev_error = error

        output = self.kp * error + self.ki * self._integral + self.kd * derivative
        return _clamp(output, self.output_limit)


class StabilizationController:
    """Async 25 Hz (config) loop turning roll/pitch error into corrections."""

    def __init__(
        self,
        *,
        settings: Settings,
        control_service: ControlService,
        attitude_provider: AttitudeProvider,
        level_offsets_provider: LevelOffsetsProvider | None = None,
        event_sink: EventSink,
        actuator_count: int | None = None,
        config_path: Path | None = None,
        time_fn: Callable[[], float] = monotonic,
        calibration_lock: asyncio.Lock | None = None,
    ) -> None:
        self.settings = settings
        self._control = control_service
        self._attitude_provider = attitude_provider
        self._level_offsets_provider = level_offsets_provider or (lambda: (0.0, 0.0))
        self._event_sink = event_sink
        self._time_fn = time_fn
        self._n = actuator_count if actuator_count is not None else settings.actuator_count
        self._config_path = config_path or settings.stabilization_config_path
        # SensorService's calibration lock (shared via main.py wiring). Enabling
        # while a calibration coroutine holds it must wait, and the calibration
        # side re-checks `enabled` inside the lock — closing the check-then-act
        # race between "enable stabilization" and "calibrate IMU".
        self._calibration_lock = calibration_lock

        persisted = self._load_config()
        self._gains = persisted.gains
        self._mixing = self._validate_mixing(persisted.mixing_matrix)

        # Read once at construction: the loop always uses one strategy per
        # process lifetime, matching how gains/mixing are loaded (a config
        # change requires a restart, same as other stabilization settings).
        self._derivative_source = settings.stabilization_derivative_source

        self._max_correction = settings.stabilization_max_correction
        self._pid_roll = AxisPid(
            kp=self._gains.kp_roll,
            ki=self._gains.ki_roll,
            kd=self._gains.kd_roll,
            integral_limit=settings.stabilization_integral_limit,
            output_limit=self._max_correction,
        )
        self._pid_pitch = AxisPid(
            kp=self._gains.kp_pitch,
            ki=self._gains.ki_pitch,
            kd=self._gains.kd_pitch,
            integral_limit=settings.stabilization_integral_limit,
            output_limit=self._max_correction,
        )

        self._enabled = False
        self._auto_disabled = False
        self._disabled_reason: str | None = None
        self._corrections = [0.0] * self._n
        self._roll_deg = 0.0
        self._pitch_deg = 0.0
        self._roll_error_deg = 0.0
        self._pitch_error_deg = 0.0
        self._attitude_stale = False
        self._loop_rate_hz = 0.0
        self._serial_failures = 0
        self._consecutive_step_failures = 0

        self._task: asyncio.Task[None] | None = None
        self._last_step_t: float | None = None
        self._last_publish_t = 0.0
        self._last_signature: tuple | None = None

    # -- config load / save -------------------------------------------------

    def _load_config(self) -> StabilizationPersistedConfig:
        path = self._config_path
        if not path.exists():
            return StabilizationPersistedConfig()
        try:
            return StabilizationPersistedConfig.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception:
            logger.exception("Failed to load stabilization config from %s", path)
            return StabilizationPersistedConfig()

    async def _save_config(self) -> None:
        await asyncio.to_thread(self._save_config_sync)

    def _save_config_sync(self) -> None:
        path = self._config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = StabilizationPersistedConfig(
            gains=self._gains,
            mixing_matrix=self._mixing,
            updated_at=datetime.now(UTC),
        )
        path.write_text(json.dumps(payload.model_dump(mode="json"), indent=2), encoding="utf-8")

    def _validate_mixing(self, matrix: list[list[float]] | None) -> list[list[float]]:
        if matrix is not None and len(matrix) == self._n and all(len(row) == 2 for row in matrix):
            return [[float(row[0]), float(row[1])] for row in matrix]
        if matrix is not None:
            logger.warning(
                "Ignoring stabilization mixing override with wrong shape %s (need %dx2)",
                [len(row) for row in matrix],
                self._n,
            )
        # Slicing would silently truncate for actuator_count > 8 and every loop
        # tick would then IndexError; fail loudly at construction instead.
        if self._n > len(DEFAULT_MIXING_MATRIX):
            raise ValueError(
                f"actuator_count={self._n} exceeds the {len(DEFAULT_MIXING_MATRIX)}-row default "
                "stabilization mixing matrix; provide a full mixing_matrix override in "
                f"{self._config_path}"
            )
        return [list(row) for row in DEFAULT_MIXING_MATRIX[: self._n]]

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._last_step_t = None
        self._task = asyncio.create_task(self._run(), name="stabilization")

    async def stop(self) -> None:
        # Best-effort smooth ramp-down before tearing the task down.
        self._enabled = False
        task = self._task
        self._task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # The task may have already died from an unexpected error. Never let
            # that abort the caller (main.py lifespan finally-block must still
            # run sensor stop + serial shutdown).
            logger.exception("Stabilization task ended with an error during stop()")

    # -- public API ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """User intent: stabilization has been switched on (may be auto-disabled)."""
        return self._enabled

    @property
    def active(self) -> bool:
        """Currently driving corrections (enabled and not auto-disabled/stale)."""
        return self._enabled and not self._auto_disabled and not self._attitude_stale

    def get_state(self) -> StabilizationState:
        active = self._enabled and not self._auto_disabled and not self._attitude_stale
        corrections = [
            StabilizationCorrection(
                actuator_id=index,
                label=self._control.get_actuator(index).label,
                correction=self._corrections[index],
            )
            for index in range(self._n)
        ]
        return StabilizationState(
            enabled=self._enabled,
            active=active,
            auto_disabled=self._auto_disabled,
            disabled_reason=self._disabled_reason,
            gains=self._gains,
            roll_deg=self._roll_deg,
            pitch_deg=self._pitch_deg,
            roll_error_deg=self._roll_error_deg,
            pitch_error_deg=self._pitch_error_deg,
            corrections=corrections,
            loop_rate_hz=self._loop_rate_hz,
            attitude_stale=self._attitude_stale,
            derivative_source=self._derivative_source,
            updated_at=utc_now(),
        )

    async def apply_request(self, request: StabilizationRequest) -> StabilizationState:
        changed = False
        if request.gains is not None:
            self._gains = request.gains
            self._pid_roll.set_gains(
                request.gains.kp_roll, request.gains.ki_roll, request.gains.kd_roll
            )
            self._pid_pitch.set_gains(
                request.gains.kp_pitch, request.gains.ki_pitch, request.gains.kd_pitch
            )
            self._pid_roll.reset()
            self._pid_pitch.reset()
            changed = True

        if request.enabled is not None:
            if request.enabled:
                if self._calibration_lock is not None:
                    # Serialize the enable transition against in-flight IMU
                    # calibration (see __init__). Disable stays lock-free: it
                    # must never block on a long-running calibration.
                    async with self._calibration_lock:
                        self._enable()
                else:
                    self._enable()
            else:
                self._enabled = False  # loop ramps corrections to zero

        if changed:
            await self._save_config()

        await self._publish(force=True)
        return self.get_state()

    def _enable(self) -> None:
        self._enabled = True
        self._auto_disabled = False
        self._disabled_reason = None
        self._serial_failures = 0
        self._pid_roll.reset()
        self._pid_pitch.reset()

    def _trigger_auto_disable(self, reason: str) -> None:
        if not self._auto_disabled:
            logger.warning("Stabilization auto-disabled: %s", reason)
        self._auto_disabled = True
        self._disabled_reason = reason
        self._pid_roll.reset()
        self._pid_pitch.reset()

    async def _force_corrections_zero(self) -> None:
        """Best-effort: snap corrections to zero and push a zero frame.

        Called when the loop body fails persistently and the normal
        ramp-to-zero path can no longer be trusted to run. Any failure here is
        swallowed (logged) so the loop stays alive.
        """
        self._corrections = [0.0] * self._n
        try:
            await self._control.apply_stabilization_corrections([0.0] * self._n)
        except Exception:
            logger.exception("Failed to force stabilization corrections to zero")

    # -- control loop -------------------------------------------------------

    async def _run(self) -> None:
        period = 1.0 / self.settings.stabilization_rate_hz
        while True:
            try:
                await asyncio.sleep(period)
                await self._step()
                self._consecutive_step_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                # A crashing iteration must NOT kill the task: that would latch
                # the last corrections on hardware with no ramp-down while a
                # POST {enabled:false} returns 200 doing nothing. Log it,
                # fail closed by auto-disabling (so the ramp-to-zero branch runs
                # on subsequent iterations), and keep looping.
                self._consecutive_step_failures += 1
                logger.exception(
                    "Stabilization loop iteration failed (%d consecutive)",
                    self._consecutive_step_failures,
                )
                self._trigger_auto_disable("internal error")
                if self._consecutive_step_failures >= MAX_CONSECUTIVE_STEP_FAILURES:
                    # The step itself keeps failing (e.g. attitude provider or
                    # serial send is persistently broken). Force corrections to
                    # zero directly so nothing stays latched, then idle.
                    await self._force_corrections_zero()

    async def _step(self) -> None:
        now = self._time_fn()
        if self._last_step_t is None:
            dt = 1.0 / self.settings.stabilization_rate_hz
        else:
            dt = now - self._last_step_t
        self._last_step_t = now
        if dt <= 0.0:
            dt = 1.0 / self.settings.stabilization_rate_hz
        self._loop_rate_hz = 1.0 / dt

        snapshot = self._attitude_provider()

        # Staleness telemetry is evaluated every tick regardless of the
        # enabled/auto-disabled gate so the UI always reflects sensor liveness.
        self._attitude_stale = self._is_attitude_stale(snapshot, now)

        # Auto-disable health checks only run while we are actively controlling.
        if self._enabled and not self._auto_disabled:
            reason = self._health_reason(snapshot, now)
            if reason is not None:
                self._trigger_auto_disable(reason)

        active = self._enabled and not self._auto_disabled

        if active and snapshot is not None:
            roll = snapshot.euler.roll_deg
            pitch = snapshot.euler.pitch_deg
            offset_roll, offset_pitch = self._level_offsets_provider()
            roll -= offset_roll
            pitch -= offset_pitch
            self._roll_deg = roll
            self._pitch_deg = pitch
            self._roll_error_deg = 0.0 - roll
            self._pitch_error_deg = 0.0 - pitch

            # gyro_rate mode feeds the D-term straight from the bias-corrected
            # gyro (roll rate = -gyro_x, pitch rate = -gyro_y — error = -attitude,
            # so d(error)/dt = -d(attitude)/dt = -gyro; see module docstring for
            # the body-frame sign convention) instead of finite-differencing the
            # fused angle, which double-differentiates noise. Any non-finite
            # gyro sample (e.g. the sensor-nan scenario) falls back to None
            # (finite-difference) for that tick only — never let a NaN rate
            # reach the PID output.
            roll_rate: float | None = None
            pitch_rate: float | None = None
            if self._derivative_source == "gyro_rate":
                gyro = snapshot.gyro_dps
                if isfinite(gyro.x) and isfinite(gyro.y):
                    roll_rate = -gyro.x
                    pitch_rate = -gyro.y

            u_roll = self._pid_roll.step(self._roll_error_deg, dt, rate=roll_rate)
            u_pitch = self._pid_pitch.step(self._pitch_error_deg, dt, rate=pitch_rate)
            desired = [
                _clamp(
                    self._mixing[i][0] * u_roll + self._mixing[i][1] * u_pitch,
                    self._max_correction,
                )
                for i in range(self._n)
            ]
            max_delta = self.settings.stabilization_max_correction_rate * dt
        else:
            # Disabled or auto-disabled: ramp every correction smoothly to zero
            # over ~disable_ramp_sec rather than snapping to 0 (avoids a jolt).
            self._roll_error_deg = 0.0
            self._pitch_error_deg = 0.0
            desired = [0.0] * self._n
            max_delta = (self._max_correction / self.settings.stabilization_disable_ramp_sec) * dt

        self._corrections = [
            self._corrections[i] + _clamp(desired[i] - self._corrections[i], max_delta)
            for i in range(self._n)
        ]

        # Byte-identical to base behaviour when disabled AND fully ramped down:
        # emit no serial traffic at all in that steady state.
        should_send = active or any(abs(c) > 1e-6 for c in self._corrections)
        if should_send:
            try:
                await self._control.apply_stabilization_corrections(list(self._corrections))
                self._serial_failures = 0
            except Exception:
                self._serial_failures += 1
                logger.warning(
                    "Stabilization serial send failed (%d/%d)",
                    self._serial_failures,
                    self.settings.stabilization_serial_failure_limit,
                )
                if self._serial_failures >= self.settings.stabilization_serial_failure_limit:
                    self._trigger_auto_disable("serial send failures")

        await self._publish()

    def _is_attitude_stale(self, snapshot: AttitudeState | None, now: float) -> bool:
        if snapshot is None:
            return True
        return (now - snapshot.timestamp) > self.settings.stabilization_max_staleness_sec

    def _health_reason(self, snapshot: AttitudeState | None, now: float) -> str | None:
        if snapshot is None:
            return "attitude unavailable"
        if now - snapshot.timestamp > self.settings.stabilization_max_staleness_sec:
            return "attitude stale"
        offset_roll, offset_pitch = self._level_offsets_provider()
        roll = snapshot.euler.roll_deg - offset_roll
        pitch = snapshot.euler.pitch_deg - offset_pitch
        # ``abs(NaN) > limit`` is False, so a NaN attitude would silently defeat
        # the tilt cutout. Reject non-finite angles explicitly (fail closed).
        if not isfinite(roll) or not isfinite(pitch):
            return "attitude invalid"
        max_tilt = self.settings.stabilization_max_tilt_deg
        if abs(roll) > max_tilt or abs(pitch) > max_tilt:
            return "tilt exceeded"
        return None

    # -- WebSocket publishing ----------------------------------------------

    async def _publish(self, *, force: bool = False) -> None:
        signature = (self._enabled, self._auto_disabled, self._disabled_reason)
        now = self._time_fn()
        transition = signature != self._last_signature
        # ~8 Hz steady cadence while enabled; immediate on any state transition.
        due = (now - self._last_publish_t) >= 0.12
        if not (force or transition or (self._enabled and due)):
            return
        self._last_signature = signature
        self._last_publish_t = now
        await self._event_sink(
            TelemetryEvent(
                type="stabilization_state",
                payload={"stabilization": self.get_state().model_dump(mode="json")},
            )
        )
