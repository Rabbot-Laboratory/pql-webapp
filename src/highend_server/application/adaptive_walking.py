"""Hardware-facing adaptive trot control with a short-lived browser lease.

The numerical control law is intentionally isolated in
``compute_adaptive_walking_targets``.  It has no asyncio, serial, FastAPI, or
sensor-driver dependency, so recorded real-hardware samples can be replayed in
unit tests and the exact same function can later be tuned offline.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from math import floor, isfinite
from time import monotonic

from highend_server.application.attitude_control_frame import AttitudeControlFrame
from highend_server.application.control_service import ControlService
from highend_server.application.experiment import (
    ExperimentAlreadyRunningError,
    ExperimentNotRunningError,
    ExperimentRecorder,
)
from highend_server.application.ilc import IlcState, make_ilc_state, sample_ilc, update_ilc
from highend_server.application.pql_a00_kinematics import (
    ACTUATOR_HEIGHT_EFFECTS,
    model_derived_mixing_matrix,
)
from highend_server.config import Settings
from highend_server.domain.models import (
    POSITION_MAX,
    POSITION_MIN,
    AdaptiveWalkMode,
    AdaptiveWalkState,
    ConnectionState,
    ContactLegState,
    ExperimentStartRequest,
    LegId,
    MotionCategory,
    TelemetryEvent,
)
from highend_server.sensors.sensor_service import AttitudeState

logger = logging.getLogger(__name__)
EventSink = Callable[[TelemetryEvent], Awaitable[None]]
AttitudeProvider = Callable[[], AttitudeState | None]
LevelOffsetsProvider = Callable[[], tuple[float, float]]
ContactProvider = Callable[[], list[ContactLegState]]

_REAR_LEGS = frozenset((LegId.REAR_RIGHT, LegId.REAR_LEFT))


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
    # Axes whose previous output was saturated or rate-limited: their phase
    # lead must not keep integrating (anti-windup) while the command cannot
    # physically be realized.
    blocked: tuple[bool, ...] = ()


@dataclass(frozen=True, slots=True)
class AdaptiveWalkingResult:
    targets: tuple[int, ...]
    memory: AdaptiveWalkingMemory
    phase_offsets: tuple[float, ...]
    attitude_offsets: tuple[float, ...]
    rate_limited: tuple[bool, ...] = ()
    saturated: tuple[bool, ...] = ()


@dataclass(frozen=True, slots=True)
class AdaptiveWalkDebug:
    """Per-tick internals surfaced for the experiment recorder."""

    active: bool
    phase: float
    cycle_count: int
    motion_scale: float
    phase_offsets: tuple[float, ...]
    attitude_offsets: tuple[float, ...]
    phase_leads_s: tuple[float, ...]
    rate_limited: tuple[bool, ...]
    saturated: tuple[bool, ...]
    ilc_corrections: tuple[float, ...]


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
    stance_mask: Sequence[bool] | None = None,
) -> AdaptiveWalkingResult:
    """Return one bounded adaptive target update for all walking actuators.

    ``roll_error``/``pitch_error`` use the same control frame as the existing
    stabilizer.  Persistent errors update bounded gait trims; periodic tracking
    error learns a separate phase lead for each pneumatic actuator.

    When ``stance_mask`` is provided, attitude corrections are applied only to
    axes whose leg is in contact: pushing against ground can correct posture,
    while corrections on swing legs merely distort the gait (and fight the
    intentional pitch excursion of a kick).
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
    if stance_mask is not None and len(stance_mask) != count:
        raise ValueError("stance_mask must match the actuator count")

    phase_leads: list[float] = []
    phase_offsets: list[float] = []
    for index in range(count):
        velocity = float(nominal_velocities[index])
        error = float(nominal_targets[index]) - float(actual_positions[index])
        if index < len(memory.blocked) and memory.blocked[index]:
            # Anti-windup: while the output is pinned at a mechanical end or
            # by the rate limit, tracking error says nothing about lag.
            lead = memory.phase_leads_s[index]
        else:
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
        * (1.0 if stance_mask is None or stance_mask[index] else 0.0)
        for index in range(count)
    )

    previous = (
        memory.last_targets
        if len(memory.last_targets) == count
        else tuple(float(value) for value in actual_positions)
    )
    max_delta = config.max_target_rate * dt
    targets: list[int] = []
    limited_targets: list[float] = []
    rate_limited_flags: list[bool] = []
    saturated_flags: list[bool] = []
    for index in range(count):
        desired = (
            float(nominal_targets[index])
            + phase_offsets[index]
            + attitude_offsets[index]
        )
        limited = previous[index] + _clamp(desired - previous[index], max_delta)
        rate_limited_flags.append(abs(desired - previous[index]) > max_delta + 1e-9)
        limited = max(POSITION_MIN, min(POSITION_MAX, limited))
        saturated_flags.append(limited <= POSITION_MIN or limited >= POSITION_MAX)
        limited_targets.append(limited)
        targets.append(int(round(limited)))

    next_memory = AdaptiveWalkingMemory(
        phase_leads_s=tuple(phase_leads),
        roll_trim=roll_trim,
        pitch_trim=pitch_trim,
        last_targets=tuple(limited_targets),
        blocked=tuple(
            rate_limited_flags[index] or saturated_flags[index] for index in range(count)
        ),
    )
    return AdaptiveWalkingResult(
        targets=tuple(targets),
        memory=next_memory,
        phase_offsets=tuple(phase_offsets),
        attitude_offsets=attitude_offsets,
        rate_limited=tuple(rate_limited_flags),
        saturated=tuple(saturated_flags),
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
        contact_provider: ContactProvider | None = None,
        experiment_recorder: ExperimentRecorder | None = None,
        time_fn: Callable[[], float] = monotonic,
    ) -> None:
        self.settings = settings
        self._control = control_service
        self._attitude_provider = attitude_provider
        self._level_offsets_provider = level_offsets_provider
        self._event_sink = event_sink
        self._stabilization_engaged = stabilization_engaged
        self._contact_provider = contact_provider
        self._recorder = experiment_recorder
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
        # Replay mode zeroes every adaptive term; the rate limit and range
        # clamps stay as the final hardware guard.
        self._replay_config = replace(
            self._config,
            learning_rate=0.0,
            feedback_gain=0.0,
            attitude_kp=0.0,
            attitude_kd=0.0,
            trim_adaptation_rate=0.0,
        )
        self._rear_axes = frozenset(
            effect.actuator_id
            for effect in ACTUATOR_HEIGHT_EFFECTS
            if effect.leg_id in _REAR_LEGS
        )
        self._leg_by_axis = tuple(effect.leg_id for effect in ACTUATOR_HEIGHT_EFFECTS)
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
        self._mode = AdaptiveWalkMode.ADAPTIVE
        self._target_cycles: int | None = None
        self._cycle_count = 0
        self._cycle_counting = False
        # Gait progression in cycle units (fractional part = phase). Advanced
        # by measured dt each step; frozen while waiting at the contact gate.
        self._gait_cycles = 0.0
        self._gate_waiting = False
        self._gate_wait_started = 0.0
        self._auto_experiment = False
        self._ilc: IlcState = make_ilc_state(0, settings.actuator_count)
        self._ilc_error_sums: list[list[float]] = []
        self._ilc_error_counts: list[int] = []
        self._last_ilc_corrections: tuple[float, ...] = (0.0,) * settings.actuator_count
        self._last_result: AdaptiveWalkingResult | None = None

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
        self,
        pressed: bool,
        *,
        safety_confirmed: bool,
        cycles: int | None = None,
        mode: AdaptiveWalkMode = AdaptiveWalkMode.ADAPTIVE,
    ) -> AdaptiveWalkState:
        async with self._request_lock:
            return await self._set_forward_pressed_locked(
                pressed,
                safety_confirmed=safety_confirmed,
                cycles=cycles,
                mode=mode,
            )

    async def _set_forward_pressed_locked(
        self,
        pressed: bool,
        *,
        safety_confirmed: bool,
        cycles: int | None = None,
        mode: AdaptiveWalkMode = AdaptiveWalkMode.ADAPTIVE,
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
        # Replay is a pure baseline: even the initial phase lead must be zero,
        # otherwise lead*velocity would still offset the commanded waveform.
        initial_lead = (
            self.settings.adaptive_walk_initial_phase_lead_s
            if mode is AdaptiveWalkMode.ADAPTIVE
            else 0.0
        )
        self._memory = AdaptiveWalkingMemory(
            phase_leads_s=(initial_lead,) * self.settings.actuator_count,
            last_targets=actual,
        )
        self._active = True
        self._auto_stopped = False
        self._stopped_reason = None
        self._lease_deadline = now + self.settings.adaptive_walk_lease_timeout_sec
        self._walk_started_at = now
        self._last_step_at = None
        self._current_motion_scale = 0.0
        self._mode = mode
        self._target_cycles = cycles
        self._cycle_count = 0
        self._cycle_counting = False
        self._gait_cycles = 0.0
        self._phase = 0.0
        self._gate_waiting = False
        self._ilc = make_ilc_state(len(rows), self.settings.actuator_count)
        self._reset_ilc_accumulators()
        self._last_ilc_corrections = (0.0,) * self.settings.actuator_count
        self._last_result = None
        await self._maybe_start_auto_experiment()
        await self._publish(force=True)
        return self.get_state()

    async def _maybe_start_auto_experiment(self) -> None:
        """Auto-record cycle-bounded runs so no counted walk goes unmeasured."""
        self._auto_experiment = False
        if self._target_cycles is None or self._recorder is None:
            return
        request = ExperimentStartRequest(
            experiment_type=f"walk-{self._mode.value}-{self._target_cycles}cyc"
        )
        try:
            await self._recorder.start(request)
        except ExperimentAlreadyRunningError:
            return  # a manual recording is running; it captures this walk too
        except Exception:
            logger.exception("auto experiment start failed; walking continues unrecorded")
            return
        self._auto_experiment = True

    async def release(self, reason: str = "button released", *, automatic: bool = False) -> None:
        was_active = self._active
        self._active = False
        self._current_motion_scale = 0.0
        self._gate_waiting = False
        # A late pointer-up may arrive after an automatic stop. Preserve the
        # actionable safety reason instead of overwriting it with "released".
        if was_active or automatic:
            self._auto_stopped = automatic
            self._stopped_reason = reason
        await self._control.release_adaptive_walking()
        if self._auto_experiment and self._recorder is not None:
            self._auto_experiment = False
            with contextlib.suppress(ExperimentNotRunningError):
                await self._recorder.stop()
        if was_active or automatic:
            await self._publish(force=True)

    def get_state(self) -> AdaptiveWalkState:
        now = self._time_fn()
        result = self._last_result
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
            mode=self._mode,
            cycle_count=self._cycle_count,
            target_cycles=self._target_cycles,
            gate_waiting=self._gate_waiting,
            saturated_axes=list(result.saturated) if result is not None else [],
            rate_limited_axes=list(result.rate_limited) if result is not None else [],
        )

    def latest_debug(self) -> AdaptiveWalkDebug:
        """Per-tick internals for the experiment recorder (25 Hz CSV columns)."""
        result = self._last_result
        count = self.settings.actuator_count
        empty = (0.0,) * count
        return AdaptiveWalkDebug(
            active=self._active,
            phase=self._phase,
            cycle_count=self._cycle_count,
            motion_scale=self._current_motion_scale,
            phase_offsets=result.phase_offsets if result is not None else empty,
            attitude_offsets=result.attitude_offsets if result is not None else empty,
            phase_leads_s=self._memory.phase_leads_s,
            rate_limited=result.rate_limited if result is not None else (False,) * count,
            saturated=result.saturated if result is not None else (False,) * count,
            ilc_corrections=self._last_ilc_corrections,
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
        elapsed_wall = now - self._walk_started_at
        count = self.settings.actuator_count
        adaptive = self._mode is AdaptiveWalkMode.ADAPTIVE

        contact_states = (
            self._contact_provider() if self._contact_provider is not None else []
        )
        # Missing ADC data (bank error / sensors disabled) must never silently
        # disable attitude corrections or wedge the gate: fall back to the
        # contact-free behaviour instead.
        contact_valid = any(state.raw is not None for state in contact_states)
        use_contact = adaptive and self.settings.adaptive_walk_use_contact and contact_valid

        # -- gait-time progression (event-gated, wall-clock independent) -----
        cycle_s = self._interval_s * len(self._rows)
        previous_cycles = self._gait_cycles
        if self._gate_waiting:
            if self._rear_supporting(contact_states):
                self._gate_waiting = False
                await self._emit_gate_event("resumed")
            elif (
                now - self._gate_wait_started
                >= self.settings.adaptive_walk_gate_timeout_sec
            ):
                self._gate_waiting = False
                await self._emit_gate_event("timeout")
        if not self._gate_waiting:
            advanced = previous_cycles + dt / cycle_s
            gate_phase = self.settings.adaptive_walk_kick_gate_phase
            if (
                use_contact
                and gate_phase is not None
                and floor(advanced - gate_phase) > floor(previous_cycles - gate_phase)
                and not self._rear_supporting(contact_states)
            ):
                # Hold the trajectory exactly at the kick-start phase until
                # both rear legs are loaded (or the timeout fires).
                advanced = floor(previous_cycles - gate_phase) + 1 + gate_phase
                self._gate_waiting = True
                self._gate_wait_started = now
                await self._emit_gate_event("waiting")
            self._gait_cycles = advanced
        self._phase = self._gait_cycles % 1.0

        frame_position = self._phase * len(self._rows)
        nominal, velocity = self._trajectory_sample(frame_position, elapsed_wall)
        if self._gate_waiting:
            velocity = (0.0,) * count

        # -- full-amplitude cycle bookkeeping ---------------------------------
        wrapped = floor(self._gait_cycles) > floor(previous_cycles)
        ilc_enabled = adaptive and self.settings.adaptive_walk_ilc_gain > 0.0
        if wrapped:
            if self._cycle_counting:
                self._cycle_count += 1
                if ilc_enabled:
                    await self._fold_ilc_cycle()
                if (
                    self._target_cycles is not None
                    and self._cycle_count >= self._target_cycles
                ):
                    await self.release("cycle target reached", automatic=True)
                    return
            elif (
                self._current_motion_scale
                >= self.settings.adaptive_walk_motion_scale - 1e-6
            ):
                self._cycle_counting = True
                self._reset_ilc_accumulators()

        actual = tuple(float(item.telemetry.position) for item in self._control.list_actuators())

        # -- feed-forward layers on top of the nominal trajectory -------------
        ilc_active = ilc_enabled and self._cycle_counting
        ilc_corrections = (
            sample_ilc(self._ilc, frame_position) if ilc_active else (0.0,) * count
        )
        self._last_ilc_corrections = ilc_corrections
        if ilc_active:
            frame_index = int(frame_position) % len(self._rows)
            self._ilc_error_counts[frame_index] += 1
            sums = self._ilc_error_sums[frame_index]
            for index in range(count):
                sums[index] += nominal[index] - actual[index]

        kick_factor = self._kick_thrust_factor(-pitch) if adaptive else 1.0
        neutral = (POSITION_MIN + POSITION_MAX) / 2.0
        commanded = list(nominal)
        for index in range(count):
            if kick_factor != 1.0 and index in self._rear_axes:
                commanded[index] = neutral + (commanded[index] - neutral) * kick_factor
            commanded[index] += ilc_corrections[index]

        roll_rate, pitch_rate = self._attitude_frame.error_rates(
            gyro_x_dps=snapshot.gyro_dps.x,
            gyro_y_dps=snapshot.gyro_dps.y,
        )
        result = compute_adaptive_walking_targets(
            nominal_targets=commanded,
            nominal_velocities=velocity,
            actual_positions=actual,
            roll_error_deg=-roll,
            pitch_error_deg=-pitch,
            roll_error_rate_dps=roll_rate,
            pitch_error_rate_dps=pitch_rate,
            dt=dt,
            memory=self._memory,
            config=self._config if adaptive else self._replay_config,
            mixing_matrix=self._mixing,
            stance_mask=self._stance_mask(contact_states) if use_contact else None,
        )
        self._memory = result.memory
        self._last_result = result
        await self._control.apply_adaptive_walking_targets(list(result.targets))
        await self._publish()

    def _trajectory_sample(
        self, frame_position: float, wall_elapsed: float
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """Sample the looped motion rows at a fractional frame index.

        ``frame_position`` follows the event-gated gait time; the amplitude
        ramp intentionally follows wall time so a gate hold never re-runs it.
        """
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
            wall_elapsed,
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
        return nominal, velocity

    # -- contact / gate / ILC helpers ----------------------------------------

    def _rear_supporting(self, contact_states: list[ContactLegState]) -> bool:
        by_leg = {state.leg: state.supporting for state in contact_states}
        return all(by_leg.get(leg, False) for leg in _REAR_LEGS)

    def _stance_mask(self, contact_states: list[ContactLegState]) -> tuple[bool, ...]:
        by_leg = {state.leg: state.supporting for state in contact_states}
        return tuple(by_leg.get(leg, False) for leg in self._leg_by_axis)

    def _kick_thrust_factor(self, pitch_error_deg: float) -> float:
        """Raibert-style pitch-proportional rear-kick scaling (1.0 = inert).

        The sign convention matches ``pitch_error_deg`` as fed to the control
        law; gain and sign must be tuned on hardware before trusting it.
        """
        gain = self.settings.adaptive_walk_pitch_thrust_gain
        end_phase = self.settings.adaptive_walk_kick_end_phase
        if gain == 0.0 or end_phase is None:
            return 1.0
        start_phase = self.settings.adaptive_walk_kick_gate_phase or 0.0
        phase = self._phase
        in_window = (
            start_phase <= phase < end_phase
            if start_phase <= end_phase
            else phase >= start_phase or phase < end_phase
        )
        if not in_window:
            return 1.0
        return max(0.7, min(1.3, 1.0 + gain * pitch_error_deg))

    def _reset_ilc_accumulators(self) -> None:
        frames = len(self._rows)
        count = self.settings.actuator_count
        self._ilc_error_sums = [[0.0] * count for _ in range(frames)]
        self._ilc_error_counts = [0] * frames

    async def _fold_ilc_cycle(self) -> None:
        frames = len(self._rows)
        cycle_errors: list[list[float | None]] = [
            [
                (self._ilc_error_sums[frame][axis] / self._ilc_error_counts[frame])
                if self._ilc_error_counts[frame]
                else None
                for axis in range(self.settings.actuator_count)
            ]
            for frame in range(frames)
        ]
        outcome = update_ilc(
            self._ilc,
            cycle_errors,
            gain=self.settings.adaptive_walk_ilc_gain,
            max_correction=self.settings.adaptive_walk_ilc_max,
        )
        self._ilc = outcome.state
        self._reset_ilc_accumulators()
        await self._event_sink(
            TelemetryEvent(
                type="adaptive_walk_ilc",
                payload={
                    "cycle": self._cycle_count,
                    "accepted": outcome.accepted,
                    "cycle_rms": outcome.cycle_rms,
                },
            )
        )

    async def _emit_gate_event(self, status: str) -> None:
        await self._event_sink(
            TelemetryEvent(
                type="adaptive_walk_gate",
                payload={
                    "status": status,
                    "phase": self._gait_cycles % 1.0,
                    "cycle": self._cycle_count,
                },
            )
        )

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
