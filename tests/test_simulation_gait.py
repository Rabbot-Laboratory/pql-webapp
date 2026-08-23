import numpy as np

from highend_server.simulation.config import GaitConfig
from highend_server.simulation.gait import RabbitBoundGait


def test_rabbit_bound_holds_neutral_during_startup() -> None:
    gait = RabbitBoundGait(GaitConfig(startup_s=1.5))

    angles, velocity = gait.targets(0.5)

    np.testing.assert_allclose(angles, 0.0, atol=1e-6)
    np.testing.assert_allclose(velocity, 0.0, atol=1e-6)


def test_rabbit_bound_front_legs_land_alternately() -> None:
    config = GaitConfig(startup_s=0.0, ramp_s=0.01, cycle_s=2.8, duty_factor=0.75)
    gait = RabbitBoundGait(config)
    offsets = [gait.foot_offset(index, config.cycle_s * 0.125) for index in (0, 1)]

    assert offsets[0][1] > 0.03
    assert abs(offsets[1][1]) < 1e-9


def test_rabbit_bound_rear_pair_kicks_in_sync() -> None:
    config = GaitConfig(startup_s=0.0, ramp_s=0.01)
    gait = RabbitBoundGait(config)

    for phase in (0.0, 0.1, 0.3, 0.7):
        right = gait.foot_offset(2, config.cycle_s * phase)
        left = gait.foot_offset(3, config.cycle_s * phase)
        np.testing.assert_allclose(right, left)


def test_rear_knees_stay_in_braced_midrange() -> None:
    config = GaitConfig(startup_s=0.0, ramp_s=0.01, rear_knee_limit_rad=0.30)
    gait = RabbitBoundGait(config)

    samples = np.asarray([gait.angles(float(time_s)) for time_s in np.linspace(0.1, 6.0, 80)])
    rear_knees = np.abs(samples[:, (5, 7)])

    assert np.max(rear_knees) <= 0.30
    assert np.max(rear_knees) > 0.15


def test_rabbit_bound_inverse_kinematics_stays_inside_model_limits() -> None:
    gait = RabbitBoundGait(GaitConfig(startup_s=0.0, ramp_s=0.01))

    for time_s in np.linspace(0.1, 6.0, 40):
        angles = gait.angles(float(time_s))
        assert np.all(np.abs(angles[0::2]) <= 0.31)
        assert np.all(np.abs(angles[1::2]) <= 0.48)


def test_imu_foot_height_correction_changes_joint_targets() -> None:
    gait = RabbitBoundGait(GaitConfig(startup_s=1.5))

    neutral = gait.angles(0.5)
    corrected = gait.angles(0.5, np.asarray((-0.01, 0.01, -0.01, 0.01)))

    assert not np.allclose(corrected, neutral)
    assert np.all(np.abs(corrected[[5, 7]]) <= gait.config.rear_knee_limit_rad)
