"""Asyncio front end: connect to a real cap over BLE (via bleak)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional

from .processor import Config, Event, Processor, State
from .protocol import CMD_START, CMD_STOP, NUS_NOTIFY, NUS_WRITE

log = logging.getLogger("triki")


class TrikiError(Exception):
    pass


class TrikiNotFound(TrikiError):
    pass


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    address: str
    rssi: Optional[int]
    score: int  # higher = more likely a Triki

    @property
    def likely_triki(self) -> bool:
        return self.score >= 20


async def scan(timeout: float = 5.0) -> List[DeviceInfo]:
    """All BLE devices nearby, most Triki-like first (advertises the UART service / name)."""
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for dev, adv in found.values():
        name = adv.local_name or dev.name or ""
        uuids = " ".join(adv.service_uuids or []).lower()
        score = (20 if "6e400001" in uuids else 0) + (10 if "triki" in name.lower() else 0)
        out.append(DeviceInfo(name or "Unknown", dev.address, adv.rssi, score))
    out.sort(key=lambda d: (d.score, d.rssi if d.rssi is not None else -999), reverse=True)
    return out


async def find(timeout: float = 5.0) -> str:
    """Address of the most likely Triki nearby. Raises TrikiNotFound."""
    devices = [d for d in await scan(timeout) if d.score > 0]
    if not devices:
        raise TrikiNotFound("no Triki found - is it on and not connected to another app?")
    return devices[0].address


class Controller:
    """Shared API of :class:`Triki` and :class:`~triki.simulator.SimulatedTriki`."""

    def __init__(self, config: Optional[Config] = None):
        self.processor = Processor(config)

    # --- delegation
    @property
    def config(self) -> Config:
        return self.processor.config

    @property
    def state(self) -> State:
        """Latest state (safe to read at any time)."""
        return self.processor.state

    @property
    def calibrating(self) -> bool:
        return self.processor.calibrating

    def on(self, name, handler=None):
        return self.processor.on(name, handler)

    def off(self, name, handler) -> None:
        self.processor.off(name, handler)

    def calibrate(self) -> None:
        self.processor.start_calibration()

    def zero(self) -> None:
        self.processor.zero()

    # --- asyncio helpers
    async def wait_for(self, name: str, timeout: Optional[float] = None) -> Event:
        """Wait for the next event called ``name``."""
        fut = asyncio.get_running_loop().create_future()

        def handler(e: Event) -> None:
            if not fut.done():
                fut.set_result(e)
        self.on(name, handler)
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.off(name, handler)

    async def wait_calibrated(self, timeout: Optional[float] = 15.0) -> None:
        if self.calibrating:
            await self.wait_for("calibrated", timeout)

    async def events(self, *names: str, maxsize: int = 1000) -> AsyncIterator[Event]:
        """``async for e in cap.events("click", "flip"): ...`` (all events if no names)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize)

        def handler(e: Event) -> None:
            if not names or e.name in names:
                if queue.full():
                    queue.get_nowait()  # drop oldest rather than block the data path
                queue.put_nowait(e)
        self.on("*", handler)
        try:
            while True:
                yield await queue.get()
        finally:
            self.off("*", handler)

    # --- lifecycle (overridden)
    async def connect(self) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        raise NotImplementedError

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()


class Triki(Controller):
    """A real cap.

    >>> async with Triki() as cap:            # scans for the nearest Triki
    ...     await cap.wait_calibrated()       # keep it still for ~1 s
    ...     print(cap.state.roll, cap.state.pitch)
    """

    def __init__(self, address: Optional[str] = None, config: Optional[Config] = None,
                 start_command: bytes = CMD_START):
        super().__init__(config)
        self.address = address
        self.start_command = start_command
        self._client = None
        self.on_disconnect = None  # optional callable()

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def connect(self, timeout: float = 10.0) -> None:
        from bleak import BleakClient

        if self.address is None:
            self.address = await find()
        self._client = BleakClient(self.address, disconnected_callback=self._disconnected, timeout=timeout)
        await self._client.connect()
        self.processor.parser.reset()
        self.processor.start_calibration()
        await self._client.start_notify(NUS_NOTIFY, lambda _s, data: self.processor.feed(bytes(data)))
        await self.send(self.start_command)
        log.info("connected to %s", self.address)

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        if client and client.is_connected:
            try:
                await client.write_gatt_char(NUS_WRITE, CMD_STOP, response=True)
                await client.stop_notify(NUS_NOTIFY)
            finally:
                await client.disconnect()

    async def send(self, data: bytes) -> None:
        """Write a raw command (for experimenting with the protocol)."""
        if not self._client:
            raise TrikiError("not connected")
        await self._client.write_gatt_char(NUS_WRITE, data, response=True)

    def _disconnected(self, _client) -> None:
        log.info("disconnected")
        if self.on_disconnect:
            self.on_disconnect()
