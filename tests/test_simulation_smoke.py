import pytest

pytest.importorskip("mujoco")

from highend_server.simulation.config import load_simulation_config
from highend_server.simulation.runner import PqlA00Simulation


def test_mujoco_model_runs_without_falling_during_startup() -> None:
    simulation = PqlA00Simulation(load_simulation_config())

    result = simulation.run(0.2, quiet=True)

    assert result.duration_s >= 0.2
    assert result.final_base_height_m > 0.2
    assert result.fallen is False
    assert abs(result.learned_roll_trim_deg) < 1.0
    assert abs(result.learned_pitch_trim_deg) < 1.0
