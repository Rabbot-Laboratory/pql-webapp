from __future__ import annotations

import time
from math import isfinite
from pathlib import Path

import pytest

from highend_server.application.experiment import CSV_HEADER
from highend_server.domain.models import ImuCalibration
from highend_server.sensors.replay_source import ReplayImuSource, load_replay_samples
from highend_server.sensors.sensor_service import ImuPipeline

ACTUATOR_COUNT = 8


def _tick_row(
    *,
    elapsed_ms: float,
    experiment_id: str = "20260710_120000_test",
    actuator_id: int = 0,
    blank: bool = False,
    gyro: tuple[float, float, float] = (0.0, 0.0, 0.0),
    accel: tuple[float, float, float] = (0.0, 0.0, 1.0),
    mag: tuple[float, float, float] = (20.0, 0.0, -40.0),
    mag_valid: bool = True,
) -> dict[str, str]:
    """Build one telemetry.csv row dict keyed by the real CSV_HEADER."""
    values: dict[str, str] = dict.fromkeys(CSV_HEADER, "")
    values.update(
        {
            "timestamp": "2026-07-10T12:00:00.000+00:00",
            "elapsed_ms": f"{elapsed_ms:.1f}",
            "experiment_id": experiment_id,
            "git_sha": "deadbeef",
            "motion_frame": "",
            "actuator_id": str(actuator_id),
            "actual_position": "0",
            "base_target": "0",
            "effective_target": "0",
            "stabilization_correction": "0.00",
            "pressure": "0",
            "control_mode": "idle",
            "stabilization_enabled": "0",
            "accel_confidence_candidate": "high",
        }
    )
    if not blank:
        values.update(
            {
                "roll": "1.000",
                "pitch": "2.000",
                "yaw": "3.000",
                "gyro_x": f"{gyro[0]:.3f}",
                "gyro_y": f"{gyro[1]:.3f}",
                "gyro_z": f"{gyro[2]:.3f}",
                "accel_x": f"{accel[0]:.5f}",
                "accel_y": f"{accel[1]:.5f}",
                "accel_z": f"{accel[2]:.5f}",
                "accel_norm": "1.00000",
                "linear_accel_x": "0.00000",
                "linear_accel_y": "0.00000",
                "linear_accel_z": "0.00000",
                "mag_x": f"{mag[0]:.5f}",
                "mag_y": f"{mag[1]:.5f}",
                "mag_z": f"{mag[2]:.5f}",
                "mag_valid": "1" if mag_valid else "0",
            }
        )
    return values


def _write_telemetry_csv(csv_path: Path, ticks: list[dict[str, str]]) -> None:
    """Write ``ticks`` (one dict per sample tick) as an ACTUATOR_COUNT-row-per-tick CSV."""
    lines = [",".join(CSV_HEADER)]
    for tick in ticks:
        for actuator_id in range(ACTUATOR_COUNT):
            row = dict(tick)
            row["actuator_id"] = str(actuator_id)
            lines.append(",".join(row[col] for col in CSV_HEADER))
    csv_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def _sample_ticks() -> list[dict[str, str]]:
    return [
        _tick_row(
            elapsed_ms=0.0,
            gyro=(1.0, 2.0, 3.0),
            accel=(0.1, 0.2, 0.9),
            mag=(10.0, 11.0, 12.0),
            mag_valid=True,
        ),
        _tick_row(
            elapsed_ms=40.0,
            gyro=(4.0, 5.0, 6.0),
            accel=(0.2, 0.3, 0.8),
            mag=(20.0, 21.0, 22.0),
            mag_valid=True,
        ),
        _tick_row(
            elapsed_ms=80.0,
            gyro=(7.0, 8.0, 9.0),
            accel=(0.3, 0.4, 0.7),
            mag=(30.0, 31.0, 32.0),
            mag_valid=False,
        ),
    ]


def _build_csv_with_blank_tick(tmp_path: Path) -> Path:
    ticks = _sample_ticks() + [_tick_row(elapsed_ms=120.0, blank=True)]
    csv_path = tmp_path / "telemetry.csv"
    _write_telemetry_csv(csv_path, ticks)
    return csv_path


# ---------------------------------------------------------------------------
# load_replay_samples
# ---------------------------------------------------------------------------


def test_load_replay_samples_parses_and_dedupes_and_skips_blank_tick(tmp_path: Path) -> None:
    csv_path = _build_csv_with_blank_tick(tmp_path)

    samples = load_replay_samples(csv_path)

    # 3 usable ticks (the 4th, blank, tick is skipped); dedupe collapsed the
    # 8 actuator rows per tick down to 1 sample each.
    assert len(samples) == 3

    assert samples[0].elapsed_ms == 0.0
    assert samples[0].reading.gyro_dps.x == pytest.approx(1.0)
    assert samples[0].reading.gyro_dps.y == pytest.approx(2.0)
    assert samples[0].reading.gyro_dps.z == pytest.approx(3.0)
    assert samples[0].reading.accel_g.x == pytest.approx(0.1)
    assert samples[0].reading.accel_g.z == pytest.approx(0.9)
    assert samples[0].reading.mag_raw.x == pytest.approx(10.0)
    assert samples[0].reading.mag_raw.z == pytest.approx(12.0)
    assert samples[0].reading.temperature_c is None

    assert samples[1].elapsed_ms == 40.0
    assert samples[1].reading.gyro_dps.x == pytest.approx(4.0)

    # mag_valid == "0" on the third tick must NOT suppress its mag values —
    # they are still reconstructed faithfully.
    assert samples[2].elapsed_ms == 80.0
    assert samples[2].reading.mag_raw.x == pytest.approx(30.0)
    assert samples[2].reading.gyro_dps.x == pytest.approx(7.0)


def test_load_replay_samples_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        load_replay_samples(tmp_path / "does_not_exist.csv")


def test_load_replay_samples_empty_csv_raises(tmp_path: Path) -> None:
    csv_path = tmp_path / "telemetry.csv"
    csv_path.write_text(",".join(CSV_HEADER) + "\r\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no usable"):
        load_replay_samples(csv_path)


def test_load_replay_samples_missing_column_raises(tmp_path: Path) -> None:
    csv_path = tmp_path / "telemetry.csv"
    header = [col for col in CSV_HEADER if col != "gyro_x"]
    csv_path.write_text(",".join(header) + "\r\n" + ",".join([""] * len(header)) + "\r\n")

    with pytest.raises(ValueError, match="missing required columns"):
        load_replay_samples(csv_path)


# ---------------------------------------------------------------------------
# ReplayImuSource pacing
# ---------------------------------------------------------------------------


class _FakeClock:
    """Deterministic fake clock: sleep_fn advances the clock by the sleep amount."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def time_fn(self) -> float:
        return self.t

    def sleep_fn(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def _pacing_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "telemetry.csv"
    _write_telemetry_csv(csv_path, _sample_ticks())
    return csv_path


def test_replay_pacing_scale_1(tmp_path: Path) -> None:
    csv_path = _pacing_csv(tmp_path)
    clock = _FakeClock()
    source = ReplayImuSource(
        tmp_path, time_scale=1.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn
    )
    source.open()

    for _ in range(3):
        source.read()

    assert clock.sleeps == pytest.approx([0.0, 0.04, 0.04])
    assert csv_path.exists()  # sanity: we actually read from the right file


def test_replay_pacing_scale_2_halves_sleeps(tmp_path: Path) -> None:
    _pacing_csv(tmp_path)
    clock = _FakeClock()
    source = ReplayImuSource(
        tmp_path, time_scale=2.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn
    )
    source.open()

    for _ in range(3):
        source.read()

    assert clock.sleeps == pytest.approx([0.0, 0.02, 0.02])


def test_replay_pacing_already_past_due_sleeps_zero(tmp_path: Path) -> None:
    _pacing_csv(tmp_path)
    clock = _FakeClock()
    source = ReplayImuSource(
        tmp_path, time_scale=1.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn
    )
    source.open()
    clock.t = 5.0  # wall clock has raced far ahead of the recording start

    source.read()

    assert clock.sleeps == [0.0]


# ---------------------------------------------------------------------------
# EOF behaviour
# ---------------------------------------------------------------------------


def test_replay_eof_freezes_last_reading_with_zero_gyro(tmp_path: Path) -> None:
    _pacing_csv(tmp_path)
    clock = _FakeClock()
    source = ReplayImuSource(
        tmp_path, time_scale=1.0, time_fn=clock.time_fn, sleep_fn=clock.sleep_fn
    )
    source.open()

    for _ in range(3):
        source.read()
    assert source.finished is False

    fourth = source.read()
    assert source.finished is True
    assert fourth.gyro_dps.x == 0.0
    assert fourth.gyro_dps.y == 0.0
    assert fourth.gyro_dps.z == 0.0
    # Accel/mag hold the last recorded sample (tick 3: elapsed_ms=80.0).
    assert fourth.accel_g.x == pytest.approx(0.3)
    assert fourth.mag_raw.x == pytest.approx(30.0)

    # Repeated reads after EOF stay stable.
    fifth = source.read()
    assert fifth.gyro_dps.x == 0.0
    assert fifth.accel_g.x == pytest.approx(0.3)
    assert source.finished is True


def test_replay_sample_count(tmp_path: Path) -> None:
    _pacing_csv(tmp_path)
    source = ReplayImuSource(tmp_path)
    source.open()
    assert source.sample_count == 3


# ---------------------------------------------------------------------------
# Integration-lite: ImuPipeline driven entirely by ReplayImuSource
# ---------------------------------------------------------------------------


def test_pipeline_with_replay_source_produces_finite_attitude(tmp_path: Path) -> None:
    """A real ImuPipeline thread fed by ReplayImuSource (fast time_scale) must
    produce finite fused attitude and keep advancing sample_count, exactly as
    it would with a real or emulated source."""
    n_ticks = 60
    ticks = [
        _tick_row(
            elapsed_ms=i * 40.0,
            gyro=(0.0, 0.0, 0.0),
            accel=(0.0, 0.0, 1.0),
            mag=(20.0, 0.0, -40.0),
            mag_valid=True,
        )
        for i in range(n_ticks)
    ]
    _write_telemetry_csv(tmp_path / "telemetry.csv", ticks)

    source = ReplayImuSource(tmp_path, time_scale=50.0)
    source.open()
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
        time.sleep(0.1)
        snapshot1 = pipeline.shared.snapshot()
        assert snapshot1 is not None
        count1 = snapshot1.sample_count

        time.sleep(0.15)
        snapshot2 = pipeline.shared.snapshot()
    finally:
        pipeline.stop(timeout=2.0)

    assert snapshot2 is not None
    assert snapshot2.sample_count > count1
    assert isfinite(snapshot2.euler.roll_deg)
    assert isfinite(snapshot2.euler.pitch_deg)
    assert isfinite(snapshot2.quaternion.w)
    assert isfinite(snapshot2.quaternion.x)
    assert source.sample_count == n_ticks
