"""Blocking / threaded front end for scripts and GUIs that don't use asyncio.

    import triki

    with triki.connect() as cap:          # or triki.connect(simulate=True)
        cap.on("click", lambda e: print("click!"))
        cap.wait_calibrated()
        while True:
            print(cap.state.roll)

Event handlers run on the background thread. In a GUI, hand work to the GUI
thread (e.g. tkinter ``root.after(0, ...)``) or just poll ``cap.state``.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

from .device import Controller, Triki
from .processor import Config, State


class BackgroundTriki:
    def __init__(self, address: Optional[str] = None, config: Optional[Config] = None,
                 simulate: bool = False):
        if simulate:
            from .simulator import SimulatedTriki
            self.controller: Controller = SimulatedTriki(config)
        else:
            self.controller = Triki(address, config)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop: Optional[asyncio.Event] = None
        self._ready = threading.Event()
        self._calibrated = threading.Event()
        self._error: Optional[BaseException] = None
        self.controller.on("calibrated", lambda e: self._calibrated.set())

    # --- lifecycle
    def start(self, timeout: float = 30.0) -> "BackgroundTriki":
        """Connect (scanning if no address was given) and block until streaming."""
        self._thread = threading.Thread(target=self._run, name="triki", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("timed out connecting to the Triki")
        if self._error:
            raise self._error
        return self

    def stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(5)

    def _run(self) -> None:
        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            try:
                await self.controller.connect()
            except BaseException as ex:  # report to the caller of start()
                self._error = ex
                self._ready.set()
                return
            self._ready.set()
            try:
                await self._stop.wait()
            finally:
                await self.controller.disconnect()
        asyncio.run(main())

    def __enter__(self) -> "BackgroundTriki":
        return self.start() if self._thread is None else self

    def __exit__(self, *exc) -> None:
        self.stop()

    # --- API
    @property
    def state(self) -> State:
        return self.controller.state

    @property
    def config(self) -> Config:
        return self.controller.config

    @property
    def is_connected(self) -> bool:
        return self.controller.is_connected

    def on(self, name, handler=None):
        return self.controller.on(name, handler)

    def off(self, name, handler) -> None:
        self.controller.off(name, handler)

    def calibrate(self) -> None:
        self._calibrated.clear()
        self.controller.calibrate()

    def zero(self) -> None:
        self.controller.zero()

    def wait_calibrated(self, timeout: Optional[float] = 15.0) -> bool:
        return self._calibrated.wait(timeout)


def connect(address: Optional[str] = None, config: Optional[Config] = None,
            simulate: bool = False, timeout: float = 30.0) -> BackgroundTriki:
    """Connect in a background thread and return once data is streaming."""
    return BackgroundTriki(address, config, simulate).start(timeout)
