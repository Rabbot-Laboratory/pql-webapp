"""Delayed virtual IMU and bounded adaptive body-level control."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import asin, atan2, degrees, radians

import numpy as np
from numpy.typing import NDArray

from highend_server.simulation.config import AdaptiveAttitudeControlConfig, ImuConfig


@dataclass(frozen=True, slots=True)
class ImuSample:
    timestamp_s: float
    roll_rad: float
    pitch_rad: float
    roll_rate_rad_s: float
    pitch_rate_rad_s: float


def quaternion_to_roll_pitch(quaternion_wxyz: NDArray[np.float64]) -> tuple[float, float]:
    """Return rotations about CAD X and Y axes from a MuJoCo WXYZ quaternion."""
    w, x, y, z = (float(value) for value in quaternion_wxyz)
    roll = atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    return roll, pitch


@dataclass(slots=True)
class SimulatedImu:
    """Sample, delay, bias, and add deterministic noise to ideal MuJoCo sensors."""

    config: ImuConfig
    _pending: deque[ImuSample] = field(default_factory=deque)
    _last_output: ImuSample = field(default_factory=lambda: ImuSample(0.0, 0.0, 0.0, 0.0, 0.0))
    _next_sample_s: float = 0.0
    _rng: np.random.Generator = field(init=False)

    def __post_init__(self) -> None:
        if self.config.sample_rate_hz <= 0:
            raise ValueError("IMU sample_rate_hz must be > 0")
        if self.config.delay_s < 0:
            raise ValueError("IMU delay_s must be >= 0")
        self._rng = np.random.default_rng(self.config.random_seed)

    def reset(self) -> None:
        self._pending.clear()
        self._last_output = ImuSample(0.0, 0.0, 0.0, 0.0, 0.0)
        self._next_sample_s = 0.0
        self._rng = np.random.default_rng(self.config.random_seed)

    def update(
        self,
        now_s: float,
        quaternion_wxyz: NDArray[np.float64],
        gyro_xyz_rad_s: NDArray[np.float64],
    ) -> ImuSample:
        if now_s + 1e-12 >= self._next_sample_s:
            roll, pitch = quaternion_to_roll_pitch(quaternion_wxyz)
            orientation_noise = radians(self.config.orientation_noise_std_deg)
            gyro_noise = radians(self.config.gyro_noise_std_deg_s)
            self._pending.append(
                ImuSample(
                    timestamp_s=now_s,
                    roll_rad=roll
                    + radians(self.config.roll_bias_deg)
                    + float(self._rng.normal(0.0, orientation_noise)),
                    pitch_rad=pitch
                    + radians(self.config.pitch_bias_deg)
                    + float(self._rng.normal(0.0, orientation_noise)),
                    roll_rate_rad_s=float(gyro_xyz_rad_s[0])
                    + float(self._rng.normal(0.0, gyro_noise)),
                    pitch_rate_rad_s=float(gyro_xyz_rad_s[1])
                    + float(self._rng.normal(0.0, gyro_noise)),
                )
            )
            self._next_sample_s += 1.0 / self.config.sample_rate_hz

        cutoff = now_s - self.config.delay_s
        while self._pending and self._pending[0].timestamp_s <= cutoff + 1e-12:
            self._last_output = self._pending.popleft()
        return self._last_output


@dataclass(frozen=True, slots=True)
class AttitudeCorrection:
    foot_height_m: NDArray[np.float64]
    roll_trim_rad: float
    pitch_trim_rad: float
    roll_slope_rad: float
    pitch_slope_rad: float


# Hip locations in the CAD frame: X is left, Y is rear, Z is up.
LEG_ANCHORS_XY: NDArray[np.float64] = np.asarray(
    (
        (-0.1308, -0.168616),
        (+0.1308, -0.134616),
        (-0.1233, +0.121384),
        (+0.1308, +0.155384),
    ),
    dtype=float,
)


@dataclass(slots=True)
class AdaptiveImuGaitController:
    """Level the body and learn persistent front/rear and left/right gait trim.

    Proportional and gyro terms reject transient tilt. The bounded trim states
    adapt to persistent imbalance caused by unequal cylinders or load. No foot
    contact state is required; all four nominal foot trajectories are shifted.
    """

    config: AdaptiveAttitudeControlConfig
    roll_trim_rad: float = 0.0
    pitch_trim_rad: float = 0.0

    def reset(self) -> None:
        self.roll_trim_rad = 0.0
        self.pitch_trim_rad = 0.0

    def update(self, sample: ImuSample, dt: float) -> AttitudeCorrection:
        if not self.config.enabled:
            return AttitudeCorrection(np.zeros(4), 0.0, 0.0, 0.0, 0.0)

        deadband = radians(self.config.adaptation_deadband_deg)
        leak = max(0.0, 1.0 - self.config.trim_leak_rate * dt)
        self.roll_trim_rad *= leak
        self.pitch_trim_rad *= leak
        if abs(sample.roll_rad) > deadband:
            self.roll_trim_rad += self.config.adaptation_rate * sample.roll_rad * dt
        if abs(sample.pitch_rad) > deadband:
            self.pitch_trim_rad += self.config.adaptation_rate * sample.pitch_rad * dt

        max_trim = radians(self.config.max_trim_deg)
        self.roll_trim_rad = float(np.clip(self.roll_trim_rad, -max_trim, max_trim))
        self.pitch_trim_rad = float(np.clip(self.pitch_trim_rad, -max_trim, max_trim))
        max_slope = radians(self.config.max_slope_deg)
        roll_slope = float(
            np.clip(
                self.config.kp * sample.roll_rad
                + self.config.kd_s * sample.roll_rate_rad_s
                + self.roll_trim_rad,
                -max_slope,
                max_slope,
            )
        )
        pitch_slope = float(
            np.clip(
                self.config.kp * sample.pitch_rad
                + self.config.kd_s * sample.pitch_rate_rad_s
                + self.pitch_trim_rad,
                -max_slope,
                max_slope,
            )
        )

        x = LEG_ANCHORS_XY[:, 0]
        y = LEG_ANCHORS_XY[:, 1]
        # Inverse the measured body plane: front/back for X rotation,
        # left/right for Y rotation. Negative Z extends a leg.
        foot_height = roll_slope * y - pitch_slope * x
        foot_height = np.clip(
            foot_height,
            -self.config.max_foot_correction_m,
            self.config.max_foot_correction_m,
        )
        return AttitudeCorrection(
            foot_height_m=foot_height,
            roll_trim_rad=self.roll_trim_rad,
            pitch_trim_rad=self.pitch_trim_rad,
            roll_slope_rad=roll_slope,
            pitch_slope_rad=pitch_slope,
        )

    def trim_degrees(self) -> tuple[float, float]:
        return degrees(self.roll_trim_rad), degrees(self.pitch_trim_rad)
