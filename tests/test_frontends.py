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


def test_stop_cancels_a_connect_in_progress():
    import asyncio
    cap = triki.BackgroundTriki(simulate=True)

    async def slow_connect():
        await asyncio.sleep(30)          # e.g. a sleeping cap that never answers
    cap.controller.connect = slow_connect
    import threading
    errors = []
    t = threading.Thread(target=lambda: errors.append(_try_start(cap)))
    t.start()
    time.sleep(0.3)
    started = time.time()
    cap.stop()
    t.join(5)
    assert not t.is_alive() and time.time() - started < 3
    assert isinstance(errors[0], triki.TrikiError)


def _try_start(cap):
    try:
        cap.start(timeout=20)
    except Exception as ex:
        return ex
    return None


def test_restored_calibration_skips_waiting():
    first = triki.BackgroundTriki(simulate=True)
    with first:
        assert first.wait_calibrated(5)
        snap = first.calibration_snapshot()
    assert snap["gyro_axes"] == "ABC"
    second = triki.BackgroundTriki(simulate=True)
    second.restore_calibration(snap)
    infos = []
    second.on("calibrated", lambda e: infos.append(e.value))
    with second:
        assert second.wait_calibrated(1)
        assert second.seconds_since_data < 0.5
    assert infos[0]["restored"] is True
