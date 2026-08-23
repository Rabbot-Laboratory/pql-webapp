from math import cos, radians, sin

import numpy as np

from highend_server.simulation.config import AdaptiveAttitudeControlConfig, ImuConfig
from highend_server.simulation.imu_control import (
    AdaptiveImuGaitController,
    ImuSample,
    SimulatedImu,
)


def test_virtual_imu_delays_orientation_sample() -> None:
    imu = SimulatedImu(
        ImuConfig(
            sample_rate_hz=100.0,
            delay_s=0.02,
            orientation_noise_std_deg=0.0,
            gyro_noise_std_deg_s=0.0,
            roll_bias_deg=0.0,
            pitch_bias_deg=0.0,
        )
    )
    roll = 0.1
    quaternion = np.asarray((cos(roll / 2.0), sin(roll / 2.0), 0.0, 0.0))

    initial = imu.update(0.0, quaternion, np.zeros(3))
    delayed = imu.update(0.021, quaternion, np.zeros(3))

    assert initial.roll_rad == 0.0
    assert abs(delayed.roll_rad - roll) < 1e-9


def test_positive_roll_extends_front_and_retracts_rear_legs() -> None:
    controller = AdaptiveImuGaitController(
        AdaptiveAttitudeControlConfig(kp=0.5, kd_s=0.0, adaptation_rate=0.0)
    )

    correction = controller.update(
        ImuSample(0.0, radians(5.0), 0.0, 0.0, 0.0), dt=0.01
    )

    assert np.all(correction.foot_height_m[:2] < 0.0)
    assert np.all(correction.foot_height_m[2:] > 0.0)


def test_attitude_controller_learns_bounded_persistent_trim() -> None:
    config = AdaptiveAttitudeControlConfig(
        kp=0.0,
        kd_s=0.0,
        adaptation_rate=1.0,
        trim_leak_rate=0.0,
        adaptation_deadband_deg=0.0,
        max_trim_deg=2.0,
    )
    controller = AdaptiveImuGaitController(config)
    sample = ImuSample(0.0, radians(5.0), radians(-5.0), 0.0, 0.0)

    for _ in range(100):
        controller.update(sample, dt=0.01)

    roll_trim, pitch_trim = controller.trim_degrees()
    assert 0.0 < roll_trim <= 2.0
    assert -2.0 <= pitch_trim < 0.0


def test_disabled_attitude_controller_makes_no_gait_correction() -> None:
    controller = AdaptiveImuGaitController(AdaptiveAttitudeControlConfig(enabled=False))

    correction = controller.update(
        ImuSample(0.0, radians(10.0), radians(10.0), 1.0, 1.0), dt=0.01
    )

    np.testing.assert_array_equal(correction.foot_height_m, np.zeros(4))
