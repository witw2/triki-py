"""triki - Python library for the Triki bottle-cap motion controller.

Quick start (blocking)::

    import triki
    with triki.connect() as cap:
        cap.on("click", lambda e: print("click"))
        cap.on("flip", lambda e: print("now face", e.value))
        cap.wait_calibrated()
        input("press Enter to quit\\n")

Quick start (asyncio)::

    async with triki.Triki() as cap:
        async for e in cap.events("twist"):
            print(e.value)
"""

from .background import BackgroundTriki, connect
from .device import Controller, DeviceInfo, Triki, TrikiError, TrikiNotFound, find, scan
from .processor import EVENTS, Config, Event, Knob, Processor, State
from .protocol import CMD_START, CMD_STOP, FrameParser, RawFrame, encode_frame, start_command
from .simulator import ImuSimulator, SimulatedTriki

__version__ = "0.5.0"

__all__ = [
    "BackgroundTriki", "connect",
    "Controller", "DeviceInfo", "Triki", "TrikiError", "TrikiNotFound", "find", "scan",
    "EVENTS", "Config", "Event", "Knob", "Processor", "State",
    "CMD_START", "CMD_STOP", "FrameParser", "RawFrame", "encode_frame", "start_command",
    "ImuSimulator", "SimulatedTriki",
]
