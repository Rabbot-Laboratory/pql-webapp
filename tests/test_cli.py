from __future__ import annotations

import sys

import highend_server.__main__ as cli
import highend_server.main as server_main


def test_demo_defaults_to_stationary_imu(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["highend_server", "--demo"])
    monkeypatch.delenv("HIGHEND_EMULATED_IMU_SCENARIO", raising=False)
    monkeypatch.setattr(server_main, "run", lambda: None)

    cli.main()

    assert cli.os.environ["HIGHEND_EMULATE_DEVICES"] == "true"
    assert cli.os.environ["HIGHEND_EMULATED_IMU_SCENARIO"] == "static"


def test_demo_preserves_explicit_imu_scenario(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["highend_server", "--demo"])
    monkeypatch.setenv("HIGHEND_EMULATED_IMU_SCENARIO", "roll-step")
    monkeypatch.setattr(server_main, "run", lambda: None)

    cli.main()

    assert cli.os.environ["HIGHEND_EMULATED_IMU_SCENARIO"] == "roll-step"
