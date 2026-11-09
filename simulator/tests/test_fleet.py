from agrifog_common import load_config
from simulator.fleet import Zone, build_fleet


def test_fleet_matches_config():
    zones, devices = build_fleet(load_config("fog-a"), "fog-a", period_s=10)
    assert len(devices) == 10
    assert set(zones) == {"z1", "z2", "z3"}
    assert len({d.device for d in devices}) == 10  # client_id MQTT uniques


def test_water_command_opens_valve_and_raises_moisture():
    _, devices = build_fleet(load_config("fog-a"), "fog-a", period_s=10)
    dev = devices[0]
    before = dev.zone.moisture
    dev.on_command({"action": "water", "seconds": 40})
    assert dev.telemetry()["flow_lpm"] > 0
    dev.zone.step(10)
    assert dev.zone.moisture > before


def test_soil_dries_without_water():
    z = Zone("z1", moisture=40)
    z.step(100, t=0)
    assert z.moisture < 40


def test_heartbeat_fields():
    _, devices = build_fleet(load_config("fog-a"), "fog-a", period_s=10)
    hb = devices[0].heartbeat()
    assert {"fw_version", "battery_v", "rssi", "uptime_s"} <= hb.keys()