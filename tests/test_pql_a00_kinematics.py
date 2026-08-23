from __future__ import annotations

from highend_server.application.pql_a00_kinematics import (
    ACTUATOR_HEIGHT_EFFECTS,
    leg_height_deltas,
    model_derived_mixing_matrix,
)
from highend_server.domain.models import LegId


def _corrections(*, roll_output: float = 0.0, pitch_output: float = 0.0) -> list[float]:
    return [
        row[0] * roll_output + row[1] * pitch_output
        for row in model_derived_mixing_matrix()
    ]


def test_model_effects_cover_the_eight_controlled_urdf_joints() -> None:
    assert [effect.actuator_id for effect in ACTUATOR_HEIGHT_EFFECTS] == list(range(8))
    assert [effect.joint_name for effect in ACTUATOR_HEIGHT_EFFECTS] == [
        "rev_fr2",
        "rev_fr3",
        "rev_fl2",
        "rev_fl3",
        "rev_rr2",
        "rev_rr3",
        "rev_rl2",
        "rev_rl3",
    ]


def test_model_mixing_positive_control_roll_raises_right_for_negative_pid_output() -> None:
    # Positive control Roll means right-side-down.  Its PID error/output is
    # negative; the resulting target signs must raise the right model feet.
    heights = leg_height_deltas(_corrections(roll_output=-1.0))
    assert heights[LegId.FRONT_RIGHT] > 0.0
    assert heights[LegId.REAR_RIGHT] > 0.0
    assert heights[LegId.FRONT_LEFT] < 0.0
    assert heights[LegId.REAR_LEFT] < 0.0


def test_model_mixing_positive_control_pitch_raises_rear_for_negative_pid_output() -> None:
    # Positive control Pitch means nose-up.  Its PID error/output is negative;
    # the resulting target signs must raise the rear model feet.
    heights = leg_height_deltas(_corrections(pitch_output=-1.0))
    assert heights[LegId.REAR_RIGHT] > 0.0
    assert heights[LegId.REAR_LEFT] > 0.0
    assert heights[LegId.FRONT_RIGHT] < 0.0
    assert heights[LegId.FRONT_LEFT] < 0.0
