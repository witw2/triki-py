"""Command line: ``triki scan``, ``triki monitor [--sim] [--address ADDR]``, ``triki events``."""

from __future__ import annotations

import argparse
import asyncio
import sys

from . import SimulatedTriki, Triki, scan


async def _scan(args) -> None:
    devices = await scan(args.timeout)
    if not devices:
        print("no BLE devices found")
    for d in devices:
        mark = "*" if d.likely_triki else " "
        print(f"{mark} {d.address}  {d.rssi if d.rssi is not None else '?':>4} dBm  {d.name}")
    print("\n* = advertises the Triki UART service")


def _controller(args):
    return SimulatedTriki() if args.sim else Triki(args.address)


async def _monitor(args) -> None:
    async with _controller(args) as cap:
        print("calibrating - keep the cap still...", flush=True)
        await cap.wait_calibrated()
        print("ok (Ctrl+C to quit)")
        while True:
            s = cap.state
            print(f"\rroll {s.roll:+7.1f}  pitch {s.pitch:+7.1f}  yaw {s.yaw:+7.1f}  "
                  f"face {s.face or '-':4}  twist {s.twist_dps:+6.0f} dps  "
                  f"btn {'X' if s.button else '.'}", end="", flush=True)
            await asyncio.sleep(0.05)


async def _events(args) -> None:
    async with _controller(args) as cap:
        print("calibrating - keep the cap still...", flush=True)
        async for e in cap.events():
            if e.name in ("frame", "twist"):
                continue
            print(f"{e.t:8.2f}s  {e.name:13} {'' if e.value is None else e.value}", flush=True)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="triki", description="Triki bottle-cap controller tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="list nearby BLE devices")
    s.add_argument("--timeout", type=float, default=5.0)
    for name, help_ in (("monitor", "live orientation readout"), ("events", "print gestures as they happen")):
        m = sub.add_parser(name, help=help_)
        m.add_argument("--address", help="BLE address (default: nearest Triki)")
        m.add_argument("--sim", action="store_true", help="use the built-in simulator")
    args = p.parse_args(argv)
    try:
        asyncio.run({"scan": _scan, "monitor": _monitor, "events": _events}[args.cmd](args))
    except KeyboardInterrupt:
        print()
    except Exception as ex:
        sys.exit(f"error: {ex}")


if __name__ == "__main__":
    main()
