"""Magnetometer hard-iron / soft-iron calibration.

Raw magnetometer readings on a rotating rigid body ideally trace a sphere
centred on the origin. In practice they trace an off-centre ellipsoid because
of two error sources:

* **Hard-iron** errors shift the sphere centre (constant additive offset from
  nearby permanent magnets / magnetised material).
* **Soft-iron** errors scale/skew the sphere into an ellipsoid (field
  distortion from ferromagnetic material).

This module estimates a hard-iron ``offset`` and a diagonal soft-iron ``scale``
using the min/max (bounding-box) method, which is cheap, robust to noise, and
sufficient for a diagonal soft-iron correction:

    offset_axis = (max_axis + min_axis) / 2
    radius_axis = (max_axis - min_axis) / 2
    scale_axis  = mean(radius_x, radius_y, radius_z) / radius_axis

A calibrated sample is then ``(raw - offset) * scale``, which maps the ellipsoid
back onto a sphere of the average radius. A full least-squares ellipsoid fit
would additionally recover off-diagonal (cross-axis) soft-iron terms; the
diagonal approximation is intentionally used here (documented trade-off).

Quality metrics returned alongside the fit:

* ``residual`` - RMS deviation of the calibrated sample radii from the average
  radius, normalised by that radius (0 = perfect sphere). Lower is better.
* ``coverage`` - fraction of the 8 spatial octants that contain at least one
  centred sample (0..1). Low coverage means the user did not rotate the device
  through enough orientations for a trustworthy fit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import sqrt

from highend_server.sensors.imu_bmx055 import Vector3

MIN_SAMPLES = 12


@dataclass(frozen=True, slots=True)
class MagCalibrationResult:
    offset: Vector3
    scale: Vector3
    residual: float
    coverage: float
    sample_count: int


def apply_calibration(raw: Vector3, offset: Vector3, scale: Vector3) -> Vector3:
    return Vector3(
        x=(raw.x - offset.x) * scale.x,
        y=(raw.y - offset.y) * scale.y,
        z=(raw.z - offset.z) * scale.z,
    )


def fit(samples: Sequence[Vector3]) -> MagCalibrationResult:
    """Fit hard-iron offset + diagonal soft-iron scale from raw mag samples."""
    if len(samples) < MIN_SAMPLES:
        raise ValueError(
            f"Need at least {MIN_SAMPLES} magnetometer samples to calibrate, "
            f"got {len(samples)}"
        )

    min_x = min(s.x for s in samples)
    max_x = max(s.x for s in samples)
    min_y = min(s.y for s in samples)
    max_y = max(s.y for s in samples)
    min_z = min(s.z for s in samples)
    max_z = max(s.z for s in samples)

    offset = Vector3(
        x=(max_x + min_x) * 0.5,
        y=(max_y + min_y) * 0.5,
        z=(max_z + min_z) * 0.5,
    )
    radius = Vector3(
        x=(max_x - min_x) * 0.5,
        y=(max_y - min_y) * 0.5,
        z=(max_z - min_z) * 0.5,
    )
    if radius.x <= 1e-9 or radius.y <= 1e-9 or radius.z <= 1e-9:
        raise ValueError(
            "Magnetometer samples are degenerate (zero span on an axis); "
            "rotate the device through more orientations."
        )

    avg_radius = (radius.x + radius.y + radius.z) / 3.0
    scale = Vector3(
        x=avg_radius / radius.x,
        y=avg_radius / radius.y,
        z=avg_radius / radius.z,
    )

    residual = _residual(samples, offset, scale, avg_radius)
    coverage = _octant_coverage(samples, offset)

    return MagCalibrationResult(
        offset=offset,
        scale=scale,
        residual=residual,
        coverage=coverage,
        sample_count=len(samples),
    )


def _residual(
    samples: Sequence[Vector3],
    offset: Vector3,
    scale: Vector3,
    avg_radius: float,
) -> float:
    if avg_radius <= 1e-9:
        return 0.0
    acc = 0.0
    for s in samples:
        c = apply_calibration(s, offset, scale)
        r = sqrt(c.x * c.x + c.y * c.y + c.z * c.z)
        diff = (r - avg_radius) / avg_radius
        acc += diff * diff
    return sqrt(acc / len(samples))


def _octant_coverage(samples: Sequence[Vector3], offset: Vector3) -> float:
    octants: set[int] = set()
    for s in samples:
        cx = s.x - offset.x
        cy = s.y - offset.y
        cz = s.z - offset.z
        bit = (1 if cx >= 0 else 0) | (2 if cy >= 0 else 0) | (4 if cz >= 0 else 0)
        octants.add(bit)
    return len(octants) / 8.0
