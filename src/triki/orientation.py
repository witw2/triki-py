"""Quaternion helpers and a Mahony gyro+accelerometer fusion filter.

Quaternions are ``(w, x, y, z)`` tuples that rotate the cap's body frame into
the world frame (world Z points up).
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

Quat = Tuple[float, float, float, float]
Vec3 = Tuple[float, float, float]
Mat3 = Tuple[Vec3, Vec3, Vec3]

IDENTITY: Quat = (1.0, 0.0, 0.0, 0.0)


def q_mul(a: Quat, b: Quat) -> Quat:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def q_conj(q: Quat) -> Quat:
    return (q[0], -q[1], -q[2], -q[3])


def q_normalize(q: Sequence[float]) -> Quat:
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def q_from_euler(roll: float, pitch: float, yaw: float) -> Quat:
    """Radians, aerospace ZYX order."""
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy)


def q_to_euler(q: Quat) -> Vec3:
    """Returns (roll, pitch, yaw) in radians."""
    w, x, y, z = q
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


def q_to_matrix(q: Quat) -> Mat3:
    w, x, y, z = q
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
            (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
            (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)))


def q_integrate(q: Quat, w_body: Vec3, dt: float) -> Quat:
    """Advance q by body angular velocity (rad/s) for dt seconds."""
    dq = q_mul(q, (0.0, w_body[0], w_body[1], w_body[2]))
    return q_normalize([qc + 0.5 * dqc * dt for qc, dqc in zip(q, dq)])


def rotate(q: Quat, v: Vec3) -> Vec3:
    """Body vector -> world vector."""
    return mat_vec(q_to_matrix(q), v)


def mat_vec(m: Mat3, v: Vec3) -> Vec3:
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def q_from_gravity(accel: Vec3) -> Quat:
    """Orientation (yaw = 0) whose 'down' matches a resting accelerometer reading."""
    ax, ay, az = accel
    roll = math.atan2(ay, az)
    pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az))
    return q_from_euler(roll, pitch, 0.0)


class Mahony:
    """Mahony complementary filter. Without a magnetometer yaw drifts slowly."""

    def __init__(self, kp: float = 1.5, ki: float = 0.02):
        self.kp = kp
        self.ki = ki
        self.q: Quat = IDENTITY
        self._integral: Vec3 = (0.0, 0.0, 0.0)

    def reset(self, q: Quat = IDENTITY) -> None:
        self.q = q
        self._integral = (0.0, 0.0, 0.0)

    def update(self, gyro_rad: Vec3, accel: Vec3, dt: float) -> Quat:
        gx, gy, gz = gyro_rad
        an = math.sqrt(dot(accel, accel))
        if an > 1e-9:
            a = (accel[0] / an, accel[1] / an, accel[2] / an)
            w, x, y, z = self.q
            v = (2 * (x * z - w * y), 2 * (w * x + y * z), w * w - x * x - y * y + z * z)
            ex, ey, ez = cross(a, v)
            ix, iy, iz = self._integral
            self._integral = (ix + self.ki * ex * dt, iy + self.ki * ey * dt, iz + self.ki * ez * dt)
            ix, iy, iz = self._integral
            gx += self.kp * ex + ix
            gy += self.kp * ey + iy
            gz += self.kp * ez + iz
        self.q = q_integrate(self.q, (gx, gy, gz), dt)
        return self.q
