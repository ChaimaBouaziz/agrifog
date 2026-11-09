def test_simulator_imports():
    import simulator.main  # noqa: F401
    from simulator.fleet import now_iso

    assert now_iso().endswith("Z")