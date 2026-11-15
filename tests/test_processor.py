import math

import pytest

from triki import Config, ImuSimulator, Knob, Processor
from triki.orientation import dot, q_to_matrix


def calibrated(seed=1, config=None, **sim_kw):
    sim = ImuSimulator(seed=seed, **sim_kw)
    p = Processor(config)
    events = []
    p.on("*", lambda e: events.append(e) if e.name != "frame" else None)
    p.feed(sim.hold(1.3))
    assert not p.calibrating
    return sim, p, events


def names(events):
    return [e.name for e in events]


@pytest.mark.parametrize("gyro_first", [True, False])
def test_calibration_detects_layout_and_bias(gyro_first):
    sim, p, events = calibrated(gyro_first=gyro_first)
    info = events[0].value
    assert events[0].name == "calibrated"
    assert info["gyro_axes"] == ("ABC" if gyro_first else "DEF")
    assert abs(info["one_g_lsb"] - 2048) < 20
    assert all(abs(b - t) < 2 for b, t in zip(info["gyro_bias_lsb"], sim.gyro_bias))


def test_calibration_retries_when_moving():
    sim = ImuSimulator(seed=2)
    p = Processor()
    p.feed(sim.rotate((0, 0, 1), 90, seconds=1.2))  # moving during the first window
    assert p.calibrating
    p.feed(sim.hold(2.5))  # the window that overlaps the motion is rejected too
    assert not p.calibrating


def test_orientation_tracks_motion():
    sim, p, _ = calibrated(seed=3)
    worst = 0.0
    for i in range(104 * 20):
        t = i / 104
        w = (1.6 * math.sin(t * 0.9), 1.2 * math.sin(t * 0.6 + 1), 0.8 * math.sin(t * 0.35))
        p.feed(sim.step(w))
        true_up, est_up = q_to_matrix(sim.q)[2], q_to_matrix(p.state.quaternion)[2]
        worst = max(worst, math.degrees(math.acos(max(-1, min(1, dot(true_up, est_up))))))
    assert worst < 3.0


def test_rotation_angle_is_right():
    sim, p, _ = calibrated(seed=4)
    p.feed(sim.rotate((1, 0, 0), 90, seconds=1.0))
    p.feed(sim.hold(0.3))
    assert abs(p.state.roll - 90) < 3


def test_click_double_and_long_press():
    sim, p, events = calibrated(seed=5)
    events.clear()
    p.feed(sim.hold(0.1, button=True) + sim.hold(0.6))
    assert names(events) == ["press", "release", "click", "single_click"]

    events.clear()
    p.feed(sim.hold(0.1, button=True) + sim.hold(0.1) + sim.hold(0.1, button=True) + sim.hold(0.6))
    assert names(events).count("click") == 2
    assert names(events).count("double_click") == 1

    events.clear()
    p.feed(sim.hold(1.0, button=True) + sim.hold(0.2))
    assert names(events) == ["press", "long_press", "release"]


def test_button_debounce():
    sim, p, events = calibrated(seed=6)
    events.clear()
    # press, 1-sample bounce, stay pressed
    p.feed(sim.step(button=True) + sim.step(button=False) + sim.hold(0.2, button=True) + sim.hold(0.5))
    assert names(events).count("press") == 1


@pytest.mark.parametrize("face_down", [False, True])
def test_twist_is_clockwise_positive_on_both_faces(face_down):
    sim, p, events = calibrated(seed=7)
    if face_down:
        p.feed(sim.rotate((1, 0, 0), 180, seconds=0.6) + sim.hold(1.0))
        assert p.state.face == "down"
    events.clear()
    # clockwise seen from above: about world -Z. Face down, body Z points down -> body +Z.
    axis = (0, 0, 1) if face_down else (0, 0, -1)
    p.feed(sim.rotate(axis, 90, seconds=1.0))
    total = sum(e.value for e in events if e.name == "twist")
    assert 80 < total < 100


def test_slow_drift_is_ignored():
    sim, p, events = calibrated(seed=8)
    events.clear()
    p.feed(sim.rotate((0, 0, 1), 5, seconds=1.0))  # 5 deg/s < deadzone
    assert "twist" not in names(events)


def test_flip_events():
    sim, p, events = calibrated(seed=9)
    assert p.state.face == "up"
    events.clear()
    p.feed(sim.rotate((1, 0, 0), 180, seconds=0.6) + sim.hold(1.0))
    flips = [e.value for e in events if e.name == "flip"]
    assert flips == ["down"]
    p.feed(sim.rotate((1, 0, 0), 180, seconds=0.6) + sim.hold(1.0))
    assert [e.value for e in events if e.name == "flip"] == ["down", "up"]


def test_brief_tip_over_does_not_flip():
    sim, p, events = calibrated(seed=10, config=Config(flip_hold_s=0.5))
    events.clear()
    p.feed(sim.rotate((1, 0, 0), 180, seconds=0.4) + sim.hold(0.2) + sim.rotate((1, 0, 0), -180, seconds=0.4))
    p.feed(sim.hold(1.0))
    assert "flip" not in names(events)


def test_zero_pose():
    sim, p, _ = calibrated(seed=11)
    p.feed(sim.rotate((0, 1, 0), 40, seconds=0.5) + sim.hold(0.3))
    assert abs(p.state.pitch) > 30
    p.zero()
    p.feed(sim.hold(0.1))
    assert abs(p.state.pitch) < 1 and abs(p.state.roll) < 1


def test_handler_errors_do_not_break_processing():
    sim, p, _ = calibrated(seed=12)
    p.on("frame", lambda e: 1 / 0)
    p.feed(sim.hold(0.1))
    assert p.state.frame > 0


def test_unknown_event_name_rejected():
    with pytest.raises(ValueError):
        Processor().on("klick", print)


def test_knob():
    k = Knob(10)
    assert [k.update(d) for d in (4, 4, 4, -25)] == [0, 0, 1, -2]


def test_upside_down_mount_swaps_faces_but_not_twist():
    sim, p, events = calibrated(seed=13, config=Config(upside_down=True))
    assert p.state.face == "down"             # sensor up = cap lying on its back
    assert abs(abs(p.state.roll) - 180) < 2
    events.clear()
    p.feed(sim.rotate((0, 0, -1), 90, seconds=1.0))  # clockwise seen from above
    assert 80 < sum(e.value for e in events if e.name == "twist") < 100
    p.feed(sim.rotate((1, 0, 0), 180, seconds=0.6) + sim.hold(1.0))
    assert [e.value for e in events if e.name == "flip"] == ["up"]


def test_zero_pose_with_upside_down_mount():
    sim, p, _ = calibrated(seed=14, config=Config(upside_down=True))
    p.zero()
    p.feed(sim.hold(1.0))  # position changes once it has been held for flip_hold_s
    assert p.state.face == "up" and p.state.tilt < 1


def test_double_tap():
    sim, p, events = calibrated(seed=15)
    events.clear()
    p.feed(sim.tap(0.4) + sim.hold(0.25) + sim.tap(0.4) + sim.hold(0.6))
    assert names(events) == ["tap", "tap", "double_tap"]
    assert events[0].value > 0.3


def test_single_tap_and_slow_taps_are_not_double():
    sim, p, events = calibrated(seed=16)
    events.clear()
    p.feed(sim.tap(0.4) + sim.hold(0.9) + sim.tap(0.4) + sim.hold(0.9))
    assert names(events) == ["tap", "tap"]


def test_weak_knock_ignored():
    sim, p, events = calibrated(seed=17)
    events.clear()
    p.feed(sim.tap(0.05) + sim.hold(0.2) + sim.tap(0.05) + sim.hold(0.6))
    assert "tap" not in names(events)


def test_taps_ignored_while_handling_the_cap():
    sim, p, events = calibrated(seed=18)
    events.clear()
    # jolts while it is being turned quickly
    p.feed(sim.rotate((0, 0, 1), 30, 0.1) + sim.tap(0.5) + sim.rotate((0, 0, 1), 30, 0.1) + sim.tap(0.5))
    p.feed(sim.rotate((0, 0, 1), 30, 0.1) + sim.hold(0.8))
    assert "tap" not in names(events)


def test_button_press_jolt_is_not_a_tap():
    sim, p, events = calibrated(seed=19)
    events.clear()
    # pressing the button knocks the accelerometer one sample before the button reads pressed
    p.feed(sim.tap(0.5) + sim.hold(0.1, button=True) + sim.hold(0.2)
           + sim.tap(0.5) + sim.hold(0.1, button=True) + sim.hold(0.8))
    assert "tap" not in names(events)
    assert names(events).count("click") == 2


def test_single_click_waits_for_double():
    sim, p, events = calibrated(seed=20)
    events.clear()
    p.feed(sim.hold(0.1, button=True) + sim.hold(0.6))
    assert names(events) == ["press", "release", "click", "single_click"]
    events.clear()
    p.feed(sim.hold(0.1, button=True) + sim.hold(0.1) + sim.hold(0.1, button=True) + sim.hold(0.6))
    assert "single_click" not in names(events) and "double_click" in names(events)
