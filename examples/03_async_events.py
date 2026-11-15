"""asyncio API: scan, connect, and consume events as an async stream.

    python examples/03_async_events.py [--sim]
"""

import asyncio
import sys

import triki


async def main():
    if "--sim" in sys.argv:
        cap = triki.SimulatedTriki()
    else:
        for d in await triki.scan():
            print(("* " if d.likely_triki else "  ") + f"{d.address}  {d.name}")
        cap = triki.Triki()  # nearest Triki

    async with cap:
        await cap.wait_calibrated()
        print("calibrated:", cap.state)
        async for e in cap.events("click", "long_press", "flip"):
            print(f"{e.t:7.2f}s  {e.name}  {e.value if e.value is not None else ''}")
            if e.name == "long_press":
                print("long press -> bye")
                break


asyncio.run(main())
