"""FastAPI route-wiring tests for the experiment-logging API.

Exercises the ASGI stack end to end: dependency resolution via app.state,
response_model serialization, and the HTTP error mapping (409 already-running /
not-running, 404 latest-when-empty). Recorder behaviour itself is covered by
test_experiment_recorder.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from highend_server.api.routes import router
from highend_server.application.control_service import ControlService
from highend_server.application.experiment import ExperimentRecorder
from highend_server.application.stabilization import StabilizationController
from highend_server.config import Settings
from highend_server.sensors.sensor_service import SensorService
from highend_server.transport.serial_gateway import StubSerialGateway


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        emulate_devices=False,
        sensors_enabled=False,
        telemetry_log_root_dir=str(tmp_path / "Logs"),
        sensor_config_dir_name=str(tmp_path / "config"),
        experiment_sample_rate_hz=25.0,
        experiment_flush_interval_sec=0.1,
    )


def _make_app(settings: Settings) -> FastAPI:
    async def event_sink(event: object) -> None:
        return None

    control_service = ControlService(
        settings=settings, gateway=StubSerialGateway(settings), event_sink=event_sink
    )
    sensor_service = SensorService(settings=settings, event_sink=event_sink)
    control_service.set_attitude_provider(sensor_service.latest_attitude)
    control_service.set_level_offsets_provider(sensor_service.level_offsets)
    stabilization_controller = StabilizationController(
        settings=settings,
        control_service=control_service,
        attitude_provider=sensor_service.latest_attitude,
        level_offsets_provider=sensor_service.level_offsets,
        event_sink=event_sink,
    )
    experiment_recorder = ExperimentRecorder(settings=settings)
    experiment_recorder.bind(
        control_service=control_service,
        stabilization_controller=stabilization_controller,
        sensor_service=sensor_service,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await control_service.connect()
        await sensor_service.start()
        await stabilization_controller.start()
        try:
            yield
        finally:
            await experiment_recorder.shutdown()
            await stabilization_controller.stop()
            await sensor_service.stop()
            await control_service.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.control_service = control_service
    app.state.sensor_service = sensor_service
    app.state.stabilization_controller = stabilization_controller
    app.state.experiment_recorder = experiment_recorder
    app.include_router(router, prefix="/api")
    return app


@pytest.fixture()
def client(tmp_path: Path):
    app = _make_app(_make_settings(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def test_start_returns_manifest(client: TestClient) -> None:
    response = client.post("/api/experiments/start", json={"experiment_type": "gait"})
    assert response.status_code == 200
    body = response.json()
    assert body["experiment_type"] == "gait"
    assert body["experiment_id"].endswith("_gait")
    assert "config_snapshot" in body
    assert set(body["stabilization"]["gains"]) == {
        "kp_roll", "ki_roll", "kd_roll", "kp_pitch", "ki_pitch", "kd_pitch"
    }
    client.post("/api/experiments/stop")


def test_second_start_conflicts(client: TestClient) -> None:
    assert client.post("/api/experiments/start", json={"experiment_type": "a"}).status_code == 200
    conflict = client.post("/api/experiments/start", json={"experiment_type": "b"})
    assert conflict.status_code == 409
    client.post("/api/experiments/stop")


def test_stop_returns_summary(client: TestClient) -> None:
    client.post("/api/experiments/start", json={"experiment_type": "a"})
    response = client.post("/api/experiments/stop")
    assert response.status_code == 200
    body = response.json()
    assert "manifest" in body
    assert body["manifest"]["ended_at"] is not None
    assert "telemetry_rows" in body
    assert "telemetry_bytes" in body


def test_second_stop_conflicts(client: TestClient) -> None:
    client.post("/api/experiments/start", json={"experiment_type": "a"})
    assert client.post("/api/experiments/stop").status_code == 200
    assert client.post("/api/experiments/stop").status_code == 409


def test_note_ok_and_conflict(client: TestClient) -> None:
    # 409 when idle
    idle = client.post("/api/experiments/note", json={"text": "hi"})
    assert idle.status_code == 409
    # 200 while running
    client.post("/api/experiments/start", json={"experiment_type": "a"})
    ok = client.post("/api/experiments/note", json={"text": "checkpoint"})
    assert ok.status_code == 200
    assert ok.json()["text"] == "checkpoint"
    client.post("/api/experiments/stop")


def test_list_experiments(client: TestClient) -> None:
    assert client.get("/api/experiments").json() == {"experiments": []}
    client.post("/api/experiments/start", json={"experiment_type": "a"})
    client.post("/api/experiments/stop")
    listing = client.get("/api/experiments")
    assert listing.status_code == 200
    experiments = listing.json()["experiments"]
    assert len(experiments) == 1
    assert experiments[0]["experiment_type"] == "a"


def test_latest_404_when_empty(client: TestClient) -> None:
    assert client.get("/api/experiments/latest").status_code == 404
    client.post("/api/experiments/start", json={"experiment_type": "a"})
    client.post("/api/experiments/stop")
    latest = client.get("/api/experiments/latest")
    assert latest.status_code == 200
    assert latest.json()["experiment_type"] == "a"
