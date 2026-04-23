from highend_server.application.joint_preview import (
    JointAngleRange,
    build_leg_preview,
    leg_id_for_actuator,
    position_to_angle,
)
from highend_server.domain.models import ActuatorState, LegId, PortRole


def test_position_to_angle_maps_midpoint_into_range() -> None:
    angle = position_to_angle(2048, angle_range=JointAngleRange(min_rad=-1.0, max_rad=1.0))
    assert abs(angle) < 0.01


def test_leg_id_for_actuator_resolves_known_pairings() -> None:
    assert leg_id_for_actuator(0) is LegId.FRONT_RIGHT
    assert leg_id_for_actuator(5) is LegId.REAR_RIGHT
    assert leg_id_for_actuator(99) is None


def test_build_leg_preview_uses_front_right_hip_and_knee() -> None:
    actuators = [
        ActuatorState(actuator_id=index, label=f"a{index}", port_role=PortRole.FRONT, local_index=index % 4)
        for index in range(8)
    ]
    actuators[0].telemetry.position = 1000
    actuators[0].target_position = 1200
    actuators[1].telemetry.position = 3000
    actuators[1].target_position = 3200

    preview = build_leg_preview(LegId.FRONT_RIGHT, actuators)

    assert preview.hip.actuator_id == 0
    assert preview.knee.actuator_id == 1
    assert preview.fixed_joint_name == "rev_fr1"
    assert preview.hip.joint_name == "rev_fr2"
    assert preview.knee.joint_name == "rev_fr3"
    assert preview.mirror_x is False
    assert preview.hip.position == 1000
    assert preview.knee.position == 3000


def test_build_leg_preview_marks_left_legs_as_mirrored() -> None:
    actuators = [
        ActuatorState(actuator_id=index, label=f"a{index}", port_role=PortRole.FRONT, local_index=index % 4)
        for index in range(8)
    ]

    preview = build_leg_preview(LegId.FRONT_LEFT, actuators)

    assert preview.fixed_joint_name == "rev_fl1"
    assert preview.hip.joint_name == "rev_fl2"
    assert preview.knee.joint_name == "rev_fl3"
    assert preview.mirror_x is True
