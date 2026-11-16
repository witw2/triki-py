# Triki BLE protocol notes

Everything here comes from a working third-party air-mouse script plus
inference. Each item is marked **confirmed** (seen working) or **guess**.
Corrections from real captures are very welcome — `examples/05_record_csv.py`
and the `status` event exist for exactly that.

## Transport — confirmed

Nordic UART Service (NUS):

| role | UUID |
|---|---|
| service | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` |
| write (commands) | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` |
| notify (data) | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` |

Notifications are a byte stream: a frame can be split across notifications and
one notification can carry several frames.

## Standard services — confirmed

Besides the UART service the cap exposes standard Bluetooth services (seen in
software from the cap's maker):

| service | characteristic | content |
|---|---|---|
| Battery `0x180F` | Battery Level `0x2A19` | 1 byte, 0–100 % |
| Generic Access `0x1800` | Device Name `0x2A00` | text |
| Device Information `0x180A` | `0x2A29` manufacturer, `0x2A24` model, `0x2A25` serial, `0x2A26` firmware, `0x2A27` hardware, `0x2A28` software, `0x2A23` system ID, `0x2A50` PnP ID | text / binary |

`cap.battery`, the `battery` event and `cap.device_info` read these;
`triki info` prints them.

## Commands

| command | bytes | status |
|---|---|---|
| start streaming | `20 10 00 D0 07 68 00 01` | confirmed |
| stop streaming | `20 00 00 00 00 00 00` | confirmed |

**Guess:** the start command is `0x20` followed by three little-endian u16
fields and an enable byte:

| field | value | likely meaning |
|---|---|---|
| `10 00` | 16 | accelerometer range ±16 g |
| `D0 07` | 2000 | gyroscope range ±2000 °/s |
| `68 00` | 104 | output data rate 104 Hz |
| `01` | 1 | enable |

These are exactly the standard settings of ST's LSM6 family of IMUs, so the
cap probably contains one. `triki.start_command(accel_range_g, gyro_range_dps,
rate_hz)` builds variants of this command — **untested** on hardware.

## Frames

### `0x22` IMU sample — confirmed layout

```
offset  size  field
0       1     0x22
1       1     button: 1 = pressed, 0 = released
2       12    six int16 little-endian: A B C D E F
```

**Guess:** A, B, C = gyroscope X/Y/Z and D, E, F = accelerometer X/Y/Z (the
LSM6 register order). The library does not rely on this: during calibration it
picks the triple whose mean magnitude is ~1 g as the accelerometer. Force it
with `Config(layout="gyro_first" | "accel_first")`.

Scale (if the guess about ranges holds):

* accelerometer ±16 g → 2048 LSB per g (measured during calibration anyway)
* gyroscope ±2000 °/s → 70 m°/s per LSB (`Config.gyro_scale = 0.070`)

### `0x21` — unknown

The reference script reads 2 more bytes after the 2-byte header and then
discards the rest of that notification. The library treats it as a 4-byte frame
and emits its bytes as a `status` event. Could be a command acknowledgement
(`0x20` command → `0x21` reply), battery level, or something else.

## Open questions

* What do `0x21` frames contain?
* Are other sample rates / ranges accepted by the start command?
* Is the sensor's Z axis the cap's face normal? (`cap.zero()` compensates either way.)
* Exact sample rate on the wire (check `state.frame` against wall time).
