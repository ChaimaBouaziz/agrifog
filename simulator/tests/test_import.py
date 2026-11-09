def test_simulator_imports():
    import simulator.main as m

    assert m.now().endswith("Z")
