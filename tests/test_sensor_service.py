import asyncio
import logging
import threading
import time
from math import cos, isfinite, pi, sin

from highend_server.config import Settings
from highend_server.domain.models import ImuCalibration, SensorConnectionState, TelemetryEvent
from highend_server.sensors.attitude import DEG_TO_RAD, MahonyMARG
from highend_server.sensors.imu_bmx055 import Bmx055Reading, Vector3
from highend_server.sensors.imu_scenarios import ScenarioSample
from highend_server.sensors.mag_calibration import MIN_SAMPLES
from highend_server.sensors.sensor_service import EmulatedImuSource, ImuPipeline, SensorService


def _emulated_settings(**overrides) -> Settings:
    base = dict(emulate_devices=True, sensors_enabled=False, sensor_poll_interval_sec=0.01)
    base.update(overrides)
    return Settings(**base)


def test_mcp3208_hardware_defaults() -> None:
    settings = Settings()

    assert settings.adc_spi_devices == "0"
    assert settings.adc_vref == 5.0


def test_sensor_service_emits_demo_sensor_state_in_device_emulation_mode() -> None:
    async def scenario() -> None:
        events: list[TelemetryEvent] = []
        settings = Settings(
            emulate_devices=True,
            sensors_enabled=False,
            sensor_poll_interval_sec=0.01,
        )

        async def sink(event: TelemetryEvent) -> None:
            events.append(event)

        service = SensorService(settings=settings, event_sink=sink)

        await service.start()
        try:
            await asyncio.sleep(0.03)
        finally:
            await service.stop()

        state = service.state
        assert state.enabled is True
        assert state.imu.connection_state is SensorConnectionState.CONNECTED
        assert state.imu.accel_g is not None
        assert state.imu.gyro_dps is not None
        assert state.imu.mag_raw is not None
        assert state.imu.orientation is not None
        assert state.imu.orientation.yaw_deg is not None
        assert state.imu.quaternion is not None
        assert state.imu.gravity_g is not None
        assert state.imu.linear_accel_g is not None
        assert state.adc_banks
        assert len(state.adc_banks) == 1
        assert state.adc_banks[0].device == 0
        assert len(state.adc_banks[0].channels) == 8
        assert all(
            bank.connection_state is SensorConnectionState.CONNECTED
            for bank in state.adc_banks
        )
        assert any(event.type == "sensor_state" for event in events)

    asyncio.run(scenario())


def test_sensor_service_stays_disabled_without_sensor_or_device_emulation() -> None:
    async def scenario() -> None:
        events: list[TelemetryEvent] = []
        settings = Settings(emulate_devices=False, sensors_enabled=False)

        async def sink(event: TelemetryEvent) -> None:
            events.append(event)

        service = SensorService(settings=settings, event_sink=sink)

        await service.start()
        await service.stop()

        state = service.state
        assert state.enabled is False
        assert state.imu.connection_state is SensorConnectionState.DISABLED
        assert events == []

    asyncio.run(scenario())


def test_imu_thread_starts_and_stops_cleanly() -> None:
    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(settings=_emulated_settings(), event_sink=sink)

        assert not _imu_thread_alive()
        await service.start()
        await asyncio.sleep(0.05)
        assert _imu_thread_alive()

        await asyncio.wait_for(service.stop(), timeout=3.0)
        # Give the OS a beat to reap the joined thread.
        await asyncio.sleep(0.05)
        assert not _imu_thread_alive()

    asyncio.run(scenario())


def test_snapshot_is_consistent_and_advances() -> None:
    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(settings=_emulated_settings(), event_sink=sink)
        await service.start()
        try:
            await asyncio.sleep(0.05)
            first = service.state.imu.sample_count
            await asyncio.sleep(0.1)
            second = service.state.imu.sample_count
        finally:
            await service.stop()

        assert first > 0
        assert second > first  # the 100 Hz thread keeps producing samples

        imu = service.state.imu
        q = imu.quaternion
        assert q is not None
        norm = (q.w ** 2 + q.x ** 2 + q.y ** 2 + q.z ** 2) ** 0.5
        assert abs(norm - 1.0) < 1e-6

    asyncio.run(scenario())


def test_publish_rate_is_decimated_below_sample_rate() -> None:
    async def scenario() -> None:
        events: list[TelemetryEvent] = []

        async def sink(event: TelemetryEvent) -> None:
            events.append(event)

        service = SensorService(
            settings=_emulated_settings(
                sensor_publish_interval_sec=0.05,
                imu_sample_rate_hz=100.0,
            ),
            event_sink=sink,
        )
        await service.start()
        try:
            await asyncio.sleep(0.35)
        finally:
            await service.stop()

        publishes = [e for e in events if e.type == "sensor_state"]
        sample_count = service.state.imu.sample_count
        # ~7 publishes in 0.35s at 20 Hz; certainly far fewer than 100 Hz samples.
        assert len(publishes) <= 12
        assert sample_count > len(publishes) * 2

    asyncio.run(scenario())


def test_gyro_zero_calibration_from_shared_state() -> None:
    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(
            settings=_emulated_settings(sensor_poll_interval_sec=0.001),
            event_sink=sink,
        )
        await service.start()
        try:
            await asyncio.sleep(0.05)
            state = await service.calibrate_gyro_zero(sample_count=10)
        finally:
            await service.stop()

        assert state.imu.calibration.gyro_offset_dps is not None

    asyncio.run(scenario())


def test_mag_calibration_flow_mechanics() -> None:
    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(settings=_emulated_settings(), event_sink=sink)
        await service.start()
        try:
            state = await service.start_mag_calibration()
            assert state.imu.mag_calibration_active is True
            await asyncio.sleep(0.15)
            state = service.state
            assert state.imu.mag_calibration_samples > 0
            state = await service.cancel_mag_calibration()
            assert state.imu.mag_calibration_active is False
        finally:
            await service.stop()

    asyncio.run(scenario())


class _SphereMagSource:
    """Synthetic source whose raw mag sweeps a full sphere quickly."""

    def __init__(self) -> None:
        self._i = 0

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def read(self) -> Bmx055Reading:
        i = self._i
        self._i += 1
        theta = pi * ((i * 7) % 37) / 36.0
        phi = 2.0 * pi * ((i * 13) % 29) / 29.0
        mag = Vector3(
            x=10.0 + 50.0 * sin(theta) * cos(phi),
            y=-5.0 + 50.0 * sin(theta) * sin(phi),
            z=3.0 + 50.0 * cos(theta),
        )
        return Bmx055Reading(
            accel_g=Vector3(0.0, 0.0, 1.0),
            gyro_dps=Vector3(0.0, 0.0, 0.0),
            mag_raw=mag,
        )


def test_pipeline_mag_collection_and_fit() -> None:
    pipeline = ImuPipeline(
        source=_SphereMagSource(),
        sample_rate_hz=1000.0,
        mag_rate_hz=1000.0,
        kp=0.8,
        ki=0.02,
        calibration=ImuCalibration(),
        max_mag_samples=500,
        sleep_fn=lambda _seconds: None,
    )
    pipeline.start_mag_collection()
    pipeline.start()
    try:
        deadline = threading.Event()
        # Wait until enough samples are collected (bounded busy-wait).
        for _ in range(2000):
            if pipeline.mag_sample_count >= 200:
                break
            deadline.wait(0.005)
    finally:
        samples = pipeline.stop_mag_collection()
        pipeline.stop(timeout=2.0)

    assert len(samples) >= MIN_SAMPLES
    from highend_server.sensors.mag_calibration import fit

    result = fit(samples)
    assert result.coverage > 0.5
    assert result.residual < 0.2


def _imu_thread_alive() -> bool:
    return any(t.name == "imu-pipeline" and t.is_alive() for t in threading.enumerate())


def test_calibration_endpoints_raise_when_pipeline_not_running() -> None:
    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(settings=_emulated_settings(), event_sink=sink)
        # Service never started -> no pipeline.
        for coro in (
            service.calibrate_level(),
            service.calibrate_gyro_zero(sample_count=1),
            service.start_mag_calibration(),
            service.finish_mag_calibration(),
        ):
            try:
                await coro
            except RuntimeError as exc:
                assert "not running" in str(exc)
            else:  # pragma: no cover
                raise AssertionError("expected RuntimeError without a pipeline")

    asyncio.run(scenario())


def test_slow_device_open_times_out_into_error_state() -> None:
    """A wedged blocking open() must not stall startup (HIGH: event-loop block)."""
    import time as _time

    class _WedgedSource:
        def open(self) -> None:
            _time.sleep(0.5)  # far beyond the configured timeout

        def read(self) -> Bmx055Reading:  # pragma: no cover - never reached
            raise AssertionError("read should not be called")

        def close(self) -> None:
            return None

    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(settings=_emulated_settings(), event_sink=sink)
        service._device_open_timeout_sec = 0.05
        service._make_imu_source = lambda: _WedgedSource()  # type: ignore[method-assign]

        start = _time.monotonic()
        await asyncio.wait_for(service.start(), timeout=2.0)
        elapsed = _time.monotonic() - start
        try:
            assert elapsed < 1.0  # startup was not held hostage by the open
            state = service.state
            assert state.imu.connection_state is SensorConnectionState.ERROR
            assert state.imu.error  # timeout recorded as the error
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_imu_state_reports_error_when_pipeline_thread_dies() -> None:
    class _DyingSource:
        def __init__(self) -> None:
            self.calls = 0

        def open(self) -> None:
            return None

        def close(self) -> None:
            return None

        def read(self) -> Bmx055Reading:
            raise OSError("bus gone")

    pipeline = ImuPipeline(
        source=_DyingSource(),
        sample_rate_hz=1000.0,
        mag_rate_hz=1000.0,
        kp=0.8,
        ki=0.02,
        calibration=ImuCalibration(),
        max_mag_samples=10,
        sleep_fn=lambda _s: None,
    )
    # Simulate a dead thread without spinning real hardware: never started.
    assert pipeline.is_alive() is False

    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(settings=_emulated_settings(), event_sink=sink)
        service._enabled = True
        service._pipeline = pipeline
        state = service.state
        assert state.imu.connection_state is SensorConnectionState.ERROR
        assert state.imu.error

    asyncio.run(scenario())


def test_pipeline_survives_read_exceptions_and_recovers() -> None:
    """The IMU thread must keep spinning through per-cycle failures."""

    class _FlakySource:
        def __init__(self) -> None:
            self.calls = 0

        def open(self) -> None:
            return None

        def close(self) -> None:
            return None

        def read(self) -> Bmx055Reading:
            self.calls += 1
            if self.calls <= 5:
                raise OSError("transient bus error")
            return Bmx055Reading(
                accel_g=Vector3(0.0, 0.0, 1.0),
                gyro_dps=Vector3(0.0, 0.0, 0.0),
                mag_raw=Vector3(20.0, 0.0, -40.0),
            )

    pipeline = ImuPipeline(
        source=_FlakySource(),
        sample_rate_hz=1000.0,
        mag_rate_hz=1000.0,
        kp=0.8,
        ki=0.02,
        calibration=ImuCalibration(),
        max_mag_samples=10,
        sleep_fn=lambda _s: None,
    )
    pipeline.start()
    try:
        deadline = threading.Event()
        for _ in range(2000):
            snapshot = pipeline.shared.snapshot()
            if snapshot is not None and snapshot.sample_count >= 3:
                break
            deadline.wait(0.005)
    finally:
        pipeline.stop(timeout=2.0)

    snapshot = pipeline.shared.snapshot()
    assert snapshot is not None and snapshot.sample_count >= 3  # recovered
    assert pipeline.error is None  # cleared once cycles succeed again


def test_calibration_coroutines_are_serialized() -> None:
    """The asyncio lock must serialize overlapping calibration mutations."""

    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(
            settings=_emulated_settings(sensor_poll_interval_sec=0.001),
            event_sink=sink,
        )
        await service.start()
        try:
            await asyncio.sleep(0.05)
            active: list[int] = []
            max_overlap = 0

            original = service._save_imu_calibration

            async def tracked_save() -> None:
                nonlocal max_overlap
                active.append(1)
                max_overlap = max(max_overlap, len(active))
                await asyncio.sleep(0.01)  # widen the race window
                active.pop()
                await original()

            service._save_imu_calibration = tracked_save  # type: ignore[method-assign]
            await asyncio.gather(
                service.calibrate_gyro_zero(sample_count=3),
                service.reset_imu_calibration(),
                service.calibrate_level(),
            )
            assert max_overlap == 1  # never concurrent
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_wedged_open_with_shared_lock_does_not_hang_startup() -> None:
    """CRITICAL regression: Bmx055Reader guards open()/close() with ONE lock.

    After an open() timeout the worker thread may still hold that lock, so the
    fallback close() would block on it forever without its own timeout — and
    lifespan() awaits start(), so the whole web app would never come up.
    """
    import time as _time

    class _LockedWedgedSource:
        def __init__(self) -> None:
            self._lock = threading.Lock()

        def open(self) -> None:
            with self._lock:
                _time.sleep(0.6)  # holds the lock far beyond the open timeout

        def read(self) -> Bmx055Reading:  # pragma: no cover - never reached
            raise AssertionError("read should not be called")

        def close(self) -> None:
            with self._lock:  # blocks until open() releases — the deadlock shape
                return None

    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(settings=_emulated_settings(), event_sink=sink)
        service._device_open_timeout_sec = 0.05
        service._make_imu_source = lambda: _LockedWedgedSource()  # type: ignore[method-assign]

        start = _time.monotonic()
        await asyncio.wait_for(service.start(), timeout=2.0)
        elapsed = _time.monotonic() - start
        try:
            # Must return after ~open timeout + close timeout, NOT the 0.6s the
            # wedged open holds the lock (and not forever, as pre-fix).
            assert elapsed < 0.4
            state = service.state
            assert state.imu.connection_state is SensorConnectionState.ERROR
            assert state.imu.error
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_calibrate_level_errors_when_no_attitude_sample() -> None:
    """A calibration that cannot run must be an error, not a silent 200 no-op."""

    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(settings=_emulated_settings(), event_sink=sink)
        # Unstarted pipeline: snapshot() stays None.
        service._pipeline = ImuPipeline(
            source=None,  # type: ignore[arg-type]
            sample_rate_hz=100.0,
            mag_rate_hz=20.0,
            kp=0.8,
            ki=0.02,
            calibration=ImuCalibration(),
            max_mag_samples=10,
        )

        async def no_snapshot(timeout: float = 0.5):
            return None

        service._await_snapshot = no_snapshot  # type: ignore[method-assign]
        try:
            await service.calibrate_level()
        except RuntimeError as exc:
            assert "no fused attitude sample" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected RuntimeError when no snapshot is available")

    asyncio.run(scenario())


def test_calibrate_gyro_zero_errors_when_no_samples_collected() -> None:
    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(
            settings=_emulated_settings(sensor_poll_interval_sec=0.001), event_sink=sink
        )
        # Unstarted pipeline: snapshot() stays None -> zero samples collected.
        service._pipeline = ImuPipeline(
            source=None,  # type: ignore[arg-type]
            sample_rate_hz=100.0,
            mag_rate_hz=20.0,
            kp=0.8,
            ki=0.02,
            calibration=ImuCalibration(),
            max_mag_samples=10,
        )
        try:
            await service.calibrate_gyro_zero(sample_count=3)
        except RuntimeError as exc:
            assert "no gyro samples" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected RuntimeError when zero samples were collected")

    asyncio.run(scenario())


def test_calibration_rejected_while_stabilization_engaged() -> None:
    """The in-lock stabilization guard must reject every calibration mutation."""

    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(settings=_emulated_settings(), event_sink=sink)
        service.set_stabilization_guard(lambda: True)
        for coro in (
            service.calibrate_level(),
            service.calibrate_gyro_zero(sample_count=1),
            service.reset_imu_calibration(),
            service.start_mag_calibration(),
            service.finish_mag_calibration(),
        ):
            try:
                await coro
            except RuntimeError as exc:
                assert "stabilization active" in str(exc)
            else:  # pragma: no cover
                raise AssertionError("expected RuntimeError while stabilization is engaged")

    asyncio.run(scenario())


def test_failed_mag_fit_preserves_samples_and_resumes_collection() -> None:
    """A degenerate fit must not discard the operator's collected samples."""

    async def scenario() -> None:
        async def sink(event: TelemetryEvent) -> None:
            return None

        service = SensorService(settings=_emulated_settings(), event_sink=sink)
        pipeline = ImuPipeline(
            source=None,  # type: ignore[arg-type]
            sample_rate_hz=100.0,
            mag_rate_hz=20.0,
            kp=0.8,
            ki=0.02,
            calibration=ImuCalibration(),
            max_mag_samples=100,
        )
        service._pipeline = pipeline
        pipeline.start_mag_collection()
        # Far fewer than MIN_SAMPLES -> fit_mag_calibration raises ValueError.
        for _ in range(max(2, MIN_SAMPLES // 4)):
            pipeline._maybe_collect(Vector3(1.0, 2.0, 3.0))
        collected_before = pipeline.mag_sample_count

        try:
            await service.finish_mag_calibration()
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected ValueError from a degenerate mag fit")

        # Buffer preserved and collection re-armed so the operator can retry.
        assert pipeline.mag_sample_count == collected_before
        assert pipeline.mag_collection_active is True

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Emulated IMU scenarios
# ---------------------------------------------------------------------------


def test_emulated_static_imu_has_no_display_motion() -> None:
    class _VirtualClock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            self.t += 0.25
            return self.t

    source = EmulatedImuSource(time_fn=_VirtualClock(), scenario="static")

    for _ in range(20):
        reading = source.read()
        assert reading.accel_g == Vector3(0.0, 0.0, 1.0)
        assert reading.gyro_dps == Vector3(0.0, 0.0, 0.0)
        assert reading.mag_raw == Vector3(22.0, 0.0, -40.0)


def test_emulated_imu_source_roll_step_scenario_drives_attitude_progression() -> None:
    """EmulatedImuSource(scenario="roll-step") feeds a Mahony filter exactly like
    ImuPipeline does; the fused roll should track the step (0 deg, then +10 deg
    during 2s-5s, then back to 0 deg) because the finite-difference gyro
    reproduces the step's instantaneous rate, so convergence is fast rather
    than depending on the slow accel/mag correction term."""
    step_dt = 0.01  # 100 Hz, same as the production imu_sample_rate_hz default

    class _VirtualClock:
        """Deterministic fake clock: advances by ``step_dt`` on every call.

        EmulatedImuSource computes its own "elapsed" from this same clock
        (matching how ImuPipeline wires a shared ``time_fn``), so driving the
        filter in lockstep with real per-cycle time_fn calls (rather than a
        real wall-clock sleep loop) is fast and fully deterministic.
        """

        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            self.t += step_dt
            return self.t

    source = EmulatedImuSource(time_fn=_VirtualClock(), scenario="roll-step")
    filt = MahonyMARG(kp=0.8, ki=0.02)  # same defaults as ImuPipeline

    checkpoints = {1.0: None, 3.0: None, 6.0: None}
    elapsed = 0.0
    total = int(6.5 / step_dt)
    for _ in range(total):
        reading = source.read()
        gyro_rad = Vector3(
            reading.gyro_dps.x * DEG_TO_RAD,
            reading.gyro_dps.y * DEG_TO_RAD,
            reading.gyro_dps.z * DEG_TO_RAD,
        )
        result = filt.update(
            gyro_rad=gyro_rad, accel_g=reading.accel_g, mag_raw=reading.mag_raw, dt=step_dt
        )
        elapsed += step_dt
        for cp in checkpoints:
            if checkpoints[cp] is None and elapsed >= cp:
                checkpoints[cp] = result.euler.roll_deg

    assert abs(checkpoints[1.0]) < 2.0  # still in the 0deg window
    assert abs(checkpoints[3.0] - 10.0) < 2.0  # deep in the +10deg window
    assert abs(checkpoints[6.0]) < 2.0  # back to 0deg after the step ends


def test_pipeline_survives_sensor_nan_scenario_and_keeps_finite_attitude() -> None:
    """inject_nan must never corrupt the fused attitude: MahonyMARG.update()
    fails closed and holds the last good (finite) quaternion/euler, even
    though the raw accel/gyro pass-through legitimately shows NaN for the
    duration of the (simulated) fault."""

    def scenario(elapsed: float) -> ScenarioSample:
        roll_deg = 12.0 * sin(elapsed * 0.85)
        return ScenarioSample(
            roll_deg=roll_deg, pitch_deg=0.0, yaw_deg=0.0, inject_nan=(elapsed >= 0.05)
        )

    source = EmulatedImuSource(scenario=scenario)
    pipeline = ImuPipeline(
        source=source,
        sample_rate_hz=200.0,
        mag_rate_hz=200.0,
        kp=0.8,
        ki=0.02,
        calibration=ImuCalibration(),
        max_mag_samples=10,
    )
    pipeline.start()
    try:
        time.sleep(0.2)  # well past the 0.05s NaN-injection onset
        snapshot = pipeline.shared.snapshot()
        alive = pipeline.is_alive()
    finally:
        pipeline.stop(timeout=2.0)

    assert alive  # the pipeline thread must never die from a NaN sample
    assert snapshot is not None
    assert snapshot.sample_count > 0
    assert isfinite(snapshot.euler.roll_deg)
    assert isfinite(snapshot.euler.pitch_deg)
    assert isfinite(snapshot.quaternion.w)
    assert isfinite(snapshot.quaternion.x)
    assert isfinite(snapshot.quaternion.y)
    assert isfinite(snapshot.quaternion.z)
    assert pipeline.error is None  # NaN input is skipped, not treated as a cycle failure


def test_pipeline_hold_stale_freezes_timestamp_but_thread_stays_alive() -> None:
    """hold_stale must make ``AttitudeState.timestamp`` genuinely stop
    advancing (the mechanism stabilization.py's staleness check keys on),
    while the pipeline thread keeps running rather than dying or hanging."""

    def scenario(elapsed: float) -> ScenarioSample:
        return ScenarioSample(
            roll_deg=0.0, pitch_deg=0.0, yaw_deg=0.0, hold_stale=(elapsed >= 0.05)
        )

    source = EmulatedImuSource(scenario=scenario)
    pipeline = ImuPipeline(
        source=source,
        sample_rate_hz=200.0,
        mag_rate_hz=200.0,
        kp=0.8,
        ki=0.02,
        calibration=ImuCalibration(),
        max_mag_samples=10,
    )
    # The read() raise (see EmulatedImuSource docstring) is logged every cycle
    # once hold_stale is permanent; silence it so the test output isn't spammed
    # with expected tracebacks.
    logging.disable(logging.CRITICAL)
    try:
        pipeline.start()
        time.sleep(0.15)  # well past the 0.05s stale onset
        snapshot_a = pipeline.shared.snapshot()
        alive_mid = pipeline.is_alive()
        time.sleep(0.2)
        snapshot_b = pipeline.shared.snapshot()
        alive_end = pipeline.is_alive()
        pipeline.stop(timeout=2.0)
    finally:
        logging.disable(logging.NOTSET)

    assert snapshot_a is not None and snapshot_b is not None
    assert snapshot_a.timestamp == snapshot_b.timestamp  # no new sample was ever published
    assert alive_mid and alive_end  # thread survives a permanently failing source
    assert pipeline.error is not None
