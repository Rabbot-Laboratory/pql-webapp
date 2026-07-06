from __future__ import annotations

from math import cos, pi, sin, sqrt

import pytest

from highend_server.sensors.imu_bmx055 import Vector3
from highend_server.sensors.mag_calibration import (
    MIN_SAMPLES,
    apply_calibration,
    fit,
)

TRUE_OFFSET = Vector3(12.0, -8.0, 5.0)
TRUE_SCALE = Vector3(1.0, 0.6, 1.4)  # ellipsoid axis radii relative to unit sphere
RADIUS = 40.0


def _synthetic_ellipsoid(n_theta: int = 24, n_phi: int = 24) -> list[Vector3]:
    """Sample an off-centre ellipsoid: raw = offset + R * (dir / scale)."""
    samples: list[Vector3] = []
    for i in range(n_theta):
        theta = pi * i / (n_theta - 1)  # 0..pi (inclination)
        for j in range(n_phi):
            phi = 2.0 * pi * j / n_phi  # 0..2pi (azimuth)
            ux = sin(theta) * cos(phi)
            uy = sin(theta) * sin(phi)
            uz = cos(theta)
            samples.append(
                Vector3(
                    x=TRUE_OFFSET.x + RADIUS * ux / TRUE_SCALE.x,
                    y=TRUE_OFFSET.y + RADIUS * uy / TRUE_SCALE.y,
                    z=TRUE_OFFSET.z + RADIUS * uz / TRUE_SCALE.z,
                )
            )
    return samples


def test_fit_recovers_offset() -> None:
    result = fit(_synthetic_ellipsoid())
    assert abs(result.offset.x - TRUE_OFFSET.x) < 1.0
    assert abs(result.offset.y - TRUE_OFFSET.y) < 1.0
    assert abs(result.offset.z - TRUE_OFFSET.z) < 1.0


def test_fit_recovers_relative_scale() -> None:
    result = fit(_synthetic_ellipsoid())
    # Scale ratios should match the true axis-radius ratios. Compare x:y and x:z.
    got_xy = result.scale.x / result.scale.y
    want_xy = TRUE_SCALE.x / TRUE_SCALE.y
    got_xz = result.scale.x / result.scale.z
    want_xz = TRUE_SCALE.x / TRUE_SCALE.z
    assert abs(got_xy - want_xy) < 0.05
    assert abs(got_xz - want_xz) < 0.05


def test_calibrated_samples_are_sphere_like() -> None:
    samples = _synthetic_ellipsoid()
    result = fit(samples)
    radii = []
    for s in samples:
        c = apply_calibration(s, result.offset, result.scale)
        radii.append(sqrt(c.x ** 2 + c.y ** 2 + c.z ** 2))
    mean_r = sum(radii) / len(radii)
    spread = max(radii) - min(radii)
    assert spread / mean_r < 0.05  # nearly a perfect sphere
    assert result.residual < 0.02


def test_full_rotation_gives_full_coverage() -> None:
    result = fit(_synthetic_ellipsoid())
    assert result.coverage == 1.0
    assert result.sample_count > MIN_SAMPLES


def test_fit_rejects_too_few_samples() -> None:
    with pytest.raises(ValueError):
        fit([Vector3(1.0, 0.0, 0.0)] * (MIN_SAMPLES - 1))


def test_fit_rejects_degenerate_span() -> None:
    # Enough samples but no span on the z axis -> not calibratable.
    samples = [Vector3(cos(t), sin(t), 0.0) for t in [i * 0.3 for i in range(MIN_SAMPLES + 4)]]
    with pytest.raises(ValueError):
        fit(samples)
