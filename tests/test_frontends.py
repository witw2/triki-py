import asyncio
import time

import triki


def test_simulated_triki_async():
    async def main():
        async with triki.SimulatedTriki(seed=1) as cap:
            await cap.wait_calibrated(timeout=5)
            await asyncio.sleep(0.3)
            assert cap.is_connected
            assert cap.state.frame > 30
            assert not cap.state.calibrating
    asyncio.run(main())


def test_background_connect_simulated():
    clicks = []
    cap = triki.BackgroundTriki(simulate=True)
    cap.controller.button = lambda t: 0.1 < t < 0.2   # one click right after calibration
    cap.on("click", clicks.append)
    with cap:
        assert cap.wait_calibrated(5)
        time.sleep(0.6)
        assert cap.state.frame > 50
    assert len(clicks) == 1
    assert not cap.is_connected


def test_cli_help(capsys):
    import pytest
    from triki.__main__ import main
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "monitor" in capsys.readouterr().out
