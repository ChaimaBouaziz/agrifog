from datetime import datetime

import pytest

from agrifog_common import load_config
from simulator.clock import SimClock
from simulator.fleet import build_fleet
from simulator.season import run_fixed_schedule
from simulator.soil import bucket_from_config

DAY = {"date": "2025-07-01", "t_max": 34.0, "t_min": 22.0, "t_mean": 28.0,
       "rain_mm": 0.0, "rh_mean": 55.0, "et0_fao": 6.0}
WEATHER = [DAY, {**DAY, "date": "2025-07-02", "rain_mm": 10.0}]


class FakeTime:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_fleet(speed=3600.0):
    fake = FakeTime()
    clock = SimClock(datetime(2025, 7, 1, 6), speed, real_time=fake)  # 1 s réelle = 1 h
    zones, devices = build_fleet(load_config("fog-a"), "fog-a", 10, clock, WEATHER)
    return fake, clock, zones, devices


def test_fleet_matches_config():
    _, _, zones, devices = make_fleet()
    assert len(devices) == 10
    assert set(zones) == {"z1", "z2", "z3"}
    assert len({d.device for d in devices}) == 10  # client_id MQTT uniques


def test_soil_dries_during_the_day():
    fake, _, zones, _ = make_fleet()
    z = zones["z1"]
    z.step()
    before = z.moisture
    fake.t = 8  # 8 h simulées (6h -> 14h)
    z.step()
    assert z.moisture < before


def test_water_command_counts_liters_and_wets_soil():
    fake, _, zones, devices = make_fleet(speed=60)  # 1 s réelle = 1 min simulée
    dev, z = devices[0], zones["z1"]
    z.soil.theta = 20
    z.step()
    dev.on_command({"action": "water", "seconds": 600})   # 10 min simulées
    assert dev.telemetry()["flow_lpm"] == 20
    fake.t = 15                                           # 15 min plus tard
    z.step()
    assert z.water_l_total == pytest.approx(200)           # 20 L/min x 10 min
    assert z.moisture > 20                                 # 200 L / 200 m² = 1 mm
    assert dev.telemetry()["flow_lpm"] == 0


def test_only_one_valve_per_zone():
    _, _, _, devices = make_fleet()
    valves = [d.device for d in devices if d.has_valve]
    assert valves == ["esp-a-01", "esp-a-04", "esp-a-07"]


def test_other_devices_ignore_water_commands():
    _, _, zones, devices = make_fleet(speed=60)
    devices[1].on_command({"action": "water", "seconds": 600})  # capteur sans vanne
    assert not zones["z1"].is_watering()


def test_redelivered_command_is_executed_once():
    fake, _, zones, devices = make_fleet(speed=60)
    z, valve = zones["z1"], devices[0]
    z.step()
    cmd = {"id": "c-42", "action": "water", "seconds": 600}
    valve.on_command(cmd)
    fake.t = 30                     # l'arrosage est fini…
    z.step()
    valve.on_command(cmd)           # …et le broker redélivre la même commande
    fake.t = 60
    z.step()
    assert z.water_l_total == pytest.approx(200)


def test_telemetry_uses_simulated_time_and_real_weather():
    fake, _, _, devices = make_fleet()
    fake.t = 9  # 15h simulées : le plus chaud
    tel = devices[0].telemetry()
    assert tel["ts"].startswith("2025-07-01T15")
    assert tel["air_temp"] == pytest.approx(34, abs=1)


def test_heartbeat_fields():
    _, _, _, devices = make_fleet()
    hb = devices[0].heartbeat()
    assert {"fw_version", "battery_v", "rssi", "uptime_s"} <= hb.keys()


def test_fixed_schedule_season():
    cfg = load_config("fog-a")
    hot_month = [DAY] * 20  # 20 jours chauds sans pluie
    dry = run_fixed_schedule(bucket_from_config(cfg), hot_month, 0, 7, 200)
    wet = run_fixed_schedule(bucket_from_config(cfg), hot_month, 6, 7, 200)
    assert wet.irrig_mm == pytest.approx(6 * 20)
    assert wet.stress_h < dry.stress_h


def test_real_zones_are_not_simulated():
    fake = FakeTime()
    clock = SimClock(datetime(2025, 7, 1, 6), 3600, real_time=fake)
    zones, devices = build_fleet(load_config("fog-b"), "fog-b", 10, clock, WEATHER)
    assert "zw" not in zones and all(d.device != "esp-b-wokwi" for d in devices)
