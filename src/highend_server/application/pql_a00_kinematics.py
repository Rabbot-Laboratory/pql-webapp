"""PQL-A00 model-derived actuator height effects and feedback mixing.

This module is the control-side counterpart of the 3D model.  It deliberately
contains *generated constants* rather than parsing URDF/STL files at runtime:
the Raspberry Pi control service must not depend on CAD assets being deployed.

Derivation inputs (all in this repository):

* ``pql-a00_description/urdf/pql-a00.xacro`` joint origins and axes;
* lower-leg STL contact vertex at the neutral pose;
* ``joint_preview.position_to_angle`` target-to-joint mapping;
* the confirmed hardware convention: increasing a position target extends the
  corresponding pneumatic cylinder.

At neutral target 2048, finite differencing the lower-leg contact vertex gives
the signed vertical sensitivity in metres per target unit below.  Only its
*sign* is used for feedback mixing.  Its magnitude is retained as diagnostic
metadata, but must not be treated as a calibrated pneumatic gain.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import copysign

from highend_server.domain.models import LegId


@dataclass(frozen=True, slots=True)
class ActuatorHeightEffect:
    """Neutral-pose model effect of one position target on foot height."""

    actuator_id: int
    leg_id: LegId
    joint_name: str
    height_m_per_target: float

    @property
    def height_sign(self) -> float:
        """``+1`` raises the model foot when target increases, ``-1`` lowers it."""
        return copysign(1.0, self.height_m_per_target)


# Generated on 2026-07-11 from the PQL-A00 URDF and lower-leg collision meshes;
# all signs flipped on 2026-08-30 after the hardware check showed the preview's
# target->angle direction (hip -1 / knee +1) was mirrored for every joint.
# The ordering is the serial/control ordering used by ControlService.
ACTUATOR_HEIGHT_EFFECTS: tuple[ActuatorHeightEffect, ...] = (
    ActuatorHeightEffect(0, LegId.FRONT_RIGHT, "rev_fr2", -1.65906302e-6),
    ActuatorHeightEffect(1, LegId.FRONT_RIGHT, "rev_fr3", +3.52767462e-5),
    ActuatorHeightEffect(2, LegId.FRONT_LEFT, "rev_fl2", -4.37948354e-6),
    ActuatorHeightEffect(3, LegId.FRONT_LEFT, "rev_fl3", +3.68242224e-5),
    ActuatorHeightEffect(4, LegId.REAR_RIGHT, "rev_rr2", -8.71523298e-6),
    ActuatorHeightEffect(5, LegId.REAR_RIGHT, "rev_rr3", +3.91568108e-5),
    ActuatorHeightEffect(6, LegId.REAR_LEFT, "rev_rl2", +6.44408734e-6),
    ActuatorHeightEffect(7, LegId.REAR_LEFT, "rev_rl3", +2.72224320e-5),
)

_RIGHT_LEGS = frozenset((LegId.FRONT_RIGHT, LegId.REAR_RIGHT))
_REAR_LEGS = frozenset((LegId.REAR_RIGHT, LegId.REAR_LEFT))


def model_derived_mixing_matrix() -> list[list[float]]:
    """Return target-correction mixing signs derived from the 3D model.

    The PID output is negative for a positive measured tilt because the error
    is ``setpoint(0) - measured``.  A positive control-frame roll means the
    right side is low; a positive pitch means the nose is up.  Therefore a
    negative PID output must raise right/rear corners and lower left/front
    corners respectively.

    Each actuator's target sign is additionally flipped when increasing that
    target lowers its model foot.  Matrix magnitudes intentionally remain one:
    the static URDF sensitivity is geometry metadata, not a pneumatic gain or
    load model, and using inverse sensitivities would create unsafe large hip
    commands before any pressure/load identification has been performed.
    """
    matrix: list[list[float]] = []
    for effect in ACTUATOR_HEIGHT_EFFECTS:
        lateral = 1.0 if effect.leg_id in _RIGHT_LEGS else -1.0
        longitudinal = 1.0 if effect.leg_id in _REAR_LEGS else -1.0
        matrix.append(
            [
                -lateral * effect.height_sign,
                -longitudinal * effect.height_sign,
            ]
        )
    return matrix


def leg_height_deltas(corrections: list[float]) -> dict[LegId, float]:
    """Predict neutral-pose foot-height changes for target corrections.

    This is intended for tests and offline diagnostics only.  It is a linear
    kinematic approximation and does not model pressure, contact, load,
    cylinder force, or joint limits.
    """
    deltas = {leg_id: 0.0 for leg_id in LegId}
    for effect, correction in zip(ACTUATOR_HEIGHT_EFFECTS, corrections, strict=False):
        deltas[effect.leg_id] += effect.height_m_per_target * correction
    return deltas
