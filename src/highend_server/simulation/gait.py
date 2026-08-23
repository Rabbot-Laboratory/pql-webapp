"""Rear-driven rabbit bound and inverse kinematics for the CAD-derived leg geometry."""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi, sin

import numpy as np
from numpy.typing import NDArray

from highend_server.simulation.config import JOINT_NAMES, GaitConfig


def _rotate_yz(vector: NDArray[np.float64], angle: float) -> NDArray[np.float64]:
    y, z = vector
    return np.array((cos(angle) * y - sin(angle) * z, sin(angle) * y + cos(angle) * z))


@dataclass(frozen=True, slots=True)
class PlanarLeg:
    upper_yz: tuple[float, float]
    lower_yz: tuple[float, float]
    hip_axis_sign: float
    knee_axis_sign: float

    def foot_yz(self, angles: NDArray[np.float64]) -> NDArray[np.float64]:
        hip = self.hip_axis_sign * float(angles[0])
        knee = self.knee_axis_sign * float(angles[1])
        upper = _rotate_yz(np.asarray(self.upper_yz), hip)
        lower = _rotate_yz(np.asarray(self.lower_yz), hip + knee)
        return upper + lower

    def inverse(self, target_yz: NDArray[np.float64]) -> NDArray[np.float64]:
        angles = np.zeros(2, dtype=float)
        for _ in range(12):
            current = self.foot_yz(angles)
            error = target_yz - current
            if float(np.linalg.norm(error)) < 1e-7:
                break
            epsilon = 1e-5
            jacobian = np.column_stack(
                (
                    (self.foot_yz(angles + (epsilon, 0.0)) - current) / epsilon,
                    (self.foot_yz(angles + (0.0, epsilon)) - current) / epsilon,
                )
            )
            delta = np.linalg.solve(jacobian.T @ jacobian + np.eye(2) * 1e-6, jacobian.T @ error)
            angles += delta
            angles[0] = np.clip(angles[0], -0.30, 0.30)
            angles[1] = np.clip(angles[1], -0.46, 0.46)
        return angles


# Vectors come directly from the MJCF/URDF joint origins and simplified foot contacts.
LEGS: tuple[PlanarLeg, ...] = (
    PlanarLeg((0.16027, -0.19834), (-0.1707, -0.1483), +1.0, +1.0),
    PlanarLeg((0.147888, -0.207725), (-0.1783, -0.1389), +1.0, +1.0),
    PlanarLeg((-0.1275, -0.220903), (0.1894, -0.1259), -1.0, -1.0),
    PlanarLeg((-0.180313, -0.180312), (0.1313, -0.1664), -1.0, -1.0),
)

def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


class RabbitBoundGait:
    def __init__(self, config: GaitConfig) -> None:
        if not 0.35 <= config.duty_factor < 1.0:
            raise ValueError("duty_factor must be in [0.35, 1.0)")
        self.config = config
        self._neutral = tuple(leg.foot_yz(np.zeros(2)) for leg in LEGS)
        # Subclasses may override these to express other gaits with the same
        # swing/stance trajectory machinery.
        self._phase_offsets: tuple[float, float, float, float] = (
            0.0,
            0.5,
            config.rear_phase_offset,
            config.rear_phase_offset,
        )
        self._rear_kick_enabled = True

    def _amplitude(self, time_s: float) -> float:
        if time_s <= self.config.startup_s:
            return 0.0
        return _smoothstep((time_s - self.config.startup_s) / self.config.ramp_s)

    def foot_offset(self, leg_index: int, time_s: float) -> NDArray[np.float64]:
        amplitude = self._amplitude(time_s)
        phase_time = max(0.0, time_s - self.config.startup_s)
        phase = (phase_time / self.config.cycle_s + self._phase_offsets[leg_index]) % 1.0
        swing_fraction = 1.0 - self.config.duty_factor
        rear_push = 0.0

        if phase < swing_fraction:
            progress = phase / swing_fraction
            forward = -0.5 + _smoothstep(progress)
            lift = sin(pi * progress)
        else:
            progress = (phase - swing_fraction) / self.config.duty_factor
            if self._rear_kick_enabled and leg_index >= 2:
                rear_push = sin(pi * progress) * self.config.rear_push_m
                progress = min(1.0, progress / self.config.rear_kick_fraction)
            forward = 0.5 - _smoothstep(progress)
            lift = 0.0

        # Robot forward is -Y in the CAD frame.
        stride_scale = (
            self.config.front_stride_scale if leg_index < 2 else self.config.rear_stride_scale
        )
        lift_scale = 1.0 if leg_index < 2 else self.config.rear_lift_scale
        delta_y = -forward * self.config.stride_m * stride_scale * amplitude
        delta_z = (lift * self.config.lift_m * lift_scale - rear_push) * amplitude
        return np.array((delta_y, delta_z))

    def angles(
        self,
        time_s: float,
        foot_height_m: NDArray[np.float64] | None = None,
    ) -> NDArray[np.float64]:
        height_offsets = np.zeros(4) if foot_height_m is None else np.asarray(foot_height_m)
        if height_offsets.shape != (4,):
            raise ValueError("foot_height_m must contain one value for each of four legs")
        result = np.zeros(len(JOINT_NAMES), dtype=float)
        for leg_index, leg in enumerate(LEGS):
            target = self._neutral[leg_index] + self.foot_offset(leg_index, time_s)
            target[1] += height_offsets[leg_index]
            angles = leg.inverse(target)
            if leg_index >= 2:
                angles[1] = np.clip(
                    angles[1],
                    -self.config.rear_knee_limit_rad,
                    self.config.rear_knee_limit_rad,
                )
            result[2 * leg_index : 2 * leg_index + 2] = angles
        return result

    def targets(
        self,
        time_s: float,
        foot_height_m: NDArray[np.float64] | None = None,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        angles = self.angles(time_s, foot_height_m)
        epsilon = 0.002
        before = self.angles(max(0.0, time_s - epsilon), foot_height_m)
        after = self.angles(time_s + epsilon, foot_height_m)
        velocity = (after - before) / (2.0 * epsilon)
        return angles, velocity


class PhaseOffsetGait(RabbitBoundGait):
    """The same swing/stance foot trajectory with arbitrary per-leg phasing.

    Covers crawl / trot / pace / bound / pronk by choosing ``phase_offsets``
    (FR, FL, RR, RL as cycle fractions) and the duty factor. The rabbit
    bound's rear-kick compression is disabled by default: these gaits push
    evenly through the whole stance.
    """

    def __init__(
        self,
        config: GaitConfig,
        phase_offsets: tuple[float, float, float, float],
        *,
        rear_kick: bool = False,
    ) -> None:
        super().__init__(config)
        if len(phase_offsets) != 4:
            raise ValueError("phase_offsets must contain one value per leg")
        self._phase_offsets = tuple(float(offset) % 1.0 for offset in phase_offsets)
        self._rear_kick_enabled = rear_kick
