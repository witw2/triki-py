"""A fake cap that produces byte-exact Triki packets - for demos and tests."""

from __future__ import annotations

import asyncio
import math
import random
from typing import Callable, Optional, Tuple

from .device import Controller
from .orientation import IDENTITY, Quat, Vec3, mat_vec, q_integrate, q_to_matrix
from .processor import Config
from .protocol import encode_frame

MotionFn = Callable[[float], Vec3]     # t -> body angular velocity (rad/s)
ButtonFn = Callable[[float], bool]     # t -> pressed


def demo_motion(t: float) -> Vec3:
    return (1.6 * math.sin(t * 0.9), 1.2 * math.sin(t * 0.6 + 1.0), 0.8 * math.sin(t * 0.35))


class ImuSimulator:
    """Integrates a true orientation and renders it as raw Triki packets.

    Layout matches the real cap's suspected layout: ABC = gyro, DEF = accel.
    """

    def __init__(self, sample_rate: float = 104.0, gyro_scale: float = 0.070, one_g: float = 2048.0,
                 gyro_bias: Tuple[int, int, int] = (12, -7, 4), gyro_noise: float = 3.0,
                 accel_noise: float = 8.0, seed: Optional[int] = None, gyro_first: bool = True):
        self.sample_rate = sample_rate
        self.gyro_scale = gyro_scale
        self.one_g = one_g
        self.gyro_bias = gyro_bias
        self.gyro_noise = gyro_noise
        self.accel_noise = accel_noise
        self.gyro_first = gyro_first
        self.rng = random.Random(seed)
        self.q: Quat = IDENTITY  # true body -> world orientation

    def step(self, w_body: Vec3 = (0.0, 0.0, 0.0), button: bool = False,
             accel_offset_g: Vec3 = (0.0, 0.0, 0.0)) -> bytes:
        dt = 1.0 / self.sample_rate
        self.q = q_integrate(self.q, w_body, dt)
        m = q_to_matrix(self.q)
        grav_body = mat_vec(tuple(zip(*m)), (0.0, 0.0, 1.0))  # R^T * up
        gauss = self.rng.gauss
        gyro = [math.degrees(w_body[i]) / self.gyro_scale + self.gyro_bias[i] + gauss(0, self.gyro_noise)
                for i in range(3)]
        accel = [(grav_body[i] + accel_offset_g[i]) * self.one_g + gauss(0, self.accel_noise) for i in range(3)]
        return encode_frame(button, gyro + accel if self.gyro_first else accel + gyro)

    def hold(self, seconds: float, button: bool = False) -> bytes:
        return b"".join(self.step(button=button) for _ in range(int(seconds * self.sample_rate)))

    def tap(self, strength_g: float = 0.4, button: bool = False) -> bytes:
        """A knock on the table: a short ringing jolt along the cap's axis (4 samples)."""
        ring = (1.0, -0.7, 0.35, -0.1)
        return b"".join(self.step(button=button, accel_offset_g=(0.05 * k * strength_g, 0.0, k * strength_g))
                        for k in ring)

    def rotate(self, axis: Vec3, degrees: float, seconds: float, button: bool = False) -> bytes:
        """Rotate about a body axis at constant speed."""
        n = max(1, int(seconds * self.sample_rate))
        rate = math.radians(degrees) / (n / self.sample_rate)
        w = (axis[0] * rate, axis[1] * rate, axis[2] * rate)
        return b"".join(self.step(w, button) for _ in range(n))


class SimulatedTriki(Controller):
    """Drop-in stand-in for :class:`~triki.Triki` that runs without hardware."""

    def __init__(self, config: Optional[Config] = None, motion: MotionFn = demo_motion,
                 button: Optional[ButtonFn] = None, seed: Optional[int] = None):
        super().__init__(config)
        self.sim = ImuSimulator(sample_rate=self.config.sample_rate, seed=seed)
        self.motion = motion
        self.button = button or (lambda t: int(t) % 6 == 5)
        self._task: Optional[asyncio.Task] = None

    @property
    def is_connected(self) -> bool:
        return self._task is not None and not self._task.done()

    async def connect(self) -> None:
        self.processor.start_calibration()
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def disconnect(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        dt = 1.0 / self.sim.sample_rate
        start = loop.time()
        n = 0
        t = 0.0  # motion time, starts once calibration is done
        while True:
            if self.processor.calibrating:  # a real user holds the cap still, so do we
                self.processor.feed(self.sim.step())
            else:
                self.processor.feed(self.sim.step(self.motion(t), self.button(t)))
                t += dt
            n += 1
            delay = start + n * dt - loop.time()
            await asyncio.sleep(max(0.0, delay))
