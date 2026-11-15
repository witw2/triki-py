"""Wire protocol of the Triki bottle-cap controller.

What is known for sure (observed in working code):

* The cap talks over the Nordic UART Service (NUS): commands are written to
  ``NUS_WRITE`` and data arrives as notifications on ``NUS_NOTIFY``.
* IMU frames are 14 bytes: ``0x22``, a button byte (1 = pressed), then six
  little-endian ``int16`` values (axes A..F).
* Frames can be split across BLE notifications.

What is inferred (see docs/PROTOCOL.md):

* The start command reads as ``0x20 | accel range g | gyro range dps |
  rate Hz | enable`` (u16 little-endian fields), i.e. 16 g / 2000 dps / 104 Hz.
* ``0x21`` frames are 4 bytes long (probably a status / command reply).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_WRITE = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

FRAME_IMU = 0x22
FRAME_STATUS = 0x21
IMU_FRAME_SIZE = 14
STATUS_FRAME_SIZE = 4

CMD_STOP = bytes([0x20, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])


def start_command(accel_range_g: int = 16, gyro_range_dps: int = 2000, rate_hz: int = 104) -> bytes:
    """Build the streaming start command.

    The defaults reproduce the one command known to work. Other values follow
    the inferred field layout and are untested on hardware.
    """
    return bytes([0x20]) + struct.pack("<HHH", accel_range_g, gyro_range_dps, rate_hz) + bytes([0x01])


CMD_START = start_command()


@dataclass(frozen=True)
class RawFrame:
    """One IMU sample exactly as sent by the cap."""

    button: bool
    axes: Tuple[int, int, int, int, int, int]  # A..F, raw int16


def encode_frame(button: bool, axes) -> bytes:
    """Inverse of parsing - handy for simulators and tests."""
    clipped = [max(-32768, min(32767, int(v))) for v in axes]
    return struct.pack("<BB6h", FRAME_IMU, 1 if button else 0, *clipped)


class FrameParser:
    """Reassembles frames from arbitrarily fragmented notification payloads."""

    def __init__(self, on_status: Optional[Callable[[bytes], None]] = None):
        self._buf = bytearray()
        self._on_status = on_status
        self.skipped_bytes = 0  # bytes dropped while re-synchronising

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, data: bytes) -> List[RawFrame]:
        self._buf += data
        out: List[RawFrame] = []
        buf = self._buf
        while buf:
            head = buf[0]
            if head == FRAME_IMU:
                if len(buf) < IMU_FRAME_SIZE:
                    break
                _, btn, *axes = struct.unpack_from("<BB6h", buf)
                out.append(RawFrame(btn == 1, tuple(axes)))
                del buf[:IMU_FRAME_SIZE]
            elif head == FRAME_STATUS:
                if len(buf) < STATUS_FRAME_SIZE:
                    break
                if self._on_status:
                    self._on_status(bytes(buf[:STATUS_FRAME_SIZE]))
                del buf[:STATUS_FRAME_SIZE]
            else:
                del buf[:1]
                self.skipped_bytes += 1
        return out
