from __future__ import annotations

from math import radians
from dataclasses import dataclass

from highend_server.domain.models import (
    ActuatorState,
    JointPreview,
    LegId,
    LegPreview,
    POSITION_MAX,
    POSITION_MIN,
)

# 3D preview tuning knobs.
# - `*_TRAVEL_DEG` controls the visible total travel around the neutral pose.
# - `*_NEUTRAL_OFFSET_DEG` shifts the midpoint pose for a joint.
# - `*_VISUAL_OFFSET_DEG` is the final display-only correction when the model
#   still looks mechanically misaligned even though the URDF joint origin is correct.
HIP_TRAVEL_DEG = 16.0
KNEE_TRAVEL_DEG = 24.0
HIP_NEUTRAL_OFFSET_DEG = 0.0
KNEE_NEUTRAL_OFFSET_DEG = 0.0

# Keep the default pose mapping neutral and use per-leg visual offsets for
# "mechanically aligned" 3D presentation.
HIP_VISUAL_OFFSET_DEG_BY_LEG: dict[LegId, float] = {
    LegId.FRONT_RIGHT: 0.0,
    LegId.FRONT_LEFT: 0.0,
    LegId.REAR_RIGHT: 0.0,
    LegId.REAR_LEFT: 0.0,
}

KNEE_VISUAL_OFFSET_DEG_BY_LEG: dict[LegId, float] = {
    LegId.FRONT_RIGHT: 0.0,
    LegId.FRONT_LEFT: 0.0,
    LegId.REAR_RIGHT: 0.0,
    LegId.REAR_LEFT: 0.0,
}


@dataclass(frozen=True, slots=True)
class JointAngleRange:
    min_rad: float
    max_rad: float
    neutral_rad: float = 0.0
    visual_offset_rad: float = 0.0
    direction: float = 1.0


@dataclass(frozen=True, slots=True)
class LegLayout:
    leg_id: LegId
    label: str
    fixed_joint_name: str
    hip_index: int
    knee_index: int
    hip_joint_name: str
    knee_joint_name: str
    mirror_x: bool
    hip_range: JointAngleRange
    knee_range: JointAngleRange


def symmetric_joint_range(
    travel_deg: float,
    *,
    neutral_offset_deg: float = 0.0,
    visual_offset_deg: float = 0.0,
    direction: float = 1.0,
) -> JointAngleRange:
    half_travel = radians(travel_deg)
    return JointAngleRange(
        min_rad=-half_travel,
        max_rad=half_travel,
        neutral_rad=radians(neutral_offset_deg),
        visual_offset_rad=radians(visual_offset_deg),
        direction=direction,
    )


LEG_LAYOUTS: tuple[LegLayout, ...] = (
    LegLayout(
        leg_id=LegId.FRONT_RIGHT,
        label="Front Right",
        fixed_joint_name="rev_fr1",
        hip_index=0,
        knee_index=1,
        hip_joint_name="rev_fr2",
        knee_joint_name="rev_fr3",
        mirror_x=False,
        hip_range=symmetric_joint_range(
            HIP_TRAVEL_DEG,
            neutral_offset_deg=HIP_NEUTRAL_OFFSET_DEG,
            visual_offset_deg=HIP_VISUAL_OFFSET_DEG_BY_LEG[LegId.FRONT_RIGHT],
            direction=-1.0,
        ),
        knee_range=symmetric_joint_range(
            KNEE_TRAVEL_DEG,
            neutral_offset_deg=KNEE_NEUTRAL_OFFSET_DEG,
            visual_offset_deg=KNEE_VISUAL_OFFSET_DEG_BY_LEG[LegId.FRONT_RIGHT],
        ),
    ),
    LegLayout(
        leg_id=LegId.FRONT_LEFT,
        label="Front Left",
        fixed_joint_name="rev_fl1",
        hip_index=2,
        knee_index=3,
        hip_joint_name="rev_fl2",
        knee_joint_name="rev_fl3",
        mirror_x=True,
        hip_range=symmetric_joint_range(
            HIP_TRAVEL_DEG,
            neutral_offset_deg=HIP_NEUTRAL_OFFSET_DEG,
            visual_offset_deg=HIP_VISUAL_OFFSET_DEG_BY_LEG[LegId.FRONT_LEFT],
            direction=-1.0,
        ),
        knee_range=symmetric_joint_range(
            KNEE_TRAVEL_DEG,
            neutral_offset_deg=KNEE_NEUTRAL_OFFSET_DEG,
            visual_offset_deg=KNEE_VISUAL_OFFSET_DEG_BY_LEG[LegId.FRONT_LEFT],
        ),
    ),
    LegLayout(
        leg_id=LegId.REAR_RIGHT,
        label="Rear Right",
        fixed_joint_name="rev_rr1",
        hip_index=4,
        knee_index=5,
        hip_joint_name="rev_rr2",
        knee_joint_name="rev_rr3",
        mirror_x=False,
        hip_range=symmetric_joint_range(
            HIP_TRAVEL_DEG,
            neutral_offset_deg=HIP_NEUTRAL_OFFSET_DEG,
            visual_offset_deg=HIP_VISUAL_OFFSET_DEG_BY_LEG[LegId.REAR_RIGHT],
            direction=-1.0,
        ),
        knee_range=symmetric_joint_range(
            KNEE_TRAVEL_DEG,
            neutral_offset_deg=KNEE_NEUTRAL_OFFSET_DEG,
            visual_offset_deg=KNEE_VISUAL_OFFSET_DEG_BY_LEG[LegId.REAR_RIGHT],
        ),
    ),
    LegLayout(
        leg_id=LegId.REAR_LEFT,
        label="Rear Left",
        fixed_joint_name="rev_rl1",
        hip_index=6,
        knee_index=7,
        hip_joint_name="rev_rl2",
        knee_joint_name="rev_rl3",
        mirror_x=True,
        hip_range=symmetric_joint_range(
            HIP_TRAVEL_DEG,
            neutral_offset_deg=HIP_NEUTRAL_OFFSET_DEG,
            visual_offset_deg=HIP_VISUAL_OFFSET_DEG_BY_LEG[LegId.REAR_LEFT],
            direction=-1.0,
        ),
        knee_range=symmetric_joint_range(
            KNEE_TRAVEL_DEG,
            neutral_offset_deg=KNEE_NEUTRAL_OFFSET_DEG,
            visual_offset_deg=KNEE_VISUAL_OFFSET_DEG_BY_LEG[LegId.REAR_LEFT],
        ),
    ),
)

LEG_LAYOUT_BY_ID = {layout.leg_id: layout for layout in LEG_LAYOUTS}


def position_to_angle(position: int, angle_range: JointAngleRange) -> float:
    clamped = max(POSITION_MIN, min(POSITION_MAX, position))
    midpoint = (POSITION_MIN + POSITION_MAX) / 2

    if clamped <= midpoint:
        ratio = (clamped - POSITION_MIN) / max(midpoint - POSITION_MIN, 1)
        mapped = angle_range.min_rad + ratio * (angle_range.neutral_rad - angle_range.min_rad)
        return mapped * angle_range.direction + angle_range.visual_offset_rad

    ratio = (clamped - midpoint) / max(POSITION_MAX - midpoint, 1)
    mapped = angle_range.neutral_rad + ratio * (angle_range.max_rad - angle_range.neutral_rad)
    return mapped * angle_range.direction + angle_range.visual_offset_rad


def leg_id_for_actuator(actuator_id: int) -> LegId | None:
    for layout in LEG_LAYOUTS:
        if actuator_id in (layout.hip_index, layout.knee_index):
            return layout.leg_id
    return None


def build_leg_previews(actuators: list[ActuatorState]) -> list[LegPreview]:
    return [build_leg_preview(layout.leg_id, actuators) for layout in LEG_LAYOUTS]


def build_leg_preview(leg_id: LegId, actuators: list[ActuatorState]) -> LegPreview:
    layout = LEG_LAYOUT_BY_ID[leg_id]
    hip = actuators[layout.hip_index]
    knee = actuators[layout.knee_index]
    updated_at = max(hip.updated_at, knee.updated_at)

    return LegPreview(
        leg_id=layout.leg_id,
        label=layout.label,
        fixed_joint_name=layout.fixed_joint_name,
        fixed_joint_angle_rad=0.0,
        mirror_x=layout.mirror_x,
        hip=_build_joint_preview(hip, layout.hip_joint_name, layout.hip_range),
        knee=_build_joint_preview(knee, layout.knee_joint_name, layout.knee_range),
        updated_at=updated_at,
    )


def _build_joint_preview(actuator: ActuatorState, joint_name: str, angle_range: JointAngleRange) -> JointPreview:
    return JointPreview(
        actuator_id=actuator.actuator_id,
        label=actuator.label,
        joint_name=joint_name,
        position=actuator.telemetry.position,
        angle_rad=position_to_angle(actuator.telemetry.position, angle_range),
        target_position=actuator.target_position,
        target_angle_rad=position_to_angle(actuator.target_position, angle_range),
        command=actuator.telemetry.command,
    )
