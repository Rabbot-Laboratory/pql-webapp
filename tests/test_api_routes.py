"""FastAPI route-wiring tests for the stabilization and IMU calibration API.

The service behaviour itself is covered by test_stabilization.py /
test_sensor_service.py; these tests exercise the ASGI stack end to end:
dependency resolution via app.state, response_model serialization, and the
HTTP error-code mapping (422 validation, 400 bad mag fit, 409 pipeline
unavailable / stabilization interlock).
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import highend_server.api.routes as routes_module
from highend_server.api.routes import router
from highend_server.application.adaptive_walking import AdaptiveWalkingController
from highend_server.application.control_service import ControlService
from highend_server.application.experiment import ExperimentRecorder
from highend_server.application.hardware_status import HardwareStatusService
from highend_server.application.stabilization import StabilizationController
from highend_server.application.standing import StandingController
from highend_server.config import Settings
from highend_server.input.gamepad_service import GamepadService
from highend_server.sensors.sensor_service import SensorService
from highend_server.transport.serial_gateway import StubSerialGateway


def _make_settings(tmp_path: Path, *, emulate_devices: bool) -> Settings:
    # The emulated IMU pipeline runs when emulate_devices=True and
    # sensors_enabled=False (sensors_enabled=True means real I2C hardware).
    # With both False the sensor service starts nothing (pipeline is None).
    # sensor_config_dir_name accepts an absolute path (Path join semantics),
    # which keeps calibration JSON writes inside tmp_path instead of the repo.
    return Settings(
        emulate_devices=emulate_devices,
        sensors_enabled=False,
        adaptive_walk_require_standing=False,
        sensor_config_dir_name=str(tmp_path / "config"),
        telemetry_log_root_dir=str(tmp_path / "Logs"),
        sensor_publish_interval_sec=0.02,
        imu_sample_rate_hz=200.0,
        stabilization_rate_hz=50.0,
    )


def _make_app(settings: Settings) -> FastAPI:
    async def event_sink(event: object) -> None:
        return None

    control_service = ControlService(
        settings=settings, gateway=StubSerialGateway(settings), event_sink=event_sink
    )
    sensor_service = SensorService(settings=settings, event_sink=event_sink)
    gamepad_service = GamepadService(settings=settings, event_sink=event_sink)
    hardware_status_service = HardwareStatusService(
        settings=settings,
        gateway=control_service.gateway,
        sensor_service=sensor_service,
        event_sink=event_sink,
    )
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
    standing_controller = StandingController(
        settings=settings,
        control_service=control_service,
        attitude_provider=sensor_service.latest_attitude,
        level_offsets_provider=sensor_service.level_offsets,
        event_sink=event_sink,
        stabilization_engaged=lambda: stabilization_controller.enabled,
    )
    adaptive_walking_controller = AdaptiveWalkingController(
        settings=settings,
        control_service=control_service,
        attitude_provider=sensor_service.latest_attitude,
        level_offsets_provider=sensor_service.level_offsets,
        event_sink=event_sink,
        stabilization_engaged=lambda: stabilization_controller.enabled,
        contact_provider=sensor_service.latest_contact,
        experiment_recorder=experiment_recorder,
        standing_controller=standing_controller,
    )
    experiment_recorder.bind(
        control_service=control_service,
        stabilization_controller=stabilization_controller,
        sensor_service=sensor_service,
        adaptive_walking=adaptive_walking_controller,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await control_service.connect()
        await sensor_service.start()
        await gamepad_service.start()
        await hardware_status_service.start()
        await stabilization_controller.start()
        await standing_controller.start()
        await adaptive_walking_controller.start()
        try:
            yield
        finally:
            await experiment_recorder.shutdown()
            await adaptive_walking_controller.stop()
            await standing_controller.stop()
            await stabilization_controller.stop()
            await gamepad_service.stop()
            await hardware_status_service.stop()
            await sensor_service.stop()
            await control_service.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.control_service = control_service
    app.state.sensor_service = sensor_service
    app.state.gamepad_service = gamepad_service
    app.state.hardware_status_service = hardware_status_service
    app.state.stabilization_controller = stabilization_controller
    app.state.standing_controller = standing_controller
    app.state.adaptive_walking_controller = adaptive_walking_controller
    app.state.experiment_recorder = experiment_recorder
    app.include_router(router, prefix="/api")
    return app


@pytest.fixture()
def client(tmp_path: Path):
    app = _make_app(_make_settings(tmp_path, emulate_devices=True))
    with TestClient(app) as test_client:
        # Give the emulated 200 Hz IMU thread a moment to produce a snapshot
        # so calibration endpoints have attitude data to work with.
        time.sleep(0.1)
        yield test_client


@pytest.fixture()
def client_without_sensors(tmp_path: Path):
    app = _make_app(_make_settings(tmp_path, emulate_devices=False))
    with TestClient(app) as test_client:
        yield test_client


def test_health_responds(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "highend-control-server"
    assert "system" in body
    assert body["robot_ready"] is True


def test_system_info_reports_wifi_and_ethernet(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        routes_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps(
                [
                    {
                        "ifname": "eth0",
                        "addr_info": [],
                    },
                    {
                        "ifname": "wlan0",
                        "addr_info": [
                            {"family": "inet", "scope": "global", "local": "192.168.1.21"}
                        ],
                    },
                    {
                        "ifname": "tailscale0",
                        "addr_info": [{"family": "inet", "scope": "global", "local": "100.64.0.1"}],
                    },
                ]
            )
        ),
    )

    response = client.get("/api/system/info")

    assert response.status_code == 200
    assert response.json() == {
        "network_interfaces": [
            {"interface": "wlan0", "kind": "wifi", "address": "192.168.1.21"},
            {"interface": "eth0", "kind": "ethernet", "address": None},
        ]
    }


def test_system_power_requires_confirmation_and_uses_fixed_command(
    client: TestClient, monkeypatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        routes_module.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )

    denied = client.post("/api/system/power", json={"action": "reboot", "confirmed": False})
    accepted = client.post("/api/system/power", json={"action": "reboot", "confirmed": True})

    assert denied.status_code == 400
    assert accepted.status_code == 200
    assert accepted.json() == {"ok": True, "action": "reboot"}
    assert calls == [["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "--no-block", "reboot"]]


def test_home_requires_confirmation_and_starts_ramp(client: TestClient) -> None:
    denied = client.post("/api/control/home", json={"safety_confirmed": False})
    assert denied.status_code == 400

    fixed_path = client.app.state.settings.fixed_motion_path
    fixed_path.mkdir(parents=True, exist_ok=True)
    (fixed_path / "home.csv").write_text(
        "# interval_sec=0.04\n2048,2048,2048,2048,2048,2048,2048,2048\n",
        encoding="utf-8",
    )
    started = client.post("/api/control/home", json={"safety_confirmed": True})
    assert started.status_code == 200
    assert started.json() == {"ok": True}


def test_get_sensors_shape(client: TestClient) -> None:
    response = client.get("/api/sensors")
    assert response.status_code == 200
    imu = response.json()["item"]["imu"]
    assert "quaternion" in imu
    assert "mag_calibration_active" in imu


def test_get_gamepad_shape(client: TestClient) -> None:
    response = client.get("/api/gamepad")
    assert response.status_code == 200
    state = response.json()["item"]
    assert state["source"] == "none"
    assert state["connected"] is False
    assert state["deadman"] is False


def test_get_hardware_status_shape(client: TestClient) -> None:
    response = client.get("/api/hardware")
    assert response.status_code == 200
    state = response.json()["item"]
    assert state["server_ok"] is True
    assert state["robot_ready"] is True
    assert {device["device_id"] for device in state["devices"]} == {
        "esp32_front",
        "esp32_back",
        "imu_bmx055",
        "contact_adc",
    }


def test_get_stabilization_state_shape(client: TestClient) -> None:
    response = client.get("/api/control/stabilization")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert set(body["gains"]) == {
        "kp_roll", "ki_roll", "kd_roll", "kp_pitch", "ki_pitch", "kd_pitch",
    }
    assert len(body["corrections"]) == 8


def test_post_stabilization_enable_disable_roundtrip(client: TestClient) -> None:
    enabled = client.post("/api/control/stabilization", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    disabled = client.post("/api/control/stabilization", json={"enabled": False})
    assert disabled.status_code == 200
    body = disabled.json()
    assert body["enabled"] is False
    assert body["active"] is False


def test_post_stabilization_rejects_invalid_gains(client: TestClient) -> None:
    response = client.post(
        "/api/control/stabilization",
        json={"gains": {
            "kp_roll": -1.0, "ki_roll": 0.0, "kd_roll": 0.0,
            "kp_pitch": 1.0, "ki_pitch": 0.0, "kd_pitch": 0.0,
        }},
    )
    assert response.status_code == 422


def test_adaptive_forward_requires_confirmation_and_releases(client: TestClient) -> None:
    rejected = client.post(
        "/api/control/adaptive-walk/forward",
        json={"pressed": True, "safety_confirmed": False},
    )
    assert rejected.status_code == 400

    started = client.post(
        "/api/control/adaptive-walk/forward",
        json={"pressed": True, "safety_confirmed": True},
    )
    assert started.status_code == 200
    assert started.json()["active"] is True

    stopped = client.post(
        "/api/control/adaptive-walk/forward",
        json={"pressed": False, "safety_confirmed": False},
    )
    assert stopped.status_code == 200
    assert stopped.json()["active"] is False


def test_calibration_blocked_while_stabilization_enabled(client: TestClient) -> None:
    assert client.post("/api/control/stabilization", json={"enabled": True}).status_code == 200

    blocked = client.post("/api/sensors/imu/calibration/level")
    assert blocked.status_code == 409

    assert client.post("/api/control/stabilization", json={"enabled": False}).status_code == 200
    # Wait out the disable ramp so the controller reports fully idle.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        state = client.get("/api/control/stabilization").json()
        if not state["enabled"] and not state["active"]:
            break
        time.sleep(0.05)

    allowed = client.post("/api/sensors/imu/calibration/level")
    assert allowed.status_code == 200


def test_mag_calibration_start_bad_finish_and_cancel(client: TestClient) -> None:
    start = client.post("/api/sensors/imu/calibration/mag/start")
    assert start.status_code == 200
    assert start.json()["item"]["imu"]["mag_calibration_active"] is True

    # Immediately finishing cannot have collected enough samples -> 400.
    finish = client.post("/api/sensors/imu/calibration/mag/finish")
    assert finish.status_code == 400

    restart = client.post("/api/sensors/imu/calibration/mag/start")
    assert restart.status_code == 200
    cancel = client.post("/api/sensors/imu/calibration/mag/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["item"]["imu"]["mag_calibration_active"] is False


def test_mag_calibration_conflict_when_sensors_disabled(
    client_without_sensors: TestClient,
) -> None:
    start = client_without_sensors.post("/api/sensors/imu/calibration/mag/start")
    assert start.status_code == 409


def test_contact_calibration_roundtrip(client: TestClient) -> None:
    initial = client.get("/api/sensors/contact-calibration")
    assert initial.status_code == 200
    calibration = initial.json()["item"]
    assert {leg["leg"] for leg in calibration["legs"]} == {
        "front_right",
        "front_left",
        "rear_right",
        "rear_left",
    }

    for leg in calibration["legs"]:
        leg["on_threshold"] = 2500
        leg["off_threshold"] = 2100
    calibration["debounce_ticks"] = 3
    update = client.put("/api/sensors/contact-calibration", json=calibration)
    assert update.status_code == 200
    assert len(update.json()["item"]["contact"]) == 4

    persisted = client.get("/api/sensors/contact-calibration").json()["item"]
    assert persisted["debounce_ticks"] == 3
    assert all(leg["on_threshold"] == 2500 for leg in persisted["legs"])


def test_contact_calibration_rejects_inconsistent_thresholds(client: TestClient) -> None:
    calibration = client.get("/api/sensors/contact-calibration").json()["item"]
    calibration["legs"][0]["on_threshold"] = 100
    calibration["legs"][0]["off_threshold"] = 3000
    response = client.put("/api/sensors/contact-calibration", json=calibration)
    assert response.status_code == 422


def test_sensor_state_includes_contact_legs(client: TestClient) -> None:
    response = client.get("/api/sensors")
    assert response.status_code == 200
    contact = response.json()["item"]["contact"]
    assert len(contact) == 4
    assert {entry["leg"] for entry in contact} == {
        "front_right",
        "front_left",
        "rear_right",
        "rear_left",
    }
    assert all(isinstance(entry["supporting"], bool) for entry in contact)


def test_adaptive_forward_accepts_cycles_and_mode(client: TestClient) -> None:
    started = client.post(
        "/api/control/adaptive-walk/forward",
        json={
            "pressed": True,
            "safety_confirmed": True,
            "cycles": 3,
            "mode": "replay",
        },
    )
    assert started.status_code == 200
    body = started.json()
    assert body["active"] is True
    assert body["mode"] == "replay"
    assert body["target_cycles"] == 3
    assert body["cycle_count"] == 0

    stopped = client.post(
        "/api/control/adaptive-walk/forward",
        json={"pressed": False, "safety_confirmed": False},
    )
    assert stopped.status_code == 200

    invalid = client.post(
        "/api/control/adaptive-walk/forward",
        json={"pressed": True, "safety_confirmed": True, "cycles": 99},
    )
    assert invalid.status_code == 422


def _write_home_csv(client: TestClient) -> None:
    fixed_path = client.app.state.settings.fixed_motion_path
    fixed_path.mkdir(parents=True, exist_ok=True)
    (fixed_path / "home.csv").write_text(
        "# interval_sec=0.04\n2048,2048,2048,2048,2048,2048,2048,2048\n",
        encoding="utf-8",
    )


def test_standing_roundtrip_and_interlocks(client: TestClient) -> None:
    _write_home_csv(client)

    denied = client.post("/api/control/standing", json={"enabled": True})
    assert denied.status_code == 400  # safety confirmation required

    started = client.post(
        "/api/control/standing", json={"enabled": True, "safety_confirmed": True}
    )
    assert started.status_code == 200
    body = started.json()
    assert body["enabled"] is True
    assert body["phase"] in ("rising", "holding")

    fetched = client.get("/api/control/standing")
    assert fetched.status_code == 200
    assert fetched.json()["enabled"] is True

    # Standing blocks stabilization, Home, and manual targets.
    stab = client.post("/api/control/stabilization", json={"enabled": True})
    assert stab.status_code == 409
    home = client.post("/api/control/home", json={"safety_confirmed": True})
    assert home.status_code == 409
    manual = client.post(
        "/api/actuators/0/target", json={"mode": "position", "value": 2000}
    )
    assert manual.status_code == 409

    stopped = client.post("/api/control/standing", json={"enabled": False})
    assert stopped.status_code == 200
    assert stopped.json()["enabled"] is False


def test_stabilization_blocks_standing(client: TestClient) -> None:
    _write_home_csv(client)
    enable = client.post("/api/control/stabilization", json={"enabled": True})
    assert enable.status_code == 200
    try:
        standing = client.post(
            "/api/control/standing", json={"enabled": True, "safety_confirmed": True}
        )
        assert standing.status_code == 409
    finally:
        client.post("/api/control/stabilization", json={"enabled": False})


def test_standing_manual_ok_route(client: TestClient) -> None:
    _write_home_csv(client)

    rejected = client.post(
        "/api/control/standing", json={"enabled": True, "manual_ok": True}
    )
    assert rejected.status_code == 400  # safety confirmation still required

    started = client.post(
        "/api/control/standing",
        json={"enabled": True, "safety_confirmed": True, "manual_ok": True},
    )
    assert started.status_code == 200
    body = started.json()
    assert body["manual_ok"] is True
    assert body["standing_ok"] is True

    revoked = client.post(
        "/api/control/standing", json={"enabled": True, "manual_ok": False}
    )
    assert revoked.status_code == 200
    assert revoked.json()["manual_ok"] is False

    client.post("/api/control/standing", json={"enabled": True, "manual_ok": True})
    stopped = client.post("/api/control/standing", json={"enabled": False})
    assert stopped.status_code == 200
    assert stopped.json()["manual_ok"] is False
