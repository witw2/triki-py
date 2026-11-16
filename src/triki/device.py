"""Asyncio front end: connect to a real cap over BLE (via bleak)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional

from .processor import Config, Event, Processor, State
from .protocol import BATTERY_LEVEL, CMD_START, CMD_STOP, DEVICE_INFO, DEVICE_NAME, NUS_NOTIFY, NUS_WRITE

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
        self._restore = None
        self.battery: Optional[int] = None     # percent, None until known
        self.device_info: dict = {}            # name, manufacturer, model, firmware, ...

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

    def _set_battery(self, level: Optional[int]) -> None:
        if level is None or level == self.battery:
            return
        self.battery = level
        st = self.processor.state
        self.processor.emit(Event("battery", st.t, st, level))

    def calibration_snapshot(self):
        """Plain dict describing the current calibration (save it to skip calibrating next time)."""
        return self.processor.calibration_snapshot()

    def restore_calibration(self, snapshot) -> None:
        """Reuse a saved calibration on the next connect instead of measuring a new one."""
        self._restore = snapshot

    @property
    def seconds_since_data(self) -> float:
        """Wall-clock seconds since the cap last sent anything."""
        return self.processor.seconds_since_data

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
        self._battery_task = None
        self.on_disconnect = None  # optional callable()
        self.battery_poll_s = 60.0

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
        self.processor.start_calibration(self._restore)
        await self._client.start_notify(NUS_NOTIFY, lambda _s, data: self.processor.feed(bytes(data)))
        await self.send(self.start_command)
        log.info("connected to %s", self.address)
        await self._read_device_info()
        await self._start_battery()

    async def _read_device_info(self) -> None:
        info = {}
        for key, uuid in (("name", DEVICE_NAME), *DEVICE_INFO.items()):
            try:
                raw = await self._client.read_gatt_char(uuid)
                info[key] = bytes(raw).decode("utf-8", "replace").strip("\x00 ").strip()
            except Exception:
                pass  # not every cap / firmware has every field
        self.device_info = info

    async def read_battery(self) -> Optional[int]:
        """Battery level in percent (standard Bluetooth battery service), or None."""
        try:
            raw = await self._client.read_gatt_char(BATTERY_LEVEL)
        except Exception:
            return None
        level = raw[0] if raw else None
        self._set_battery(level)
        return level

    async def _start_battery(self) -> None:
        if await self.read_battery() is None:
            return
        try:   # push updates if the cap supports it...
            await self._client.start_notify(BATTERY_LEVEL, lambda _s, d: self._set_battery(d[0] if d else None))
        except Exception:
            pass
        self._battery_task = asyncio.ensure_future(self._battery_loop())   # ...and poll as a fallback

    async def _battery_loop(self) -> None:
        while self.is_connected:
            await asyncio.sleep(self.battery_poll_s)
            await self.read_battery()

    async def disconnect(self) -> None:
        if self._battery_task:
            self._battery_task.cancel()
            self._battery_task = None
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
