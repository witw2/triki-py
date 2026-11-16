"""Turns raw Triki data into orientation, state and high-level events.

The :class:`Processor` is pure Python with no I/O, so it can be driven by a
real device, the simulator, a recorded log or a unit test alike.

Events (each handler receives one :class:`Event`):

=================  ==========================================================
``frame``          every processed sample (``event.state``)
``calibrated``     calibration finished, ``value`` = dict with details
                   (``restored`` is True when a previous calibration was reused)
``press``          button went down
``release``        button went up, ``value`` = hold duration in seconds
``click``          short press released (fires immediately, also for each half of a double click)
``single_click``   a click that was *not* followed by a second one (arrives
                   ``Config.double_click_s`` later) - use it together with ``double_click``
``double_click``   second click within ``Config.double_click_s``
``long_press``     button held for ``Config.long_press_s`` (fires while held,
                   that press then produces no ``click``)
``twist``          cap rotated like a knob, ``value`` = degrees this sample,
                   positive = clockwise when looking down at the cap
``flip``           cap settled face ``"up"`` or ``"down"`` (``value``)
``tap``            a knock on the table (or on the cap) while it lies still,
                   ``value`` = strength in g
``double_tap``     second tap within ``Config.double_tap_s``
``tilt``           cap tipped onto one edge and held there: ``value`` is ``"left"``,
                   ``"right"`` (towards -X / +X), ``"forward"`` or ``"back"`` (+Y / -Y);
                   repeats every ``Config.tilt_repeat_s`` while held
``shake``          a few strong back-and-forth jolts
``battery``        battery level changed, ``value`` = percent (real caps only)
``status``         a non-IMU frame arrived, ``value`` = its raw bytes
=================  ==========================================================

Timing uses *sample time* (frame count / sample rate), so behaviour is
deterministic and independent of BLE packet batching.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from .orientation import (IDENTITY, Mahony, Quat, Vec3, dot, q_conj, q_from_gravity, q_mul,
                          q_to_euler, q_to_matrix, rotate)
from .protocol import FrameParser, RawFrame

log = logging.getLogger("triki")

_FLIP_X: Quat = (0.0, 1.0, 0.0, 0.0)  # 180 deg about X

EVENTS = ("frame", "calibrated", "press", "release", "click", "single_click", "double_click",
          "long_press", "twist", "flip", "tap", "double_tap", "tilt", "shake", "battery", "status")


@dataclass
class Config:
    # sensor
    gyro_scale: float = 0.070        # dps per LSB (LSM6-style chip at +-2000 dps)
    sample_rate: float = 104.0       # Hz, used as the integration time step
    layout: str = "auto"             # "auto", "gyro_first" (ABC=gyro) or "accel_first"
    invert_gyro: bool = False
    upside_down: bool = False        # sensor sits flipped in the cap: its +Z points into the cap
    # fusion
    kp: float = 1.5                  # how strongly the accelerometer corrects tilt
    ki: float = 0.02
    # calibration
    calib_frames: int = 120
    calib_max_noise: float = 40.0    # max std of any axis (LSB) to accept; otherwise retry
    calib_max_bias_dps: float = 10.0  # larger gyro mean = cap was spinning, retry
    calib_max_attempts: int = 5
    # gestures
    debounce_s: float = 0.03
    double_click_s: float = 0.35
    long_press_s: float = 0.6
    twist_deadzone_dps: float = 10.0
    twist_max_tilt_deg: float = 35.0  # twist only counts while the cap is roughly flat
    flip_threshold: float = 0.6       # |up_z| needed to count as face up/down
    flip_hold_s: float = 0.5
    # taps (knocks on the table, detected as sudden accelerometer jumps while the cap is still)
    tap_threshold_g: float = 0.15     # jump in acceleration between two samples that counts as a tap
    tap_max_rotation_dps: float = 40.0  # ignore taps while the cap is being turned / picked up
    tap_refractory_s: float = 0.08    # one knock rings for a few samples - count it once
    tap_confirm_s: float = 0.15       # wait this long so a button press can veto the tap
    double_tap_s: float = 0.5
    # tilt (tip the cap onto an edge and hold)
    tilt_threshold_deg: float = 25.0
    tilt_max_deg: float = 65.0        # steeper = it is being flipped, not tilted
    tilt_hold_s: float = 0.25
    tilt_repeat_s: float = 0.6        # 0 = fire once per tilt
    # shake
    shake_threshold_g: float = 0.8    # acceleration must stray this far from gravity...
    shake_count: int = 4              # ...this many times...
    shake_window_s: float = 1.0       # ...within this long


@dataclass(frozen=True)
class State:
    t: float = 0.0                         # sample time in seconds
    frame: int = 0
    button: bool = False
    raw: Tuple[int, ...] = (0,) * 6
    gyro_dps: Vec3 = (0.0, 0.0, 0.0)       # body frame, bias removed
    accel_g: Vec3 = (0.0, 0.0, 0.0)        # body frame
    quaternion: Quat = IDENTITY            # body -> world, relative to the zero pose
    roll: float = 0.0                      # degrees
    pitch: float = 0.0
    yaw: float = 0.0                       # drifts slowly (no magnetometer)
    up_z: float = 1.0                      # +1 face up, -1 face down
    face: Optional[str] = None             # "up", "down" or None (on edge / unknown)
    twist_dps: float = 0.0                 # + = clockwise seen from above
    impact_g: float = 0.0                  # accelerometer jump this sample (what taps are measured by)
    lean_x: float = 0.0                    # degrees the +X edge is tipped down (- = the -X edge)
    lean_y: float = 0.0                    # degrees the +Y edge is tipped down
    calibrating: bool = True

    @property
    def tilt(self) -> float:
        """Degrees between the cap's face normal and straight up."""
        return math.degrees(math.acos(max(-1.0, min(1.0, self.up_z))))


@dataclass(frozen=True)
class Event:
    name: str
    t: float
    state: State
    value: Any = None


Handler = Callable[[Event], Any]


class EventEmitter:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[Handler]] = {}

    def on(self, name: str, handler: Optional[Handler] = None):
        """Register a handler. Usable directly or as a decorator: ``@cap.on("click")``.
        ``"*"`` receives every event."""
        if name != "*" and name not in EVENTS:
            raise ValueError(f"unknown event {name!r}; choose from {EVENTS}")

        def register(fn: Handler) -> Handler:
            self._handlers.setdefault(name, []).append(fn)
            return fn
        return register(handler) if handler else register

    def off(self, name: str, handler: Handler) -> None:
        try:
            self._handlers.get(name, []).remove(handler)
        except ValueError:
            pass

    def emit(self, event: Event) -> None:
        for fn in self._handlers.get(event.name, []) + self._handlers.get("*", []):
            try:
                fn(event)
            except Exception:  # a broken handler must not kill the data stream
                log.exception("handler for %r failed", event.name)


@dataclass
class _Calibration:
    gyro_bias: Vec3 = (0.0, 0.0, 0.0)
    one_g: float = 2048.0
    gyro_first: bool = True


class Processor(EventEmitter):
    def __init__(self, config: Optional[Config] = None):
        super().__init__()
        self.config = config or Config()
        self.parser = FrameParser(on_status=self._on_status)
        self.fusion = Mahony(self.config.kp, self.config.ki)
        self.calibration = _Calibration()
        self.state = State()
        self._q_ref: Quat = IDENTITY
        self._zero_pending = False
        self._calib: List[Tuple[int, ...]] = []
        self._calib_attempts = 0
        self._calibrating = True
        self._restore: Optional[dict] = None
        self._has_calibration = False
        self._frames = 0
        self._last_data = time.monotonic()
        # button
        self._button = False
        self._last_edge = -1e9
        self._down_at = 0.0
        self._long_fired = False
        self._last_click = -1e9
        self._pending_single: Optional[float] = None
        # flip
        self._pending_face: Optional[str] = None
        self._pending_since = 0.0
        # taps
        self._prev_accel: Optional[Vec3] = None
        self._tap_candidates: List[Tuple[float, float]] = []  # (time, strength)
        self._last_candidate = -1e9
        self._quiet_until = 0.0      # no taps until then (motion / button)
        self._last_tap = -1e9
        # tilt / shake
        self._tilt_pending: Optional[str] = None
        self._tilt_since = 0.0
        self._tilt_fired_at: Optional[float] = None
        self._shake_peaks: List[float] = []
        self._gravity_lp: Optional[Vec3] = None
        self._shake_quiet_until = 0.0

    # ------------------------------------------------------------- control
    @property
    def calibrating(self) -> bool:
        return self._calibrating

    def start_calibration(self, restore: Optional[dict] = None) -> None:
        """Keep the cap still for ~calib_frames samples after calling this.

        ``restore``: a previous ``calibrated`` event value (or :meth:`calibration_snapshot`).
        It is applied on the first sample instead, so e.g. a reconnect after the cap
        woke up from sleep is usable immediately.
        """
        self._calibrating = True
        self._calib = []
        self._calib_attempts = 0
        self._restore = restore if _valid_snapshot(restore) else None

    def calibration_snapshot(self) -> Optional[dict]:
        """Current calibration as a plain (JSON-friendly) dict, or None before the first one."""
        if not self._has_calibration:
            return None
        cal = self.calibration
        return {"gyro_axes": "ABC" if cal.gyro_first else "DEF", "one_g_lsb": cal.one_g,
                "gyro_bias_lsb": list(cal.gyro_bias)}

    @property
    def seconds_since_data(self) -> float:
        """Wall-clock seconds since the last bytes arrived (a sleeping cap goes quiet)."""
        return time.monotonic() - self._last_data

    def zero(self) -> None:
        """Treat the current pose as 'flat, facing forward' (also clears yaw drift)."""
        self._zero_pending = True

    # ------------------------------------------------------------- input
    def feed(self, data: bytes) -> None:
        """Feed raw notification bytes (any fragmentation)."""
        self._last_data = time.monotonic()
        for frame in self.parser.feed(data):
            self.process(frame)

    def process(self, frame: RawFrame) -> State:
        cfg = self.config
        self._frames += 1
        t = self._frames / cfg.sample_rate
        dt = 1.0 / cfg.sample_rate
        events: List[Tuple[str, Any]] = []
        if self._frames == 1 and frame.button:
            # held already when data started (e.g. the press that woke the cap): no click
            self._button, self._last_edge, self._down_at, self._long_fired = True, t, t, True

        if self._calibrating and self._restore is not None:
            events.append(("calibrated", self._apply_restore(frame)))
            self.state = replace(self.state, t=t, frame=self._frames, button=frame.button,
                                 raw=frame.axes, calibrating=False)
        elif self._calibrating:
            self._calib.append(frame.axes)
            if len(self._calib) >= cfg.calib_frames:
                info = self._finish_calibration()
                if info is not None:
                    events.append(("calibrated", info))
            self.state = replace(self.state, t=t, frame=self._frames, button=frame.button,
                                 raw=frame.axes, calibrating=self._calibrating)
        else:
            self._update_motion(frame, t, dt, events)

        self._update_button(frame.button, t, events)
        st = self.state
        for name, value in events:
            self.emit(Event(name, t, st, value))
        self.emit(Event("frame", t, st))
        return st

    # ------------------------------------------------------------- internals
    def _on_status(self, raw: bytes) -> None:
        self.emit(Event("status", self.state.t, self.state, raw))

    def _finish_calibration(self) -> Optional[dict]:
        cfg = self.config
        n = len(self._calib)
        mean = [sum(f[i] for f in self._calib) / n for i in range(6)]
        std = [math.sqrt(sum((f[i] - mean[i]) ** 2 for f in self._calib) / n) for i in range(6)]
        mag_abc = math.sqrt(sum(m * m for m in mean[:3]))
        mag_def = math.sqrt(sum(m * m for m in mean[3:]))
        if cfg.layout == "gyro_first":
            gyro_first = True
        elif cfg.layout == "accel_first":
            gyro_first = False
        else:  # the accelerometer is the triple that sees gravity
            gyro_first = mag_def > mag_abc
        g_idx, a_idx = (range(0, 3), range(3, 6)) if gyro_first else (range(3, 6), range(0, 3))
        noise = max(std)
        bias_dps = math.sqrt(sum(mean[i] ** 2 for i in g_idx)) * cfg.gyro_scale
        still = noise <= cfg.calib_max_noise and bias_dps <= cfg.calib_max_bias_dps
        self._calib_attempts += 1
        if not still and self._calib_attempts < cfg.calib_max_attempts:
            log.info("cap moved during calibration (noise %.0f LSB, spin %.1f dps) - retrying",
                     noise, bias_dps)
            self._calib = []
            return None

        a_mean = tuple(mean[i] for i in a_idx)
        self._finish(_Calibration(
            gyro_bias=tuple(mean[i] for i in g_idx),
            one_g=math.sqrt(dot(a_mean, a_mean)) or 2048.0,
            gyro_first=gyro_first,
        ), a_mean)
        return {
            "gyro_axes": "ABC" if gyro_first else "DEF",
            "one_g_lsb": self.calibration.one_g,
            "gyro_bias_lsb": self.calibration.gyro_bias,
            "noise_lsb": noise,
            "still": still,
        }

    def _finish(self, cal: "_Calibration", accel_raw) -> None:
        self.calibration = cal
        self._has_calibration = True
        self.fusion.reset(q_from_gravity(tuple(accel_raw)))
        self._q_ref = IDENTITY
        self._calibrating = False
        self._calib = []
        up_z = q_to_matrix(self._cap_q(self.fusion.q))[2][2]
        face = self._face_of(up_z)
        self._pending_face, self._pending_since = face, 0.0
        self.state = replace(self.state, face=face, up_z=up_z)

    def _apply_restore(self, frame: RawFrame) -> dict:
        r, self._restore = self._restore, None
        cal = _Calibration(gyro_bias=tuple(float(v) for v in r["gyro_bias_lsb"]),
                           one_g=float(r["one_g_lsb"]), gyro_first=r["gyro_axes"] == "ABC")
        self._finish(cal, frame.axes[3:] if cal.gyro_first else frame.axes[:3])
        return {"gyro_axes": r["gyro_axes"], "one_g_lsb": cal.one_g, "gyro_bias_lsb": cal.gyro_bias,
                "noise_lsb": 0.0, "still": True, "restored": True}

    def _cap_q(self, q_sensor: Quat) -> Quat:
        """Sensor orientation -> cap orientation (applies Config.upside_down)."""
        return q_mul(q_sensor, _FLIP_X) if self.config.upside_down else q_sensor

    def _face_of(self, up_z: float) -> Optional[str]:
        th = self.config.flip_threshold
        return "up" if up_z > th else "down" if up_z < -th else None

    def _update_motion(self, frame: RawFrame, t: float, dt: float, events) -> None:
        cfg, cal = self.config, self.calibration
        axes = frame.axes
        g_raw, a_raw = (axes[:3], axes[3:]) if cal.gyro_first else (axes[3:], axes[:3])
        sign = -1.0 if cfg.invert_gyro else 1.0
        gyro = tuple(sign * (g_raw[i] - cal.gyro_bias[i]) * cfg.gyro_scale for i in range(3))
        accel = tuple(v / cal.one_g for v in a_raw)

        self.fusion.kp, self.fusion.ki = cfg.kp, cfg.ki
        q_abs = self.fusion.update(tuple(math.radians(v) for v in gyro), accel, dt)
        q_cap = self._cap_q(q_abs)
        if self._zero_pending:
            self._q_ref = q_cap
            self._zero_pending = False
        q = q_mul(q_cap, q_conj(self._q_ref))
        roll, pitch, yaw = (math.degrees(v) for v in q_to_euler(q))
        up_z = q_to_matrix(q)[2][2]

        # Knob: spin about the *world* vertical, so clockwise-from-above means the
        # same thing whether the cap is face up or face down.
        twist = -rotate(q_abs, gyro)[2]
        flat = abs(up_z) >= math.cos(math.radians(cfg.twist_max_tilt_deg))
        if flat and abs(twist) >= cfg.twist_deadzone_dps:
            events.append(("twist", twist * dt))

        face = self.state.face
        cand = self._face_of(up_z)
        if cand != self._pending_face:
            self._pending_face, self._pending_since = cand, t
        elif cand is not None and cand != face and t - self._pending_since >= cfg.flip_hold_s:
            face = cand
            events.append(("flip", face))

        impact = self._update_taps(gyro, accel, t, events)
        lean_x, lean_y = self._update_tilt(q, t, events)
        self._update_shake(accel, t, events)

        self.state = State(t=t, frame=self._frames, button=frame.button, raw=axes,
                           gyro_dps=gyro, accel_g=accel, quaternion=q,
                           roll=roll, pitch=pitch, yaw=yaw, up_z=up_z, face=face,
                           twist_dps=twist, impact_g=impact, lean_x=lean_x, lean_y=lean_y,
                           calibrating=False)

    def _update_tilt(self, q: Quat, t: float, events) -> Tuple[float, float]:
        cfg = self.config
        m = q_to_matrix(q)
        up = (m[2][0], m[2][1], m[2][2])            # world 'up' seen from the cap
        side = 1.0 if up[2] >= 0 else -1.0          # face down mirrors forward/back
        flat = abs(up[2])
        lean_x = math.degrees(math.atan2(-up[0], flat))
        lean_y = math.degrees(math.atan2(-up[1], flat)) * side
        big = max(abs(lean_x), abs(lean_y))
        direction = None
        if cfg.tilt_threshold_deg <= big <= cfg.tilt_max_deg:
            if abs(lean_x) >= abs(lean_y):
                direction = "right" if lean_x > 0 else "left"
            else:
                direction = "forward" if lean_y > 0 else "back"
        if direction != self._tilt_pending:
            self._tilt_pending, self._tilt_since, self._tilt_fired_at = direction, t, None
        elif direction is not None:
            if self._tilt_fired_at is None:
                if t - self._tilt_since >= cfg.tilt_hold_s:
                    self._tilt_fired_at = t
                    events.append(("tilt", direction))
            elif cfg.tilt_repeat_s > 0 and t - self._tilt_fired_at >= cfg.tilt_repeat_s:
                self._tilt_fired_at = t
                events.append(("tilt", direction))
        return lean_x, lean_y

    def _update_shake(self, accel: Vec3, t: float, events) -> None:
        cfg = self.config
        # distance from a slowly tracked gravity vector: catches sideways shakes too
        g = self._gravity_lp or accel
        g = tuple(gc + 0.05 * (ac - gc) for gc, ac in zip(g, accel))
        self._gravity_lp = g
        dev = math.sqrt(sum((ac - gc) ** 2 for ac, gc in zip(accel, g)))
        if dev >= cfg.shake_threshold_g and t < self._shake_quiet_until:
            self._shake_quiet_until = t + 0.6    # still shaking: one shake = one event
            return
        if dev >= cfg.shake_threshold_g and (not self._shake_peaks or t - self._shake_peaks[-1] >= 0.08):
            self._shake_peaks.append(t)
        self._shake_peaks = [p for p in self._shake_peaks if t - p <= cfg.shake_window_s]
        if len(self._shake_peaks) >= cfg.shake_count and t >= self._shake_quiet_until:
            events.append(("shake", None))
            self._shake_peaks = []
            self._shake_quiet_until = t + 0.6
            self._tap_candidates = []             # the jolts of a shake are not taps
            self._quiet_until = max(self._quiet_until, t + 0.5)

    def _update_taps(self, gyro: Vec3, accel: Vec3, t: float, events) -> float:
        cfg = self.config
        prev, self._prev_accel = self._prev_accel, accel
        if prev is None:
            return 0.0
        impact = math.sqrt(sum((a - b) ** 2 for a, b in zip(accel, prev)))

        if math.sqrt(dot(gyro, gyro)) > cfg.tap_max_rotation_dps:
            # being handled: drop knocks that were really the start of this movement
            self._quiet_until = t + 0.25
            self._tap_candidates = [c for c in self._tap_candidates if c[0] < t - 0.1]
        if (impact >= cfg.tap_threshold_g and t >= self._quiet_until and not self._button
                and t - self._last_candidate >= cfg.tap_refractory_s):
            self._tap_candidates.append((t, impact))
            self._last_candidate = t
        elif self._tap_candidates and self._tap_candidates[-1][0] == self._last_candidate \
                and t - self._last_candidate < cfg.tap_refractory_s:
            # same knock still ringing: keep its peak strength
            tc, strength = self._tap_candidates[-1]
            self._tap_candidates[-1] = (tc, max(strength, impact))

        while self._tap_candidates and t - self._tap_candidates[0][0] >= cfg.tap_confirm_s:
            tc, strength = self._tap_candidates.pop(0)
            events.append(("tap", strength))
            if tc - self._last_tap <= cfg.double_tap_s:
                events.append(("double_tap", strength))
                self._last_tap = -1e9
            else:
                self._last_tap = tc
        return impact

    def _update_button(self, pressed: bool, t: float, events) -> None:
        cfg = self.config
        if pressed != self._button and t - self._last_edge >= cfg.debounce_s:
            self._button = pressed
            self._last_edge = t
            # pressing the button jolts the accelerometer: that is not a tap
            self._tap_candidates = [c for c in self._tap_candidates if c[0] < t - cfg.tap_confirm_s]
            self._quiet_until = max(self._quiet_until, t + 0.25)
            if pressed:
                self._down_at, self._long_fired = t, False
                events.append(("press", None))
            else:
                events.append(("release", t - self._down_at))
                if not self._long_fired:
                    events.append(("click", None))
                    if t - self._last_click <= cfg.double_click_s:
                        events.append(("double_click", None))
                        self._last_click = -1e9  # a triple click is not two doubles
                        self._pending_single = None
                    else:
                        self._last_click = t
                        self._pending_single = t
        if self._button and not self._long_fired and t - self._down_at >= cfg.long_press_s:
            self._long_fired = True
            events.append(("long_press", None))
        if (self._pending_single is not None and not self._button
                and t - self._pending_single > cfg.double_click_s):
            self._pending_single = None
            events.append(("single_click", None))


def _valid_snapshot(r) -> bool:
    try:
        return (r["gyro_axes"] in ("ABC", "DEF") and float(r["one_g_lsb"]) > 0
                and len(r["gyro_bias_lsb"]) == 3)
    except (TypeError, KeyError, ValueError):
        return False


class Knob:
    """Turns ``twist`` degrees into whole steps, e.g. one volume notch per 7.2 deg.

    Example::

        knob = Knob(degrees_per_step=7.2)

        @cap.on("twist")
        def on_twist(e):
            steps = knob.update(e.value)   # +n clockwise, -n counter-clockwise
            if steps:
                print("turned", steps, "notches")
    """

    def __init__(self, degrees_per_step: float = 7.2):
        self.degrees_per_step = degrees_per_step
        self._acc = 0.0

    def update(self, delta_deg: float) -> int:
        self._acc += delta_deg
        steps = int(self._acc / self.degrees_per_step)
        self._acc -= steps * self.degrees_per_step
        return steps

    def reset(self) -> None:
        self._acc = 0.0
