"""Print the cap's orientation 20 times a second (blocking API).

    python examples/01_print_orientation.py          # real cap
    python examples/01_print_orientation.py --sim    # no hardware needed
"""

import sys
import time

import triki

with triki.connect(simulate="--sim" in sys.argv) as cap:
    print("Calibrating - keep the cap still...")
    cap.wait_calibrated()
    print("Go! (Ctrl+C to quit)")
    try:
        while True:
            s = cap.state
            print(f"\rroll {s.roll:+7.1f}  pitch {s.pitch:+7.1f}  yaw {s.yaw:+7.1f}  "
                  f"tilt {s.tilt:5.1f}  face {s.face or '-':4}", end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print()
