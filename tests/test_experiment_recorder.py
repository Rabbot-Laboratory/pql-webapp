"""Unit tests for the experiment-logging subsystem (ExperimentRecorder).

The recorder is driven against a real ``ControlService`` (stub serial gateway)
so the CSV row invariant ``effective_target == clamp(base + round(correction))``
is exercised end-to-end through the same code path that builds outbound frames.
Stabilization and sensor collaborators are lightweight fakes so attitude, gains
and enabled-state are fully deterministic.

Follows the suite-wide ``asyncio.run(scenario())`` convention (pytest-asyncio is
not installed).
"""

from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path

import pytest

from highend_server.application.control_service import ControlService
from highend_server.application.experiment import (
    CSV_HEADER,
    ExperimentAlreadyRunningError,
    ExperimentNotRunningError,
    ExperimentRecorder,
    accel_confidence_candidate,
)
from highend_server.config import Settings
from highend_server.domain.models import (
    POSITION_MAX,
    POSITION_MIN,
    ControlMode,
    ExperimentStartRequest,
    SetTargetRequest,
    StabilizationGains,
    StabilizationState,
    TelemetryEvent,
)
from highend_server.sensors.attitude import EulerAngles, Quaternion
from highend_server.sensors.imu_bmx055 import Vector3
from highend_server.sensors.sensor_service import AttitudeState
from highend_server.transport.serial_gateway import StubSerialGateway


def _make_settings(tmp_path: Path) -> Settings:
    # telemetry_log_root_dir accepts an absolute path (Path-join semantics keep
    # the absolute rhs), so Logs/experiments lands inside tmp_path, not the repo.
    return Settings(
        emulate_devices=False,
        sensors_enabled=False,
        telemetry_log_root_dir=str(tmp_path / "Logs"),
        sensor_config_dir_name=str(tmp_path / "config"),
        experiment_sample_rate_hz=25.0,
        experiment_flush_interval_sec=0.1,
    )


class _FakeStab:
    def __init__(self, *, gains: StabilizationGains | None = None, enabled: bool = False) -> None:
        self._gains = gains or StabilizationGains()
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get_state(self) -> StabilizationState:
        return StabilizationState(enabled=self._enabled, gains=self._gains)


class _FakeSensor:
    def __init__(
        self,
        *,
        attitude: AttitudeState | None = None,
        offsets: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        self._attitude = attitude
        self._offsets = offsets

    def latest_attitude(self) -> AttitudeState | None:
        return self._attitude

    def level_offsets(self) -> tuple[float, float]:
        return self._offsets


def _make_attitude(*, accel_norm: float = 1.0, mag_valid: bool = True) -> AttitudeState:
    zero = Vector3(0.0, 0.0, 0.0)
    return AttitudeState(
        quaternion=Quaternion(1.0, 0.0, 0.0, 0.0),
        euler=EulerAngles(roll_deg=1.234, pitch_deg=-2.345, yaw_deg=10.5),
        gravity_g=Vector3(0.0, 0.0, accel_norm),
        linear_accel_g=zero,
        accel_g=Vector3(0.0, 0.0, accel_norm),
        gyro_dps=Vector3(0.1, -0.2, 0.3),
        raw_gyro_dps=zero,
        mag=Vector3(20.0, -5.0, -40.0),
        mag_valid=mag_valid,
        temperature_c=31.0,
        timestamp=0.0,
        sample_count=1,
    )


def _make_control(settings: Settings) -> ControlService:
    async def _sink(event: TelemetryEvent) -> None:
        return None

    return ControlService(
        settings=settings, gateway=StubSerialGateway(settings), event_sink=_sink
    )


def _make_recorder(
    settings: Settings,
    *,
    control: ControlService | None = None,
    stab: _FakeStab | None = None,
    sensor: _FakeSensor | None = None,
) -> ExperimentRecorder:
    recorder = ExperimentRecorder(settings=settings)
    recorder.bind(
        control_service=control or _make_control(settings),
        stabilization_controller=stab or _FakeStab(),
        sensor_service=sensor or _FakeSensor(attitude=_make_attitude()),
    )
    return recorder


def _read_csv(directory: Path) -> tuple[list[str], list[list[str]]]:
    with (directory / "telemetry.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    return rows[0], rows[1:]


def _read_events(directory: Path) -> list[dict]:
    lines = (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# start / files / manifest
# ---------------------------------------------------------------------------


def test_start_creates_directory_and_four_files(tmp_path: Path) -> None:
    recorder = _make_recorder(_make_settings(tmp_path))

    async def scenario() -> str:
        manifest = await recorder.start(ExperimentStartRequest(experiment_type="gait_test"))
        await recorder.stop()
        return manifest.experiment_id

    experiment_id = asyncio.run(scenario())
    directory = tmp_path / "Logs" / "experiments" / experiment_id
    assert directory.is_dir()
    for name in ("manifest.json", "telemetry.csv", "events.jsonl", "notes.md"):
        assert (directory / name).exists(), name


def test_manifest_fields(tmp_path: Path) -> None:
    gains = StabilizationGains(kp_roll=2.0, ki_roll=0.1, kd_roll=0.4, kp_pitch=2.5, ki_pitch=0.2)
    stab = _FakeStab(gains=gains, enabled=True)
    sensor = _FakeSensor(attitude=_make_attitude(), offsets=(1.5, -0.5))
    settings = _make_settings(tmp_path)
    recorder = _make_recorder(settings, stab=stab, sensor=sensor)

    async def scenario():
        manifest = await recorder.start(
            ExperimentStartRequest(experiment_type="gait_test", name="run A")
        )
        await recorder.stop()
        return manifest

    manifest = asyncio.run(scenario())
    assert manifest.experiment_type == "gait_test"
    assert manifest.name == "run A"
    assert manifest.robot == settings.experiment_robot_name
    assert manifest.sample_rate_hz == 25.0
    # git block present
    assert manifest.git.sha
    assert manifest.git.branch
    # 6 gains
    assert manifest.stabilization["gains"]["kp_roll"] == 2.0
    assert set(manifest.stabilization["gains"]) == {
        "kp_roll", "ki_roll", "kd_roll", "kp_pitch", "ki_pitch", "kd_pitch"
    }
    assert manifest.stabilization["enabled"] is True
    assert manifest.stabilization["derivative_source"] == settings.stabilization_derivative_source
    # imu block
    assert manifest.imu["mahony_kp"] == settings.mahony_kp
    assert manifest.imu["level_offsets"] == [1.5, -0.5]
    # config snapshot
    assert manifest.config_snapshot["experiment_robot_name"] == settings.experiment_robot_name
    # persisted atomically to disk
    directory = tmp_path / "Logs" / "experiments" / manifest.experiment_id
    on_disk = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["experiment_id"] == manifest.experiment_id


def test_double_start_raises(tmp_path: Path) -> None:
    recorder = _make_recorder(_make_settings(tmp_path))

    async def scenario() -> None:
        await recorder.start(ExperimentStartRequest(experiment_type="t"))
        try:
            with pytest.raises(ExperimentAlreadyRunningError):
                await recorder.start(ExperimentStartRequest(experiment_type="t2"))
        finally:
            await recorder.stop()

    asyncio.run(scenario())


def test_stop_when_idle_raises(tmp_path: Path) -> None:
    recorder = _make_recorder(_make_settings(tmp_path))

    async def scenario() -> None:
        with pytest.raises(ExperimentNotRunningError):
            await recorder.stop()

    asyncio.run(scenario())


def test_status_lifecycle(tmp_path: Path) -> None:
    recorder = _make_recorder(_make_settings(tmp_path))
    assert recorder.status().running is False

    async def scenario():
        manifest = await recorder.start(ExperimentStartRequest(experiment_type="t"))
        running = recorder.status()
        await recorder.stop()
        return manifest, running

    manifest, running = asyncio.run(scenario())
    assert running.running is True
    assert running.experiment_id == manifest.experiment_id
    assert running.elapsed_sec is not None and running.elapsed_sec >= 0.0
    assert recorder.status().running is False


def test_stop_finalizes_manifest(tmp_path: Path) -> None:
    recorder = _make_recorder(_make_settings(tmp_path))

    async def scenario():
        await recorder.start(ExperimentStartRequest(experiment_type="t"))
        await recorder._sample_once()
        return await recorder.stop()

    summary = asyncio.run(scenario())
    assert summary.manifest.ended_at is not None
    assert summary.manifest.row_counts is not None
    assert summary.manifest.row_counts["telemetry_rows"] == summary.telemetry_rows
    assert "events" in summary.manifest.row_counts
    assert summary.telemetry_rows == 8
    directory = Path(summary.directory)
    on_disk = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["ended_at"] is not None


# ---------------------------------------------------------------------------
# CSV header + row invariant
# ---------------------------------------------------------------------------


def test_csv_header_exact(tmp_path: Path) -> None:
    recorder = _make_recorder(_make_settings(tmp_path))

    async def scenario():
        await recorder.start(ExperimentStartRequest(experiment_type="t"))
        await recorder._sample_once()
        return await recorder.stop()

    summary = asyncio.run(scenario())
    header, _ = _read_csv(Path(summary.directory))
    assert header == CSV_HEADER
    assert len(header) == 37


def test_effective_target_row_invariant(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    control = _make_control(settings)
    recorder = _make_recorder(settings, control=control)
    # Base targets, including one that will clamp high at the 4095 boundary.
    bases = [1000, 4000, 500, 2048, 3000, 100, 4095, 2000]
    corrections = [50.4, 200.0, -80.6, 0.0, -3000.0, -500.0, 300.0, 12.5]

    async def scenario():
        for actuator_id, value in enumerate(bases):
            await control.set_target(
                actuator_id, SetTargetRequest(mode=ControlMode.POSITION, value=value)
            )
        await control.apply_stabilization_corrections(corrections)
        await recorder.start(ExperimentStartRequest(experiment_type="invariant"))
        await recorder._sample_once()
        return await recorder.stop()

    summary = asyncio.run(scenario())
    header, data = _read_csv(Path(summary.directory))
    idx = {
        name: header.index(name)
        for name in ("base_target", "effective_target", "stabilization_correction")
    }
    assert len(data) == 8
    for row in data:
        base = int(row[idx["base_target"]])
        correction = float(row[idx["stabilization_correction"]])
        effective = int(row[idx["effective_target"]])
        expected = max(POSITION_MIN, min(POSITION_MAX, base + round(correction)))
        assert effective == expected


def test_motion_frame_blank_when_idle(tmp_path: Path) -> None:
    recorder = _make_recorder(_make_settings(tmp_path))

    async def scenario():
        await recorder.start(ExperimentStartRequest(experiment_type="t"))
        await recorder._sample_once()
        return await recorder.stop()

    summary = asyncio.run(scenario())
    header, data = _read_csv(Path(summary.directory))
    frame_idx = header.index("motion_frame")
    assert all(row[frame_idx] == "" for row in data)


def test_attitude_none_blanks_imu_columns(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    recorder = _make_recorder(settings, sensor=_FakeSensor(attitude=None))

    async def scenario():
        await recorder.start(ExperimentStartRequest(experiment_type="t"))
        written = await recorder._sample_once()
        summary = await recorder.stop()
        return written, summary

    written, summary = asyncio.run(scenario())
    assert written == 8
    header, data = _read_csv(Path(summary.directory))
    assert len(data) == 8
    imu_cols = ["roll", "pitch", "yaw", "gyro_x", "accel_norm", "mag_x", "mag_valid",
                "accel_confidence_candidate"]
    for name in imu_cols:
        assert data[0][header.index(name)] == ""
    # control columns still populated
    assert data[0][header.index("actuator_id")] == "0"


# ---------------------------------------------------------------------------
# event teeing
# ---------------------------------------------------------------------------


def test_event_tee_selects_and_dedupes(tmp_path: Path) -> None:
    recorder = _make_recorder(_make_settings(tmp_path))
    stab_payload = {
        "stabilization": {
            "enabled": True,
            "active": True,
            "auto_disabled": False,
            "disabled_reason": None,
            "gains": {"kp_roll": 1.5, "ki_roll": 0.0, "kd_roll": 0.3,
                      "kp_pitch": 1.5, "ki_pitch": 0.0, "kd_pitch": 0.3},
        }
    }

    async def scenario() -> str:
        manifest = await recorder.start(ExperimentStartRequest(experiment_type="t"))
        recorder.observe_event(TelemetryEvent(type="telemetry", payload={"actuator": {}}))
        recorder.observe_event(
            TelemetryEvent(type="csv_playback_status", payload={"status": "running"})
        )
        for _ in range(3):
            recorder.observe_event(
                TelemetryEvent(type="stabilization_state", payload=stab_payload)
            )
        await recorder.stop()
        return manifest.experiment_id

    experiment_id = asyncio.run(scenario())
    directory = tmp_path / "Logs" / "experiments" / experiment_id
    events = _read_events(directory)
    types = [event["type"] for event in events]
    assert "telemetry" not in types
    assert types.count("csv_playback_status") == 1
    assert types.count("stabilization_state") == 1
    assert types[0] == "experiment_start"
    assert types[-1] == "experiment_stop"


def test_add_note(tmp_path: Path) -> None:
    recorder = _make_recorder(_make_settings(tmp_path))

    async def scenario():
        manifest = await recorder.start(ExperimentStartRequest(experiment_type="t"))
        result = await recorder.add_note("checkpoint reached")
        await recorder.stop()
        return manifest.experiment_id, result

    experiment_id, result = asyncio.run(scenario())
    directory = tmp_path / "Logs" / "experiments" / experiment_id
    assert result["text"] == "checkpoint reached"
    assert "checkpoint reached" in (directory / "notes.md").read_text(encoding="utf-8")
    events = _read_events(directory)
    assert any(e["type"] == "note" for e in events)


def test_add_note_when_idle_raises(tmp_path: Path) -> None:
    recorder = _make_recorder(_make_settings(tmp_path))

    async def scenario() -> None:
        with pytest.raises(ExperimentNotRunningError):
            await recorder.add_note("nope")

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("norm", "expected"),
    [
        (1.0, "high"),
        (0.93, "high"),
        (1.07, "high"),
        (1.2, "medium"),
        (0.61, "medium"),
        (1.4, "medium"),
        (1.41, "low"),
        (0.2, "low"),
    ],
)
def test_accel_confidence_bands(norm: float, expected: str) -> None:
    assert accel_confidence_candidate(norm) == expected


def test_list_and_latest_experiments_empty(tmp_path: Path) -> None:
    recorder = _make_recorder(_make_settings(tmp_path))
    assert recorder.list_experiments() == []
    assert recorder.latest_experiment() is None
