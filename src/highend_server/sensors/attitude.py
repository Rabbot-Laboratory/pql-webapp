from __future__ import annotations

from dataclasses import dataclass
from math import asin, atan2, cos, radians, sin, sqrt

from highend_server.sensors.imu_bmx055 import Vector3

DEG_TO_RAD = 0.017453292519943295
RAD_TO_DEG = 57.29577951308232


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


def add3(a: Vector3, b: Vector3) -> Vector3:
    return Vector3(a.x + b.x, a.y + b.y, a.z + b.z)


def sub3(a: Vector3, b: Vector3) -> Vector3:
    return Vector3(a.x - b.x, a.y - b.y, a.z - b.z)


def scale3(v: Vector3, scale: float) -> Vector3:
    return Vector3(v.x * scale, v.y * scale, v.z * scale)


def normalize3(v: Vector3) -> Vector3 | None:
    n = sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
    if n <= 1e-12:
        return None
    return Vector3(v.x / n, v.y / n, v.z / n)


def quat_normalize(q: Quaternion) -> Quaternion:
    n = sqrt(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z)
    if n <= 1e-12:
        return Quaternion(1.0, 0.0, 0.0, 0.0)
    return Quaternion(q.w / n, q.x / n, q.y / n, q.z / n)


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
    def __init__(self, *, kp: float = 1.2, ki: float = 0.0) -> None:
        self.kp = kp
        self.ki = ki
        self.q = Quaternion(1.0, 0.0, 0.0, 0.0)
        self.integral = Vector3(0.0, 0.0, 0.0)
        self.initialized = False

    def update(
        self,
        *,
        gyro_rad: Vector3,
        accel_g: Vector3,
        mag_raw: Vector3,
        dt: float,
    ) -> FusedAttitude:
        acc = normalize3(accel_g)
        mag = normalize3(mag_raw)
        if acc is None:
            return self._attitude(accel_g)

        if not self.initialized:
            fallback_mag = Vector3(1.0, 0.0, 0.0)
            self.q = initial_quat_from_acc_mag(acc, mag if mag is not None else fallback_mag)
            self.initialized = True

        q0, q1, q2, q3 = self.q.w, self.q.x, self.q.y, self.q.z

        vx = 2.0 * (q1 * q3 - q0 * q2)
        vy = 2.0 * (q0 * q1 + q2 * q3)
        vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3

        ex = acc.y * vz - acc.z * vy
        ey = acc.z * vx - acc.x * vz
        ez = acc.x * vy - acc.y * vx

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

        if self.ki > 0.0:
            self.integral = add3(self.integral, scale3(Vector3(ex, ey, ez), self.ki * dt))
        else:
            self.integral = Vector3(0.0, 0.0, 0.0)

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
