from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite, pi, sin, sqrt
from pathlib import Path
from time import monotonic
from typing import Protocol

from highend_server.config import Settings
from highend_server.domain.models import (
    AdcBankState,
    AdcChannelState,
    Bmx055State,
    ImuCalibration,
    ImuOrientation,
    ImuQuaternion,
    ImuVector,
    MagCalibrationQuality,
    SensorConnectionState,
    SensorState,
    TelemetryEvent,
)
from highend_server.sensors.adc_mcp3204 import Mcp3204Reader
from highend_server.sensors.attitude import (
    DEG_TO_RAD,
    RAD_TO_DEG,
    EulerAngles,
    MahonyMARG,
    Quaternion,
    add3,
    euler_to_quat,
    gravity_from_quat,
    quat_conjugate,
    quat_multiply,
    rotate_vector_by_quat_inverse,
)
from highend_server.sensors.imu_bmx055 import Bmx055Reader, Bmx055Reading, Vector3
from highend_server.sensors.imu_scenarios import ScenarioFn, get_scenario
from highend_server.sensors.mag_calibration import apply_calibration as apply_mag_calibration
from highend_server.sensors.mag_calibration import fit as fit_mag_calibration
from highend_server.sensors.replay_source import ReplayImuSource

EventSink = Callable[[TelemetryEvent], Awaitable[None]]
logger = logging.getLogger(__name__)

_ZERO = Vector3(0.0, 0.0, 0.0)
_UNIT_SCALE = Vector3(1.0, 1.0, 1.0)


def _model_vector(vector: Vector3) -> ImuVector:
    return ImuVector(x=vector.x, y=vector.y, z=vector.z)


def _model_quaternion(quaternion: Quaternion) -> ImuQuaternion:
    return ImuQuaternion(w=quaternion.w, x=quaternion.x, y=quaternion.y, z=quaternion.z)


def _cal_vector(vector: ImuVector) -> Vector3:
    return Vector3(x=vector.x, y=vector.y, z=vector.z)


def _orientation_from_euler(
    euler: EulerAngles,
    calibration: ImuCalibration | None = None,
) -> ImuOrientation:
    roll_deg = euler.roll_deg
    pitch_deg = euler.pitch_deg
    if calibration is not None:
        roll_deg -= calibration.level_roll_deg
        pitch_deg -= calibration.level_pitch_deg
    return ImuOrientation(roll_deg=roll_deg, pitch_deg=pitch_deg, yaw_deg=euler.yaw_deg)


# ---------------------------------------------------------------------------
# Shared attitude snapshot (written by the IMU thread, read from asyncio)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttitudeState:
    """Immutable snapshot of the latest fused attitude produced by the IMU thread.

    Frozen so it can be handed to asyncio consumers (and, in Phase 2, the
    stabilization controller) without copying or extra locking: the shared
    holder swaps the whole object under a lock, readers get a stable reference.
    """

    quaternion: Quaternion
    euler: EulerAngles
    gravity_g: Vector3
    linear_accel_g: Vector3
    accel_g: Vector3
    gyro_dps: Vector3  # bias-corrected
    raw_gyro_dps: Vector3  # pre-bias (used by gyro-zero calibration)
    mag: Vector3  # calibrated (hard/soft-iron corrected)
    mag_valid: bool
    temperature_c: float | None
    timestamp: float  # monotonic seconds
    sample_count: int


class _SharedAttitude:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: AttitudeState | None = None

    def set(self, state: AttitudeState) -> None:
        with self._lock:
            self._state = state

    def snapshot(self) -> AttitudeState | None:
        with self._lock:
            return self._state


# ---------------------------------------------------------------------------
# IMU sources (real vs emulated share the same pipeline / fusion code)
# ---------------------------------------------------------------------------


class ImuSource(Protocol):
    def open(self) -> None: ...

    def read(self) -> Bmx055Reading: ...

    def close(self) -> None: ...


class RealImuSource:
    """Wraps :class:`Bmx055Reader` for the real I2C device."""

    def __init__(self, settings: Settings) -> None:
        self._reader = Bmx055Reader(
            bus=settings.sensor_i2c_bus,
            accel_address=settings.bmx055_accel_address,
            gyro_address=settings.bmx055_gyro_address,
            mag_address=settings.bmx055_mag_address,
        )

    def open(self) -> None:
        self._reader.open()

    def read(self) -> Bmx055Reading:
        return self._reader.read()

    def close(self) -> None:
        self._reader.close()


def _angular_velocity_dps(q_prev: Quaternion, q_cur: Quaternion, dt: float) -> Vector3:
    """Body angular rate (deg/s) from two orientations via finite difference."""
    dq = quat_multiply(quat_conjugate(q_prev), q_cur)
    if dq.w < 0.0:  # take the shortest rotation
        dq = Quaternion(-dq.w, -dq.x, -dq.y, -dq.z)
    scale = (2.0 / dt) * RAD_TO_DEG
    return Vector3(dq.x * scale, dq.y * scale, dq.z * scale)


class EmulatedImuSource:
    """Synthetic IMU generating smooth, self-consistent 9-axis motion.

    Accel is the specific force opposing gravity for the current attitude, the
    magnetometer is a fixed world field rotated into the body frame, and the gyro
    is derived by finite-differencing the attitude so it stays consistent with
    the accel/mag references (the Mahony filter then converges cleanly).

    ``scenario`` selects the attitude/fault-injection profile (see
    ``sensors/imu_scenarios.py``); it defaults to ``"smooth"``, the legacy
    waveform. Two fault flags on the scenario sample are handled here rather
    than in the pipeline:

    * ``inject_nan``: the accel/gyro components emitted this cycle are
      replaced with NaN. ``MahonyMARG.update()`` fails closed on non-finite
      input (holds the previous, finite quaternion/euler) so the fused
      attitude never gets corrupted, while the raw accel/gyro pass-through
      still shows NaN — an honest reflection of the (simulated) sensor fault.
    * ``hold_stale``: rather than returning a frozen-but-fresh reading (which
      would NOT trip staleness — ``ImuPipeline`` stamps ``AttitudeState.
      timestamp`` with ``now()`` every cycle regardless of whether the values
      changed), this raises so ``ImuPipeline._run``'s existing per-cycle
      exception handler runs instead: it logs the error, keeps the thread
      alive, but never calls ``self._shared.set(...)`` again. The last
      published snapshot's ``timestamp`` then genuinely stops advancing,
      which is what the stabilization staleness/health checks actually key
      on. Raising (vs. blocking the thread forever) also keeps `stop()`'s
      `join()` responsive.
    """

    def __init__(
        self,
        *,
        time_fn: Callable[[], float] = monotonic,
        scenario: str | ScenarioFn = "smooth",
    ) -> None:
        self._time_fn = time_fn
        self._start = time_fn()
        self._prev_q: Quaternion | None = None
        self._prev_t: float | None = None
        self._scenario_fn: ScenarioFn = scenario if callable(scenario) else get_scenario(scenario)

    def open(self) -> None:
        self._start = self._time_fn()
        self._prev_q = None
        self._prev_t = None

    def close(self) -> None:
        pass

    def read(self) -> Bmx055Reading:
        t = self._time_fn()
        elapsed = t - self._start
        sample = self._scenario_fn(elapsed)

        if sample.hold_stale:
            raise RuntimeError("emulated IMU: sensor read stalled (scenario hold_stale)")

        q = euler_to_quat(sample.roll_deg, sample.pitch_deg, sample.yaw_deg)

        gravity = gravity_from_quat(q)
        accel = Vector3(
            x=gravity.x + 0.015 * sin(elapsed * 4.2) + sample.accel_extra_g.x,
            y=gravity.y + 0.012 * sin(elapsed * 3.1) + sample.accel_extra_g.y,
            z=gravity.z + 0.01 * sin(elapsed * 2.3) + sample.accel_extra_g.z,
        )

        world_mag = Vector3(22.0, 0.0, -40.0)
        mag = rotate_vector_by_quat_inverse(q, world_mag)

        if self._prev_q is not None and self._prev_t is not None:
            dt = max(1e-3, t - self._prev_t)
            gyro = _angular_velocity_dps(self._prev_q, q, dt)
        else:
            gyro = Vector3(0.0, 0.0, 0.0)
        gyro = Vector3(
            x=gyro.x + sample.gyro_bias_dps.x,
            y=gyro.y + sample.gyro_bias_dps.y,
            z=gyro.z + sample.gyro_bias_dps.z,
        )
        self._prev_q = q
        self._prev_t = t

        if sample.inject_nan:
            nan = float("nan")
            accel = Vector3(nan, nan, nan)
            gyro = Vector3(nan, nan, nan)

        return Bmx055Reading(
            accel_g=accel,
            gyro_dps=gyro,
            mag_raw=mag,
            temperature_c=31.0 + 1.5 * sin(elapsed * 0.12),
        )


# ---------------------------------------------------------------------------
# Dedicated IMU thread (~100Hz)
# ---------------------------------------------------------------------------


class ImuPipeline:
    """Runs IMU read -> calibration -> Mahony fusion on a dedicated thread.

    Accel + gyro are sampled every cycle (~``sample_rate_hz``); the magnetometer
    is sampled at ``mag_rate_hz`` via decimation. Cycles with a fresh, valid mag
    sample run the 9-axis Mahony update; other cycles run the 6-axis update.
    """

    def __init__(
        self,
        *,
        source: ImuSource,
        sample_rate_hz: float,
        mag_rate_hz: float,
        kp: float,
        ki: float,
        calibration: ImuCalibration,
        max_mag_samples: int,
        time_fn: Callable[[], float] = monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._source = source
        self._sample_period = 1.0 / sample_rate_hz
        self._mag_decimation = max(1, round(sample_rate_hz / max(mag_rate_hz, 1e-6)))
        self._filter = MahonyMARG(kp=kp, ki=ki)
        self._shared = _SharedAttitude()
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn
        self._max_mag_samples = max_mag_samples

        self._stop = threading.Event()
        self._reset_requested = threading.Event()
        self._thread = threading.Thread(target=self._run, name="imu-pipeline", daemon=True)

        self._cal_lock = threading.Lock()
        self._gyro_offset = _cal_vector(calibration.gyro_offset_dps)
        self._mag_offset = _cal_vector(calibration.mag_offset)
        self._mag_scale = _cal_vector(calibration.mag_scale)

        self._mag_lock = threading.Lock()
        self._collecting = False
        self._mag_samples: list[Vector3] = []

        self._sample_count = 0
        self._last_mag = _ZERO
        self._error: str | None = None
        self._consecutive_failures = 0

    @property
    def shared(self) -> _SharedAttitude:
        return self._shared

    @property
    def error(self) -> str | None:
        return self._error

    def start(self) -> None:
        self._stop.clear()
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)
        if self._thread.is_alive():
            logger.error(
                "IMU pipeline thread did not stop within %.1fs; the device fd will "
                "be leaked rather than closed under a live read.",
                timeout,
            )

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def apply_calibration(self, calibration: ImuCalibration) -> None:
        with self._cal_lock:
            self._gyro_offset = _cal_vector(calibration.gyro_offset_dps)
            self._mag_offset = _cal_vector(calibration.mag_offset)
            self._mag_scale = _cal_vector(calibration.mag_scale)

    def request_filter_reset(self) -> None:
        self._reset_requested.set()

    # -- magnetometer calibration collection (thread-safe) ------------------

    def start_mag_collection(self) -> None:
        with self._mag_lock:
            self._mag_samples = []
            self._collecting = True

    def stop_mag_collection(self) -> list[Vector3]:
        with self._mag_lock:
            self._collecting = False
            return list(self._mag_samples)

    def resume_mag_collection(self) -> None:
        """Re-arm collection after a failed fit, preserving the sample buffer."""
        with self._mag_lock:
            self._collecting = True

    def cancel_mag_collection(self) -> None:
        with self._mag_lock:
            self._collecting = False
            self._mag_samples = []

    @property
    def mag_collection_active(self) -> bool:
        with self._mag_lock:
            return self._collecting

    @property
    def mag_sample_count(self) -> int:
        with self._mag_lock:
            return len(self._mag_samples)

    # -- thread body --------------------------------------------------------

    def _run(self) -> None:
        last = self._time_fn()
        cycle = 0
        while not self._stop.is_set():
            loop_start = self._time_fn()
            try:
                if self._reset_requested.is_set():
                    self._filter.reset()
                    self._reset_requested.clear()

                reading = self._source.read()

                with self._cal_lock:
                    gyro_offset = self._gyro_offset
                    mag_offset = self._mag_offset
                    mag_scale = self._mag_scale

                raw_gyro = reading.gyro_dps
                corrected_gyro = Vector3(
                    raw_gyro.x - gyro_offset.x,
                    raw_gyro.y - gyro_offset.y,
                    raw_gyro.z - gyro_offset.z,
                )

                use_mag = (cycle % self._mag_decimation) == 0
                cal_mag: Vector3 | None = None
                mag_valid = False
                if use_mag:
                    candidate = apply_mag_calibration(reading.mag_raw, mag_offset, mag_scale)
                    norm = sqrt(candidate.x ** 2 + candidate.y ** 2 + candidate.z ** 2)
                    if norm > 1e-6 and isfinite(norm):
                        cal_mag = candidate
                        mag_valid = True
                        self._last_mag = candidate
                    self._maybe_collect(reading.mag_raw)

                now = self._time_fn()
                dt = max(1e-3, min(0.2, now - last))
                last = now

                gyro_rad = Vector3(
                    corrected_gyro.x * DEG_TO_RAD,
                    corrected_gyro.y * DEG_TO_RAD,
                    corrected_gyro.z * DEG_TO_RAD,
                )
                attitude = self._filter.update(
                    gyro_rad=gyro_rad,
                    accel_g=reading.accel_g,
                    mag_raw=cal_mag,
                    dt=dt,
                )
                self._sample_count += 1

                self._shared.set(
                    AttitudeState(
                        quaternion=attitude.quaternion,
                        euler=attitude.euler,
                        gravity_g=attitude.gravity_g,
                        linear_accel_g=attitude.linear_accel_g,
                        accel_g=reading.accel_g,
                        gyro_dps=corrected_gyro,
                        raw_gyro_dps=raw_gyro,
                        mag=cal_mag if cal_mag is not None else self._last_mag,
                        mag_valid=mag_valid,
                        temperature_c=reading.temperature_c,
                        timestamp=now,
                        sample_count=self._sample_count,
                    )
                )

                self._error = None
                self._consecutive_failures = 0
                cycle += 1
            except Exception as exc:
                # Any failure in the cycle (read, calibration, fusion) must not
                # kill the thread: record the error for the state layer and keep
                # spinning so recovery is possible when the fault clears.
                self._consecutive_failures += 1
                self._error = str(exc)
                logger.exception(
                    "IMU pipeline cycle failed (%d consecutive)",
                    self._consecutive_failures,
                )
                self._sleep_fn(self._sample_period)
                continue

            elapsed = self._time_fn() - loop_start
            sleep_for = self._sample_period - elapsed
            if sleep_for > 0:
                self._sleep_fn(sleep_for)

    def _maybe_collect(self, raw_mag: Vector3) -> None:
        with self._mag_lock:
            if self._collecting and len(self._mag_samples) < self._max_mag_samples:
                self._mag_samples.append(raw_mag)


# ---------------------------------------------------------------------------
# Sensor service (asyncio side: publishing, ADC, calibration endpoints)
# ---------------------------------------------------------------------------


class SensorService:
    def __init__(self, settings: Settings, event_sink: EventSink) -> None:
        self.settings = settings
        self.event_sink = event_sink
        self._task: asyncio.Task[None] | None = None
        self._imu_source: ImuSource | None = None
        self._pipeline: ImuPipeline | None = None
        self._adc_readers: list[tuple[int, Mcp3204Reader]] = []
        self._imu_calibration = self._load_imu_calibration()
        # Serializes all calibration-mutating coroutines so overlapping requests
        # (level / gyro-zero / reset / mag start-finish-cancel) can't interleave
        # writes to the shared calibration or the filter-reset request. The
        # stabilization controller shares this lock for its enable transition
        # (see `calibration_lock`) so enable-vs-calibrate cannot race.
        self._calibration_lock = asyncio.Lock()
        # Checked INSIDE the calibration lock; the route-level pre-check alone
        # is check-then-act and can race a concurrent stabilization enable.
        self._stabilization_engaged: Callable[[], bool] | None = None
        self._enabled = False
        self._imu_active = False
        self._imu_error: str | None = None
        self._adc_banks: list[AdcBankState] = self._disabled_adc_banks()
        self._demo_started_at = monotonic()
        # Blocking smbus2/spidev opens are offloaded to a thread with this
        # timeout so a wedged device can never stall the event loop / startup.
        self._device_open_timeout_sec = 5.0
        self._last_state = self._build_state()

    @property
    def _use_emulated_sensors(self) -> bool:
        return self.settings.emulate_devices and not self.settings.sensors_enabled

    @property
    def state(self) -> SensorState:
        if self._pipeline is not None:
            return self._build_state()
        return self._last_state.model_copy(deep=True)

    def latest_attitude(self) -> AttitudeState | None:
        """Latest fused attitude snapshot (raw euler), or None if unavailable.

        Public, lock-protected, allocation-free accessor for consumers such as
        the Phase 2 stabilization controller. Euler angles are RAW (not
        level-corrected); apply the level offsets from the IMU calibration.
        """
        if self._pipeline is None:
            return None
        return self._pipeline.shared.snapshot()

    def level_offsets(self) -> tuple[float, float]:
        """(roll, pitch) level-calibration offsets in degrees for stabilization."""
        return (
            self._imu_calibration.level_roll_deg,
            self._imu_calibration.level_pitch_deg,
        )

    @property
    def calibration_lock(self) -> asyncio.Lock:
        """Shared with StabilizationController's enable transition.

        Holding this lock across both calibration mutations and the enable
        transition is what makes "never calibrate while stabilization is
        engaged" an actual invariant instead of a racy check-then-act.
        """
        return self._calibration_lock

    def set_stabilization_guard(self, engaged: Callable[[], bool]) -> None:
        self._stabilization_engaged = engaged

    def _ensure_stabilization_idle(self) -> None:
        if self._stabilization_engaged is not None and self._stabilization_engaged():
            raise RuntimeError("stabilization active — disable first")

    # -- calibration endpoints ---------------------------------------------

    async def calibrate_level(self) -> SensorState:
        async with self._calibration_lock:
            self._ensure_stabilization_idle()
            if self._pipeline is None:
                raise RuntimeError("IMU pipeline is not running")
            snapshot = await self._await_snapshot()
            if snapshot is None:
                # Do NOT silently no-op: the caller must be able to tell
                # "calibration applied" from "no fused sample was available".
                raise RuntimeError("no fused attitude sample available yet — retry shortly")
            self._imu_calibration.level_roll_deg = snapshot.euler.roll_deg
            self._imu_calibration.level_pitch_deg = snapshot.euler.pitch_deg
            self._imu_calibration.updated_at = datetime.now(UTC)
            await self._save_imu_calibration()
            self._apply_calibration()
            await self._publish()
            return self.state

    async def calibrate_gyro_zero(self, sample_count: int = 60) -> SensorState:
        async with self._calibration_lock:
            self._ensure_stabilization_idle()
            if self._pipeline is None:
                raise RuntimeError("IMU pipeline is not running")
            total = _ZERO
            collected = 0
            interval = min(self.settings.sensor_poll_interval_sec, 0.02)
            for _ in range(sample_count):
                snapshot = self._pipeline.shared.snapshot() if self._pipeline else None
                if snapshot is not None:
                    total = add3(total, snapshot.raw_gyro_dps)
                    collected += 1
                await asyncio.sleep(interval)

            if collected == 0:
                # Same contract as calibrate_level: a no-op must be an error,
                # not a 200 that leaves the previous offsets in place.
                raise RuntimeError(
                    "no gyro samples collected — is the IMU pipeline producing data?"
                )
            self._imu_calibration.gyro_offset_dps = ImuVector(
                x=total.x / collected,
                y=total.y / collected,
                z=total.z / collected,
            )
            self._imu_calibration.updated_at = datetime.now(UTC)
            await self._save_imu_calibration()
            self._apply_calibration()
            self._reset_attitude_filter()
            await self._publish()
            return self.state

    async def reset_imu_calibration(self) -> SensorState:
        async with self._calibration_lock:
            self._ensure_stabilization_idle()
            self._imu_calibration = ImuCalibration()
            await self._save_imu_calibration()
            self._apply_calibration()
            self._reset_attitude_filter()
            await self._publish()
            return self.state

    async def start_mag_calibration(self) -> SensorState:
        async with self._calibration_lock:
            self._ensure_stabilization_idle()
            if self._pipeline is None:
                raise RuntimeError("IMU pipeline is not running")
            self._pipeline.start_mag_collection()
            await self._publish()
            return self.state

    async def cancel_mag_calibration(self) -> SensorState:
        async with self._calibration_lock:
            if self._pipeline is not None:
                self._pipeline.cancel_mag_collection()
            await self._publish()
            return self.state

    async def finish_mag_calibration(self) -> tuple[SensorState, MagCalibrationQuality]:
        async with self._calibration_lock:
            self._ensure_stabilization_idle()
            if self._pipeline is None:
                raise RuntimeError("IMU pipeline is not running")
            samples = self._pipeline.stop_mag_collection()
            try:
                result = fit_mag_calibration(samples)
            except ValueError:
                # Too few samples / degenerate span: keep the buffer and resume
                # collecting so the operator can rotate more and retry, instead
                # of throwing away tens of seconds of collected motion.
                self._pipeline.resume_mag_collection()
                raise

            self._imu_calibration.mag_offset = _model_vector(result.offset)
            self._imu_calibration.mag_scale = _model_vector(result.scale)
            self._imu_calibration.updated_at = datetime.now(UTC)
            await self._save_imu_calibration()
            self._apply_calibration()
            self._reset_attitude_filter()
            await self._publish()

            quality = MagCalibrationQuality(
                sample_count=result.sample_count,
                residual=result.residual,
                coverage=result.coverage,
                offset=_model_vector(result.offset),
                scale=_model_vector(result.scale),
            )
            return self.state, quality

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        if not self.settings.sensors_enabled and not self._use_emulated_sensors:
            self._enabled = False
            self._imu_active = False
            self._adc_banks = self._disabled_adc_banks()
            self._last_state = self._build_state()
            return

        self._enabled = True
        self._demo_started_at = monotonic()
        await self._open_imu()
        await self._open_adc()
        # Prime ADC + publish an initial state before the periodic loop starts.
        self._read_adc_once()
        await self._publish()
        self._task = asyncio.create_task(self._poll_loop(), name="sensor-poll")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Capture a final snapshot for `state` reads that happen after teardown.
        self._last_state = self._build_state()
        imu_thread_alive = False
        if self._pipeline is not None:
            await asyncio.to_thread(self._pipeline.stop)
            # If the thread refused to join, it may still be mid-read. Closing
            # the IMU fd now would race that read, so we leak it instead.
            imu_thread_alive = self._pipeline.is_alive()
            self._pipeline = None
        self._close_devices(skip_imu=imu_thread_alive)
        self._imu_active = False

    # -- device management --------------------------------------------------

    def _make_imu_source(self) -> ImuSource:
        # Highest precedence: an explicit replay directory always wins, even
        # over emulate_devices, so `--replay` behaves predictably regardless
        # of what else is set.
        if self.settings.replay_dir:
            return ReplayImuSource(
                Path(self.settings.replay_dir),
                time_scale=self.settings.replay_time_scale,
            )
        if self._use_emulated_sensors:
            return EmulatedImuSource(scenario=self.settings.emulated_imu_scenario)
        return RealImuSource(self.settings)

    async def _open_imu(self) -> None:
        self._imu_error = None
        self._imu_source = self._make_imu_source()
        try:
            # Offload the blocking smbus2 open to a worker thread with a hard
            # timeout so a wedged bus never stalls the asyncio event loop.
            await asyncio.wait_for(
                asyncio.to_thread(self._imu_source.open),
                timeout=self._device_open_timeout_sec,
            )
        except Exception as exc:
            logger.exception("BMX055 initialization failed")
            self._imu_error = str(exc) or repr(exc)
            try:
                # The fallback close must be timeout-bounded too: Bmx055Reader
                # guards open()/close() with one lock, so after an open timeout
                # the worker thread may still hold that lock indefinitely and
                # close() would block on it forever. Leak the device instead of
                # hanging startup.
                await asyncio.wait_for(
                    asyncio.to_thread(self._imu_source.close),
                    timeout=self._device_open_timeout_sec,
                )
            except Exception:
                logger.exception("BMX055 close failed or timed out after init error")
            self._imu_source = None
            self._imu_active = False
            return

        self._pipeline = ImuPipeline(
            source=self._imu_source,
            sample_rate_hz=self.settings.imu_sample_rate_hz,
            mag_rate_hz=self.settings.imu_mag_sample_rate_hz,
            kp=self.settings.mahony_kp,
            ki=self.settings.mahony_ki,
            calibration=self._imu_calibration,
            max_mag_samples=self.settings.mag_calibration_max_samples,
        )
        self._pipeline.start()
        self._imu_active = True

    async def _open_adc(self) -> None:
        self._adc_readers = []
        self._adc_banks = self._disabled_adc_banks()
        if self._use_emulated_sensors:
            return
        for bank_index, device in enumerate(self._adc_devices()):
            reader = Mcp3204Reader(
                bus=self.settings.adc_spi_bus,
                device=device,
                vref=self.settings.adc_vref,
                max_speed_hz=self.settings.adc_spi_max_speed_hz,
            )
            try:
                # Offload the blocking spidev open with a hard timeout (see
                # _open_imu): a wedged SPI device must not stall startup.
                await asyncio.wait_for(
                    asyncio.to_thread(reader.open),
                    timeout=self._device_open_timeout_sec,
                )
            except Exception as exc:
                logger.exception("MCP3204 initialization failed on SPI device %s", device)
                try:
                    # Timeout-bounded for the same reason as the IMU fallback
                    # close: never let cleanup of a wedged device hang startup.
                    await asyncio.wait_for(
                        asyncio.to_thread(reader.close),
                        timeout=self._device_open_timeout_sec,
                    )
                except Exception:
                    logger.exception("MCP3204 close failed or timed out after init error")
                if bank_index < len(self._adc_banks):
                    self._adc_banks[bank_index].connection_state = SensorConnectionState.ERROR
                    self._adc_banks[bank_index].error = str(exc) or repr(exc)
                continue
            self._adc_readers.append((bank_index, reader))

    def _close_devices(self, *, skip_imu: bool = False) -> None:
        if self._imu_source is not None:
            if skip_imu:
                logger.error(
                    "Not closing IMU device: pipeline thread is still alive. "
                    "Leaking the fd to avoid closing under an in-flight read."
                )
                self._imu_source = None
            else:
                self._imu_source.close()
                self._imu_source = None
        for _, reader in self._adc_readers:
            reader.close()
        self._adc_readers = []

    def _reset_attitude_filter(self) -> None:
        if self._pipeline is not None:
            self._pipeline.request_filter_reset()

    def _apply_calibration(self) -> None:
        if self._pipeline is not None:
            self._pipeline.apply_calibration(self._imu_calibration)

    async def _await_snapshot(self, timeout: float = 0.5) -> AttitudeState | None:
        if self._pipeline is None:
            return None
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            snapshot = self._pipeline.shared.snapshot()
            if snapshot is not None:
                return snapshot
            await asyncio.sleep(0.01)
        return self._pipeline.shared.snapshot()

    # -- polling / publishing ----------------------------------------------

    async def _poll_loop(self) -> None:
        interval = self.settings.sensor_publish_interval_sec
        while True:
            await asyncio.sleep(interval)
            await asyncio.to_thread(self._read_adc_once)
            await self._publish()

    def _read_adc_once(self) -> None:
        if self._use_emulated_sensors:
            self._read_adc_emulated()
            return

        now = datetime.now(UTC)
        adc_banks = [bank.model_copy(deep=True) for bank in self._adc_banks]
        for bank_index, reader in self._adc_readers:
            try:
                readings = reader.read_all()
                adc_banks[bank_index] = AdcBankState(
                    bus=reader.bus,
                    device=reader.device,
                    connection_state=SensorConnectionState.CONNECTED,
                    channels=[
                        AdcChannelState(
                            bank=bank_index,
                            channel=item.channel,
                            raw=item.raw,
                            voltage=item.voltage,
                        )
                        for item in readings
                    ],
                    updated_at=now,
                )
            except Exception as exc:
                adc_banks[bank_index] = AdcBankState(
                    bus=reader.bus,
                    device=reader.device,
                    connection_state=SensorConnectionState.ERROR,
                    error=str(exc),
                    channels=[
                        AdcChannelState(bank=bank_index, channel=channel)
                        for channel in range(4)
                    ],
                    updated_at=now,
                )
        self._adc_banks = adc_banks

    def _read_adc_emulated(self) -> None:
        now = datetime.now(UTC)
        elapsed = monotonic() - self._demo_started_at
        adc_banks: list[AdcBankState] = []
        for bank_index, device in enumerate(self._adc_devices()):
            channels: list[AdcChannelState] = []
            for channel in range(4):
                phase = elapsed * (0.45 + channel * 0.08) + bank_index * pi * 0.5 + channel
                raw = int(max(0, min(4095, 1900 + 850 * sin(phase) + 180 * sin(phase * 2.7))))
                channels.append(
                    AdcChannelState(
                        bank=bank_index,
                        channel=channel,
                        raw=raw,
                        voltage=(raw / 4095.0) * self.settings.adc_vref,
                    )
                )
            adc_banks.append(
                AdcBankState(
                    bus=self.settings.adc_spi_bus,
                    device=device,
                    connection_state=SensorConnectionState.CONNECTED,
                    channels=channels,
                    updated_at=now,
                )
            )
        self._adc_banks = adc_banks

    async def _publish(self) -> None:
        state = self._build_state()
        self._last_state = state
        await self.event_sink(
            TelemetryEvent(
                type="sensor_state",
                payload={"sensors": state.model_dump(mode="json")},
            )
        )

    # -- state assembly -----------------------------------------------------

    def _build_state(self) -> SensorState:
        return SensorState(
            enabled=self._enabled,
            imu=self._build_imu_state(),
            adc_banks=[bank.model_copy(deep=True) for bank in self._adc_banks],
            updated_at=datetime.now(UTC),
        )

    def _build_imu_state(self) -> Bmx055State:
        if not self._imu_active and self._pipeline is None:
            state = SensorConnectionState.DISABLED
            if self._enabled and self._imu_error is not None:
                state = SensorConnectionState.ERROR
            return Bmx055State(
                connection_state=state,
                error=self._imu_error,
                calibration=self._imu_calibration,
            )

        # A pipeline object that exists but whose thread has died is a hard
        # error: surface it rather than reporting a stale CONNECTED snapshot.
        if self._pipeline is not None and not self._pipeline.is_alive():
            return Bmx055State(
                connection_state=SensorConnectionState.ERROR,
                error=self._pipeline.error or "IMU pipeline thread is not running",
                calibration=self._imu_calibration,
                mag_calibration_active=self._pipeline.mag_collection_active,
                mag_calibration_samples=self._pipeline.mag_sample_count,
            )

        collecting = self._pipeline.mag_collection_active if self._pipeline else False
        collected = self._pipeline.mag_sample_count if self._pipeline else 0
        pipeline_error = self._pipeline.error if self._pipeline else None
        snapshot = self._pipeline.shared.snapshot() if self._pipeline else None

        if snapshot is None:
            connection_state = SensorConnectionState.CONNECTING
            if pipeline_error is not None:
                connection_state = SensorConnectionState.ERROR
            return Bmx055State(
                connection_state=connection_state,
                error=pipeline_error,
                calibration=self._imu_calibration,
                mag_calibration_active=collecting,
                mag_calibration_samples=collected,
            )

        raw_orientation = _orientation_from_euler(snapshot.euler)
        corrected_orientation = _orientation_from_euler(snapshot.euler, self._imu_calibration)
        return Bmx055State(
            connection_state=SensorConnectionState.CONNECTED,
            quaternion=_model_quaternion(snapshot.quaternion),
            accel_g=_model_vector(snapshot.accel_g),
            gyro_dps=_model_vector(snapshot.gyro_dps),
            mag_raw=_model_vector(snapshot.mag),
            gravity_g=_model_vector(snapshot.gravity_g),
            linear_accel_g=_model_vector(snapshot.linear_accel_g),
            raw_orientation=raw_orientation,
            orientation=corrected_orientation,
            calibration=self._imu_calibration,
            temperature_c=snapshot.temperature_c,
            sample_count=snapshot.sample_count,
            mag_calibration_active=collecting,
            mag_calibration_samples=collected,
            updated_at=datetime.now(UTC),
        )

    # -- helpers ------------------------------------------------------------

    def _disabled_adc_banks(self) -> list[AdcBankState]:
        return [
            AdcBankState(
                bus=self.settings.adc_spi_bus,
                device=device,
                channels=[
                    AdcChannelState(bank=bank_index, channel=channel)
                    for channel in range(4)
                ],
            )
            for bank_index, device in enumerate(self._adc_devices())
        ]

    def _load_imu_calibration(self) -> ImuCalibration:
        path = self.settings.imu_calibration_path
        if not path.exists():
            return ImuCalibration()
        try:
            return ImuCalibration.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load IMU calibration from %s", path)
            return ImuCalibration()

    async def _save_imu_calibration(self) -> None:
        await asyncio.to_thread(self._save_imu_calibration_sync)

    def _save_imu_calibration_sync(self) -> None:
        path = self.settings.imu_calibration_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._imu_calibration.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def _adc_devices(self) -> list[int]:
        devices: list[int] = []
        for item in self.settings.adc_spi_devices.split(","):
            stripped = item.strip()
            if stripped == "":
                continue
            devices.append(int(stripped))
        return devices
