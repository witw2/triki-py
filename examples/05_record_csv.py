"""Record raw frames + computed state to CSV - useful for gesture work or protocol research.

    python examples/05_record_csv.py out.csv [seconds] [--sim]
"""

import csv
import sys
import time

import triki

path = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "triki_log.csv"
seconds = float(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else 10.0

with open(path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["t", "button", "A", "B", "C", "D", "E", "F", "gx", "gy", "gz", "ax", "ay", "az",
                "roll", "pitch", "yaw", "event"])

    def row(e):
        if e.name == "twist":
            return
        s = e.state
        w.writerow([f"{s.t:.4f}", int(s.button), *s.raw, *(f"{v:.3f}" for v in s.gyro_dps + s.accel_g),
                    f"{s.roll:.2f}", f"{s.pitch:.2f}", f"{s.yaw:.2f}", "" if e.name == "frame" else e.name])

    cap = triki.BackgroundTriki(simulate="--sim" in sys.argv)
    cap.on("*", row)
    with cap:
        print(f"Recording {seconds:.0f} s to {path} ...")
        time.sleep(seconds)
print("done")
