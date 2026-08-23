"""Test-derived Roll/Pitch convention used only by the stabilization loop.

The fused IMU Euler angles remain raw sensor-frame values in API telemetry and
experiment logs.  The slow pneumatic feedback loop needs a physical convention
instead: ``roll > 0`` means the robot's right side is low, and ``pitch > 0``
means the nose is up.

The 2026-07-11 manual experiments established that, after level calibration,
right-side-down appears as negative raw Roll while nose-up appears as positive
raw Pitch.  The signs below encode that measured relation.  They are separate
from the three.js presentation transform because an outer-loop controller only
uses level-corrected Roll/Pitch scalars; it must not infer a full 3D mounting
rotation from an incomplete manual trial.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AttitudeControlFrame:
    """Level-corrected sensor attitude expressed in the control convention."""

    roll_sign: int = -1
    pitch_sign: int = 1

    def tilt(
        self,
        *,
        raw_roll_deg: float,
        raw_pitch_deg: float,
        level_roll_offset_deg: float,
        level_pitch_offset_deg: float,
    ) -> tuple[float, float]:
        return (
            self.roll_sign * (raw_roll_deg - level_roll_offset_deg),
            self.pitch_sign * (raw_pitch_deg - level_pitch_offset_deg),
        )

    def error_rates(self, *, gyro_x_dps: float, gyro_y_dps: float) -> tuple[float, float]:
        """Return d(setpoint - attitude)/dt for gyro-rate D control."""
        return (-self.roll_sign * gyro_x_dps, -self.pitch_sign * gyro_y_dps)

