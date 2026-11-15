"""React to gestures with decorators (blocking API).

    python examples/02_gestures.py [--sim]
"""

import sys

import triki

cap = triki.BackgroundTriki(simulate="--sim" in sys.argv)
knob = triki.Knob(degrees_per_step=15)


@cap.on("click")
def on_click(e):
    print("click")


@cap.on("double_click")
def on_double(e):
    print("double click!")


@cap.on("long_press")
def on_long(e):
    print("long press")


@cap.on("flip")
def on_flip(e):
    print("cap is now face", e.value)


@cap.on("twist")
def on_twist(e):
    steps = knob.update(e.value)
    if steps:
        print("knob", "+" * steps if steps > 0 else "-" * -steps)


with cap:
    print("Calibrating - keep the cap still...")
    cap.wait_calibrated()
    input("Click, double-click, hold, twist or flip the cap. Enter quits.\n")
