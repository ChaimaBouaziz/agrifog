from datetime import datetime

from simulator.clock import SimClock


def test_simulator_imports():
    import simulator.main  # noqa: F401
    import simulator.season  # noqa: F401


def test_clock_is_accelerated():
    t = [100.0]
    clock = SimClock(datetime(2025, 4, 1), speed=360, real_time=lambda: t[0])
    t[0] += 240  # 4 minutes réelles
    assert clock.now().day == 2  # = 1 jour simulé
    assert clock.iso().startswith("2025-04-02T00:00")
