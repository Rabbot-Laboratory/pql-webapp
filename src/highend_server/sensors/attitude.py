"""Attitude estimation (Mahony MARG complementary filter) for the BMX055 IMU.

Coordinate convention (single source of truth for control and GUI transforms)
============================================================================

Body frame (sensor frame, right-handed):

    * +X points forward (robot nose direction)
    * +Y points to the left
    * +Z points up

At rest on a level surface the accelerometer measures the specific force that
opposes gravity, i.e. ``accel_g == (0, 0, +1)`` g. Consistently,
``gravity_from_quat(identity)`` returns ``(0, 0, +1)``: it yields the world
"up" (world +Z) direction expressed in body coordinates.

Quaternion:

    * Hamilton convention, ordered ``(w, x, y, z)``.
    * Rotates a vector from the body frame into the world frame
      (``v_world = q * v_body * q^-1``).

Euler angles (``EulerAngles``, all in degrees):

    * Tait-Bryan **ZYX intrinsic** order: yaw about world +Z, then pitch about
      the new +Y, then roll about the new +X.
    * ``roll_deg``  = rotation about body +X (right-side-down positive)
    * ``pitch_deg`` = rotation about body +Y (nose-up positive)
    * ``yaw_deg``   = rotation about body +Z (counter-clockwise positive)

Phase 2 stabilization control uses ``roll_deg`` / ``pitch_deg`` only. ``yaw_deg``
is display-only because it depends on magnetometer calibration quality.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, atan2, cos, isfinite, radians, sin, sqrt

from highend_server.sensors.imu_bmx055 import Vector3

DEG_TO_RAD = 0.017453292519943295
RAD_TO_DEG = 57.29577951308232

# Default Mahony gains, retuned for the 100 Hz dedicated-thread pipeline.
# kp governs how strongly the accel/mag reference pulls the estimate; at 100 Hz a
# lower kp than the previous 20 Hz value (1.2) keeps the filter smooth. ki > 0
# enables online gyro-bias estimation (previously dead code, see update()).
DEFAULT_KP = 0.8
DEFAULT_KI = 0.02


@dataclass(frozen=True, slots=True)
class Quaternion:
    w: float
    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class EulerAngles:
    roll_deg: float
    pitch_deg: float
    yaw_deg: float


@dataclass(frozen=True, slots=True)
class FusedAttitude:
    quaternion: Quaternion
    euler: EulerAngles
    gravity_g: Vector3
    linear_accel_g: Vector3


_ZERO_VEC = Vector3(0.0, 0.0, 0.0)


def _vec_finite(v: Vector3) -> bool:
    return isfinite(v.x) and isfinite(v.y) and isfinite(v.z)


def add3(a: Vector3, b: Vector3) -> Vector3:
    return Vector3(a.x + b.x, a.y + b.y, a.z + b.z)


def sub3(a: Vector3, b: Vector3) -> Vector3:
    return Vector3(a.x - b.x, a.y - b.y, a.z - b.z)


def scale3(v: Vector3, scale: float) -> Vector3:
    return Vector3(v.x * scale, v.y * scale, v.z * scale)


def normalize3(v: Vector3) -> Vector3 | None:
    n = sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
    # ``n <= 1e-12`` is False for NaN, so a NaN norm would slip through as
    # "valid" and poison every downstream safety guard. Require a finite,
    # non-degenerate norm explicitly.
    if not isfinite(n) or n <= 1e-12:
        return None
    return Vector3(v.x / n, v.y / n, v.z / n)


def quat_normalize(q: Quaternion) -> Quaternion:
    n = sqrt(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z)
    # Fail closed on a non-finite or degenerate norm: reset to identity rather
    # than propagating NaN/Inf through the attitude estimate.
    if not isfinite(n) or n <= 1e-12:
        return Quaternion(1.0, 0.0, 0.0, 0.0)
    return Quaternion(q.w / n, q.x / n, q.y / n, q.z / n)


def quat_conjugate(q: Quaternion) -> Quaternion:
    return Quaternion(q.w, -q.x, -q.y, -q.z)


def quat_multiply(a: Quaternion, b: Quaternion) -> Quaternion:
    return Quaternion(
        w=a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
        x=a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
        y=a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
        z=a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
    )


def rotate_vector_by_quat(q: Quaternion, v: Vector3) -> Vector3:
    """Rotate a body-frame vector into the world frame: ``q * v * q^-1``."""
    qv = Quaternion(0.0, v.x, v.y, v.z)
    result = quat_multiply(quat_multiply(q, qv), quat_conjugate(q))
    return Vector3(result.x, result.y, result.z)


def rotate_vector_by_quat_inverse(q: Quaternion, v: Vector3) -> Vector3:
    """Rotate a world-frame vector into the body frame: ``q^-1 * v * q``."""
    return rotate_vector_by_quat(quat_conjugate(q), v)


def euler_to_quat(roll_deg: float, pitch_deg: float, yaw_deg: float) -> Quaternion:
    roll = radians(roll_deg)
    pitch = radians(pitch_deg)
    yaw = radians(yaw_deg)
    cr = cos(roll * 0.5)
    sr = sin(roll * 0.5)
    cp = cos(pitch * 0.5)
    sp = sin(pitch * 0.5)
    cy = cos(yaw * 0.5)
    sy = sin(yaw * 0.5)
    return quat_normalize(
        Quaternion(
            w=cr * cp * cy + sr * sp * sy,
            x=sr * cp * cy - cr * sp * sy,
            y=cr * sp * cy + sr * cp * sy,
            z=cr * cp * sy - sr * sp * cy,
        )
    )


def quat_to_euler(q: Quaternion) -> EulerAngles:
    roll = atan2(2.0 * (q.w * q.x + q.y * q.z), 1.0 - 2.0 * (q.x * q.x + q.y * q.y))
    sin_pitch = 2.0 * (q.w * q.y - q.z * q.x)
    sin_pitch = max(-1.0, min(1.0, sin_pitch))
    pitch = asin(sin_pitch)
    yaw = atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
    return EulerAngles(roll * RAD_TO_DEG, pitch * RAD_TO_DEG, yaw * RAD_TO_DEG)


def gravity_from_quat(q: Quaternion) -> Vector3:
    """World "up" (world +Z) direction expressed in the body frame."""
    return Vector3(
        x=2.0 * (q.x * q.z - q.w * q.y),
        y=2.0 * (q.w * q.x + q.y * q.z),
        z=q.w * q.w - q.x * q.x - q.y * q.y + q.z * q.z,
    )


def initial_quat_from_acc_mag(accel: Vector3, mag: Vector3) -> Quaternion:
    roll = atan2(accel.y, accel.z)
    pitch = atan2(-accel.x, sqrt(accel.y * accel.y + accel.z * accel.z))
    mx = mag.x * cos(pitch) + mag.z * sin(pitch)
    my = (
        mag.x * sin(roll) * sin(pitch)
        + mag.y * cos(roll)
        - mag.z * sin(roll) * cos(pitch)
    )
    yaw = atan2(-my, mx)
    return euler_to_quat(roll * RAD_TO_DEG, pitch * RAD_TO_DEG, yaw * RAD_TO_DEG)


class MahonyMARG:
    """Mahony complementary filter with optional magnetometer (MARG) update.

    Supports both a 9-axis (accel + gyro + mag) update and a 6-axis
    (accel + gyro) update. Call :meth:`update` with ``mag_raw=None`` (or a
    zero-norm vector) to run the 6-axis path, which corrects roll/pitch from
    gravity while yaw integrates from the gyro alone (and slowly drifts).
    """

    def __init__(self, *, kp: float = DEFAULT_KP, ki: float = DEFAULT_KI) -> None:
        self.kp = kp
        self.ki = ki
        self.q = Quaternion(1.0, 0.0, 0.0, 0.0)
        self.integral = Vector3(0.0, 0.0, 0.0)
        self.initialized = False

    def reset(self) -> None:
        self.q = Quaternion(1.0, 0.0, 0.0, 0.0)
        self.integral = Vector3(0.0, 0.0, 0.0)
        self.initialized = False

    def update(
        self,
        *,
        gyro_rad: Vector3,
        accel_g: Vector3,
        mag_raw: Vector3 | None,
        dt: float,
    ) -> FusedAttitude:
        # Fail closed on any non-finite input: a single NaN/Inf sample would
        # otherwise corrupt the quaternion permanently and defeat the tilt /
        # staleness safety guards downstream. Skip the update and keep the
        # previous attitude, mirroring the ``acc is None`` path below.
        if not (_vec_finite(gyro_rad) and _vec_finite(accel_g)) or (
            mag_raw is not None and not _vec_finite(mag_raw)
        ):
            return self._attitude(accel_g if _vec_finite(accel_g) else _ZERO_VEC)

        acc = normalize3(accel_g)
        mag = normalize3(mag_raw) if mag_raw is not None else None
        if acc is None:
            return self._attitude(accel_g)

        if not self.initialized:
            fallback_mag = Vector3(1.0, 0.0, 0.0)
            self.q = initial_quat_from_acc_mag(acc, mag if mag is not None else fallback_mag)
            self.initialized = True

        q0, q1, q2, q3 = self.q.w, self.q.x, self.q.y, self.q.z

        # Estimated gravity direction (body frame) from the current quaternion.
        vx = 2.0 * (q1 * q3 - q0 * q2)
        vy = 2.0 * (q0 * q1 + q2 * q3)
        vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3

        # Error = measured gravity (accel) x estimated gravity.
        ex = acc.y * vz - acc.z * vy
        ey = acc.z * vx - acc.x * vz
        ez = acc.x * vy - acc.y * vx

        # 9-axis: add the magnetometer heading error term. Skipped when no fresh
        # (or valid) mag sample is supplied -> 6-axis gravity-only correction.
        if mag is not None:
            hx = (
                2.0 * mag.x * (0.5 - q2 * q2 - q3 * q3)
                + 2.0 * mag.y * (q1 * q2 - q0 * q3)
                + 2.0 * mag.z * (q1 * q3 + q0 * q2)
            )
            hy = (
                2.0 * mag.x * (q1 * q2 + q0 * q3)
                + 2.0 * mag.y * (0.5 - q1 * q1 - q3 * q3)
                + 2.0 * mag.z * (q2 * q3 - q0 * q1)
            )
            bx = sqrt(hx * hx + hy * hy)
            bz = (
                2.0 * mag.x * (q1 * q3 - q0 * q2)
                + 2.0 * mag.y * (q2 * q3 + q0 * q1)
                + 2.0 * mag.z * (0.5 - q1 * q1 - q2 * q2)
            )

            wx = 2.0 * bx * (0.5 - q2 * q2 - q3 * q3) + 2.0 * bz * (q1 * q3 - q0 * q2)
            wy = 2.0 * bx * (q1 * q2 - q0 * q3) + 2.0 * bz * (q0 * q1 + q2 * q3)
            wz = 2.0 * bx * (q0 * q2 + q1 * q3) + 2.0 * bz * (0.5 - q1 * q1 - q2 * q2)

            ex += mag.y * wz - mag.z * wy
            ey += mag.z * wx - mag.x * wz
            ez += mag.x * wy - mag.y * wx

        # Integral feedback estimates and removes the gyro bias online. This only
        # runs when ki > 0; when ki <= 0 the integral is left untouched (it stays
        # zero), instead of being destructively re-zeroed every cycle as before.
        if self.ki > 0.0:
            self.integral = add3(self.integral, scale3(Vector3(ex, ey, ez), self.ki * dt))

        gx = gyro_rad.x + self.kp * ex + self.integral.x
        gy = gyro_rad.y + self.kp * ey + self.integral.y
        gz = gyro_rad.z + self.kp * ez + self.integral.z

        q_dot0 = -0.5 * (q1 * gx + q2 * gy + q3 * gz)
        q_dot1 = 0.5 * (q0 * gx + q2 * gz - q3 * gy)
        q_dot2 = 0.5 * (q0 * gy - q1 * gz + q3 * gx)
        q_dot3 = 0.5 * (q0 * gz + q1 * gy - q2 * gx)

        self.q = quat_normalize(
            Quaternion(
                w=q0 + q_dot0 * dt,
                x=q1 + q_dot1 * dt,
                y=q2 + q_dot2 * dt,
                z=q3 + q_dot3 * dt,
            )
        )
        return self._attitude(accel_g)

    def _attitude(self, accel_g: Vector3) -> FusedAttitude:
        gravity_g = gravity_from_quat(self.q)
        return FusedAttitude(
            quaternion=self.q,
            euler=quat_to_euler(self.q),
            gravity_g=gravity_g,
            linear_accel_g=sub3(accel_g, gravity_g),
        )


def attitude_from_quaternion(q: Quaternion, accel_g: Vector3) -> FusedAttitude:
    q = quat_normalize(q)
    gravity_g = gravity_from_quat(q)
    return FusedAttitude(
        quaternion=q,
        euler=quat_to_euler(q),
        gravity_g=gravity_g,
        linear_accel_g=sub3(accel_g, gravity_g),
    )
