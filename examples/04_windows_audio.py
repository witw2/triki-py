"""Windows audio remote (needs: pip install triki[windows]).

* twist clockwise / counter-clockwise -> volume up / down (with the Windows overlay)
* click                               -> toggle microphone mute
* flip face up / face down            -> switch default output device

    python examples/04_windows_audio.py                       # list output devices
    python examples/04_windows_audio.py "Speakers" "Headset"  # face-up / face-down device
                                                              # (any part of the name)
"""

import asyncio
import sys

import triki
from triki.contrib.winaudio import AudioController


def pick(devices, needle):
    for dev_id, name in devices:
        if needle.lower() in name.lower():
            return dev_id, name
    sys.exit(f"no output device matching {needle!r}")


async def main():
    audio = AudioController()  # created on the asyncio thread, where handlers run
    devices = audio.list_outputs()
    if len(sys.argv) < 3:
        print("Output devices:")
        for _, name in devices:
            print("  ", name)
        print('\nusage: 04_windows_audio.py "<face-up device>" "<face-down device>"')
        return
    outputs = {"up": pick(devices, sys.argv[1]), "down": pick(devices, sys.argv[2])}

    cap = triki.Triki()
    knob = triki.Knob(degrees_per_step=7.2)  # 2 % per notch -> 360 deg for 0..100 %

    @cap.on("twist")
    def volume(e):
        steps = knob.update(e.value)
        if steps:
            audio.press_volume_key(steps > 0, abs(steps))

    @cap.on("click")
    def mic(e):
        muted = audio.toggle_mic()
        print("mic", "MUTED" if muted else "ON")

    @cap.on("flip")
    def output(e):
        dev_id, name = outputs[e.value]
        audio.set_default_output(dev_id)
        print("output ->", name)

    async with cap:
        print("Calibrating - keep the cap still...")
        await cap.wait_calibrated()
        print("Ready. Ctrl+C to quit.")
        await asyncio.Event().wait()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
