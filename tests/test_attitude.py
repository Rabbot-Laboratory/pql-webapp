from __future__ import annotations

from math import inf, isfinite, nan, sqrt

from highend_server.sensors.attitude import (
    DEFAULT_KI,
    DEG_TO_RAD,
    MahonyMARG,
    Quaternion,
    euler_to_quat,
    gravity_from_quat,
    normalize3,
    quat_normalize,
    quat_to_euler,
    rotate_vector_by_quat_inverse,
)
from highend_server.sensors.imu_bmx055 import Vector3

WORLD_MAG = Vector3(22.0, 0.0, -40.0)


def _static_measurements(roll_deg: float, pitch_deg: float, yaw_deg: float):
    """Return (accel_g, mag_body) for a device held statically at an attitude."""
    q = euler_to_quat(roll_deg, pitch_deg, yaw_deg)
    accel = gravity_from_quat(q)  # specific force opposing gravity at rest
    mag = rotate_vector_by_quat_inverse(q, WORLD_MAG)
    return accel, mag


def test_euler_quat_round_trip() -> None:
    for roll, pitch, yaw in [(0, 0, 0), (10, -5, 30), (-25, 15, -120), (5, 40, 90)]:
        q = euler_to_quat(roll, pitch, yaw)
        e = quat_to_euler(q)
        assert abs(e.roll_deg - roll) < 1e-6
        assert abs(e.pitch_deg - pitch) < 1e-6
        assert abs(e.yaw_deg - yaw) < 1e-6


def test_gravity_identity_points_up() -> None:
    g = gravity_from_quat(Quaternion(1.0, 0.0, 0.0, 0.0))
    assert abs(g.x) < 1e-9
    assert abs(g.y) < 1e-9
    assert abs(g.z - 1.0) < 1e-9


def test_mahony_converges_from_wrong_initial_attitude() -> None:
    # True static attitude the filter must find.
    roll_t, pitch_t, yaw_t = 15.0, -10.0, 40.0
    accel, mag = _static_measurements(roll_t, pitch_t, yaw_t)

    filt = MahonyMARG(kp=2.0, ki=0.0)
    # Corrupt the starting orientation so convergence is actually exercised
    # (roll off by 15 deg, pitch by 10 deg, yaw by 20 deg).
    filt.q = euler_to_quat(0.0, 0.0, 20.0)
    filt.initialized = True

    dt = 0.01  # 100 Hz
    result = None
    for _ in range(int(15.0 / dt)):  # 15 seconds
        result = filt.update(gyro_rad=Vector3(0.0, 0.0, 0.0), accel_g=accel, mag_raw=mag, dt=dt)

    assert result is not None
    assert abs(result.euler.roll_deg - roll_t) < 2.0
    assert abs(result.euler.pitch_deg - pitch_t) < 2.0
    assert abs(result.euler.yaw_deg - yaw_t) < 3.0


def test_six_axis_fallback_keeps_roll_pitch() -> None:
    roll_t, pitch_t = 20.0, -12.0
    accel, _ = _static_measurements(roll_t, pitch_t, 0.0)

    filt = MahonyMARG()
    dt = 0.01
    result = None
    for _ in range(int(4.0 / dt)):
        # mag_raw=None exercises the 6-axis (gravity-only) update path.
        result = filt.update(gyro_rad=Vector3(0.0, 0.0, 0.0), accel_g=accel, mag_raw=None, dt=dt)

    assert result is not None
    assert abs(result.euler.roll_deg - roll_t) < 2.0
    assert abs(result.euler.pitch_deg - pitch_t) < 2.0


def test_ki_estimates_constant_gyro_bias() -> None:
    # Device is static; a constant gyro bias is injected. With ki>0 the integral
    # feedback must learn the bias so the attitude stops drifting.
    accel, mag = _static_measurements(0.0, 0.0, 0.0)
    bias_dps = Vector3(3.0, -2.0, 1.5)
    bias_rad = Vector3(bias_dps.x * DEG_TO_RAD, bias_dps.y * DEG_TO_RAD, bias_dps.z * DEG_TO_RAD)

    filt = MahonyMARG(kp=0.8, ki=0.3)
    dt = 0.01
    for _ in range(int(20.0 / dt)):  # long enough for the integral to settle
        filt.update(gyro_rad=bias_rad, accel_g=accel, mag_raw=mag, dt=dt)

    # The integral term should have grown to cancel (negate) the injected bias.
    assert abs(filt.integral.x + bias_rad.x) < 0.2 * abs(bias_rad.x) + 1e-3
    assert abs(filt.integral.y + bias_rad.y) < 0.2 * abs(bias_rad.y) + 1e-3
    # And the estimate stays level despite the biased gyro.
    e = quat_to_euler(filt.q)
    assert abs(e.roll_deg) < 1.0
    assert abs(e.pitch_deg) < 1.0


def test_ki_zeroing_bug_is_gone() -> None:
    # Regression: with ki=0 the integral must remain untouched (the old code
    # re-zeroed it every cycle, which was harmless but confirmed dead-code intent).
    # With ki>0 the integral must actually accumulate across updates.
    accel, mag = _static_measurements(0.0, 0.0, 0.0)
    filt = MahonyMARG(kp=0.5, ki=DEFAULT_KI)
    # Tilt the estimate so there is a non-zero error term to integrate.
    filt.q = euler_to_quat(10.0, 0.0, 0.0)
    filt.initialized = True
    filt.update(gyro_rad=Vector3(0.0, 0.0, 0.0), accel_g=accel, mag_raw=mag, dt=0.01)
    first = filt.integral
    filt.update(gyro_rad=Vector3(0.0, 0.0, 0.0), accel_g=accel, mag_raw=mag, dt=0.01)
    second = filt.integral
    magnitude = sqrt(second.x ** 2 + second.y ** 2 + second.z ** 2)
    assert magnitude > 0.0
    # It accumulated (grew) rather than being reset each cycle.
    assert abs(second.x) >= abs(first.x)


def test_rotate_inverse_is_consistent_with_gravity() -> None:
    # gravity_from_quat is world +Z rotated into the body frame; verify against
    # the generic rotate-into-body helper.
    q = euler_to_quat(12.0, -8.0, 33.0)
    g = gravity_from_quat(q)
    r = rotate_vector_by_quat_inverse(q, Vector3(0.0, 0.0, 1.0))
    assert abs(g.x - r.x) < 1e-9
    assert abs(g.y - r.y) < 1e-9
    assert abs(g.z - r.z) < 1e-9
    # sanity: magnitudes preserved
    assert abs(sqrt(g.x ** 2 + g.y ** 2 + g.z ** 2) - 1.0) < 1e-9


# --------------------------------------------------------------------------
# Non-finite input hardening (fail-closed safety guards)
# --------------------------------------------------------------------------


def _quat_is_finite_unit(q: Quaternion) -> bool:
    components = (q.w, q.x, q.y, q.z)
    if not all(isfinite(c) for c in components):
        return False
    return abs(sqrt(sum(c * c for c in components)) - 1.0) < 1e-6


def test_normalize3_rejects_non_finite_vectors() -> None:
    assert normalize3(Vector3(nan, 0.0, 0.0)) is None
    assert normalize3(Vector3(0.0, inf, 0.0)) is None
    assert normalize3(Vector3(-inf, nan, 1.0)) is None
    assert normalize3(Vector3(0.0, 0.0, 0.0)) is None  # degenerate still None
    ok = normalize3(Vector3(0.0, 0.0, 2.0))
    assert ok is not None and abs(ok.z - 1.0) < 1e-12


def test_quat_normalize_resets_non_finite_to_identity() -> None:
    for bad in [
        Quaternion(nan, 0.0, 0.0, 0.0),
        Quaternion(1.0, inf, 0.0, 0.0),
        Quaternion(nan, nan, nan, nan),
        Quaternion(0.0, 0.0, 0.0, 0.0),  # zero-norm also resets
    ]:
        q = quat_normalize(bad)
        assert (q.w, q.x, q.y, q.z) == (1.0, 0.0, 0.0, 0.0)


def test_mahony_skips_update_on_non_finite_inputs() -> None:
    accel, mag = _static_measurements(10.0, -5.0, 30.0)
    filt = MahonyMARG(kp=2.0, ki=0.0)
    dt = 0.01
    for _ in range(200):
        filt.update(gyro_rad=Vector3(0.0, 0.0, 0.0), accel_g=accel, mag_raw=mag, dt=dt)
    q_before = filt.q

    bad_vec = Vector3(nan, inf, -inf)
    zero = Vector3(0.0, 0.0, 0.0)
    # NaN/Inf in accel, gyro, or mag must not move (or corrupt) the estimate.
    r1 = filt.update(gyro_rad=zero, accel_g=bad_vec, mag_raw=mag, dt=dt)
    r2 = filt.update(gyro_rad=bad_vec, accel_g=accel, mag_raw=mag, dt=dt)
    r3 = filt.update(gyro_rad=zero, accel_g=accel, mag_raw=bad_vec, dt=dt)

    for result in (r1, r2, r3):
        assert _quat_is_finite_unit(result.quaternion)
        assert isfinite(result.euler.roll_deg)
        assert isfinite(result.euler.pitch_deg)
        assert isfinite(result.euler.yaw_deg)
    assert filt.q == q_before  # previous attitude kept (fail-closed skip)

    # The filter must keep working normally after the bad samples pass.
    result = filt.update(gyro_rad=zero, accel_g=accel, mag_raw=mag, dt=dt)
    assert _quat_is_finite_unit(result.quaternion)
