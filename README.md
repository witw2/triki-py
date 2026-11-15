# triki-py

Python library for the **Triki** bottle-cap motion controller — a crown-cap
sized Bluetooth LE device with an accelerometer, a gyroscope and a button.

It handles the BLE connection, reassembles the data stream, calibrates the
sensors, tracks the cap's 3D orientation and turns motion into simple events:
**click, double click, long press, twist (knob) and flip**.

```python
import triki

with triki.connect() as cap:                      # finds the nearest Triki
    cap.on("click", lambda e: print("click!"))
    cap.on("flip",  lambda e: print("face", e.value))
    cap.wait_calibrated()                         # keep it still ~1 s
    input("Enter to quit\n")
```

No cap at hand? Everything works with the built-in simulator:
`triki.connect(simulate=True)`.

> ⚠️ Unofficial and reverse-engineered. Not affiliated with the maker of the
> Triki. Parts of the protocol are educated guesses — see
> [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Install

```bash
pip install git+https://github.com/witw2/triki-py.git
# with the Windows audio helpers used by examples/04_windows_audio.py
pip install "triki[windows] @ git+https://github.com/witw2/triki-py.git"
```

For development:

```bash
git clone https://github.com/witw2/triki-py.git
cd triki-py
pip install -e ".[dev]"
pytest
```

Requires Python 3.9+ and [bleak](https://github.com/hbldh/bleak) (installed
automatically), which supports Windows, macOS and Linux.

## Command line

```bash
triki scan              # list nearby BLE devices, Trikis marked with *
triki monitor           # live roll / pitch / yaw readout
triki events            # print gestures as they happen
triki monitor --sim     # same, with the simulator
```

## Two ways to use it

**Blocking / threaded** — for scripts and GUIs (tkinter, pygame, ...):

```python
cap = triki.connect(address=None, simulate=False)   # returns once streaming
cap.state.roll                                     # poll the latest state
cap.on("twist", handler)                           # or react to events
cap.stop()
```

Handlers run on a background thread. In a GUI, either poll `cap.state` from
your frame loop, or hand work over to the GUI thread (`root.after(0, ...)`).

**asyncio**:

```python
async with triki.Triki() as cap:          # or triki.SimulatedTriki()
    await cap.wait_calibrated()
    e = await cap.wait_for("double_click", timeout=10)
    async for e in cap.events("click", "flip"):
        ...
```

## State

`cap.state` is an immutable snapshot, updated ~104 times a second:

| field | meaning |
|---|---|
| `roll`, `pitch`, `yaw` | degrees, relative to the zero pose. Yaw drifts slowly (no compass) |
| `tilt` | degrees between the cap's face and straight up |
| `up_z` | +1 face up … −1 face down |
| `face` | `"up"`, `"down"` or `None` while on its edge |
| `twist_dps` | spin speed about the vertical axis, + = clockwise seen from above |
| `quaternion` | `(w, x, y, z)` body → world |
| `gyro_dps`, `accel_g` | calibrated sensor readings in the cap's own frame |
| `button` | button currently held |
| `raw` | the six raw int16 values (A..F) |
| `t`, `frame` | sample time (s) and frame counter |

## Events

| event | `e.value` |
|---|---|
| `press` / `release` | — / hold duration (s) |
| `click`, `double_click` | — |
| `long_press` | — (fires while still held; that press gives no `click`) |
| `twist` | degrees turned this sample, + = clockwise. Use `triki.Knob` to get whole notches |
| `flip` | `"up"` or `"down"` once the cap has settled |
| `calibrated` | dict: detected gyro axes, 1 g in LSB, gyro bias, noise |
| `status` | raw bytes of a non-IMU frame (protocol research) |
| `frame` | — (every sample) |
| `*` | every event |

Every event has `e.name`, `e.t` and `e.state`.

## Calibration and zero pose

On connect the cap calibrates itself: **keep it still for about a second**. If it
moves, calibration retries automatically. Call `cap.calibrate()` to redo it.

If the cap reports "face up" while it lies on its back, set
`Config(upside_down=True)`: the sensor is mounted flipped. This is permanent,
unlike `cap.zero()`, which is reset by every calibration.

`cap.zero()` declares the current pose as "flat, facing forward". Use it to
reset yaw drift, or if the sensor inside your cap is mounted at an angle
(e.g. face up and face down come out swapped).

## Tuning

```python
cfg = triki.Config(
    double_click_s=0.35, long_press_s=0.6,
    twist_deadzone_dps=10, twist_max_tilt_deg=35,
    flip_threshold=0.6, flip_hold_s=0.5,
    gyro_scale=0.070, sample_rate=104,     # sensor assumptions, see PROTOCOL.md
    kp=1.5,                                # how hard the accelerometer corrects tilt
    upside_down=False,                     # True if the sensor sits flipped in your cap
)
cap = triki.connect(config=cfg)
```

The `Config` object can be changed while running (`cap.config.kp = 3`).

## Examples

| file | what it shows |
|---|---|
| [01_print_orientation.py](examples/01_print_orientation.py) | polling `cap.state` |
| [02_gestures.py](examples/02_gestures.py) | decorators for click / double / long / twist / flip |
| [03_async_events.py](examples/03_async_events.py) | scanning and the asyncio event stream |
| [04_windows_audio.py](examples/04_windows_audio.py) | volume knob, mic mute, output switch on Windows |
| [05_record_csv.py](examples/05_record_csv.py) | logging raw + processed data to CSV |

All accept `--sim`.

## Without hardware: the simulator

`triki.ImuSimulator` produces byte-exact Triki packets from a motion you
describe, so you can unit-test your own gesture logic:

```python
sim = triki.ImuSimulator(seed=1)
p = triki.Processor()
p.feed(sim.hold(1.5))                                 # calibration
p.on("flip", print)
p.feed(sim.rotate((1, 0, 0), 180, seconds=0.5))       # flip it over
p.feed(sim.hold(1.0))
```

## Project layout

```
src/triki/
  protocol.py     BLE UUIDs, commands, frame parser
  orientation.py  quaternion maths + Mahony filter
  processor.py    calibration, state, gestures (pure Python, no I/O)
  device.py       bleak connection, scan(), Triki
  background.py   threaded blocking wrapper, connect()
  simulator.py    ImuSimulator, SimulatedTriki
  contrib/winaudio.py   Windows volume / mic / output device helpers
```

## License

MIT
