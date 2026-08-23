from highend_server.simulation.actuator import (
    AdaptiveTrackingController,
    PneumaticActuator,
)
from highend_server.simulation.config import (
    AdaptiveControlConfig,
    PneumaticActuatorConfig,
)


def actuator_config() -> PneumaticActuatorConfig:
    return PneumaticActuatorConfig(
        delay_s=0.05,
        extend_speed_rad_s=0.5,
        retract_speed_rad_s=1.0,
        time_constant_s=0.1,
        deadband_rad=0.001,
        kp_nm_rad=100.0,
        kd_nm_s_rad=5.0,
        max_torque_nm=20.0,
    )


def test_pneumatic_actuator_applies_delay_and_rate_limit() -> None:
    actuator = PneumaticActuator(actuator_config())
    actuator.command(1.0, now_s=0.0)

    actuator.update(now_s=0.04, dt=0.01, joint_angle_rad=0.0, joint_velocity=0.0)
    assert actuator.pressure_target_rad == 0.0

    actuator.update(now_s=0.06, dt=0.01, joint_angle_rad=0.0, joint_velocity=0.0)
    assert actuator.pressure_target_rad == 0.005


def test_pneumatic_actuator_has_asymmetric_retraction_speed() -> None:
    actuator = PneumaticActuator(actuator_config(), pressure_target_rad=0.5)
    actuator.delayed_target_rad = -0.5

    actuator.update(now_s=0.0, dt=0.01, joint_angle_rad=0.5, joint_velocity=0.0)

    assert actuator.pressure_target_rad == 0.49


def test_adaptive_controller_learns_positive_phase_lead_when_joint_lags() -> None:
    controller = AdaptiveTrackingController(
        AdaptiveControlConfig(
            enabled=True,
            learning_rate=1.0,
            initial_lead_s=0.0,
            max_lead_s=0.3,
            feedback_gain=0.0,
        )
    )

    for _ in range(100):
        controller.command(
            desired_angle=0.2,
            desired_velocity=0.5,
            measured_angle=0.1,
            dt=0.01,
        )

    assert controller.lead_s is not None
    assert 0.0 < controller.lead_s <= 0.3
