from datetime import datetime

import pytest

from agrifog_common import load_config
from simulator.clock import SimClock
from simulator.faults import ForecastOverride, apply_fault
from simulator.fleet import build_fleet

DAY = {"date": "2025-07-01", "t_max": 34.0, "t_min": 22.0, "t_mean": 28.0,
       "rain_mm": 0.0, "rh_mean": 55.0, "et0_fao": 6.0}
RAINY = {**DAY, "date": "2025-07-02", "rain_mm": 24.0}


def setup(weather=None):
    t = [0.0]
    clock = SimClock(datetime(2025, 7, 1, 6), 60, real_time=lambda: t[0])  # 1 s = 1 min
    zones, devs = build_fleet(load_config("fog-a"), "fog-a", 10, clock, weather or [DAY])
    return t, clock, zones, {d.device: d for d in devs}


def test_leak_counts_water_and_shows_flow_without_pump():
    t, clock, zones, devs = setup()
    z = zones["z2"]
    z.step()
    apply_fault({"type": "leak", "zone": "z2", "lpm": 6}, zones, devs, ForecastOverride(), clock.now())
    assert devs["esp-a-04"].telemetry()["flow_lpm"] == 6
    t[0] = 10
    z.step()
    assert z.water_l_total == pytest.approx(60)      # 10 min à 6 L/min, sans arrosage


def test_pump_failure_opens_valve_but_nothing_flows():
    t, clock, zones, devs = setup()
    z, valve = zones["z1"], devs["esp-a-01"]
    z.step()
    apply_fault({"type": "pump_fail", "zone": "z1"}, zones, devs, ForecastOverride(), clock.now())
    ack = valve.on_command({"id": "c1", "action": "water", "seconds": 600})
    assert ack["state"] == "open" and ack["until"] > ack["opened"]
    t[0] = 5
    z.step()
    assert z.water_l_total == 0 and valve.telemetry()["flow_lpm"] == 0


def test_stuck_and_biased_sensors():
    _, clock, zones, devs = setup()
    f = ForecastOverride()
    apply_fault({"type": "sensor_stuck", "device": "esp-a-02", "value": 35}, zones, devs, f, clock.now())
    apply_fault({"type": "sensor_bias", "device": "esp-a-03", "soil": -8, "air": 6}, zones, devs, f, clock.now())
    assert devs["esp-a-02"].telemetry()["soil_moisture"] == 35
    assert devs["esp-a-03"].telemetry()["air_temp"] > devs["esp-a-01"].telemetry()["air_temp"] + 4
    apply_fault({"type": "clear"}, zones, devs, f, clock.now())
    assert devs["esp-a-02"].stuck_value is None and devs["esp-a-03"].air_offset == 0


def test_offline_and_unknown_device():
    _, clock, zones, devs = setup()
    apply_fault({"type": "offline", "device": "esp-a-09"}, zones, devs, ForecastOverride(), clock.now())
    assert devs["esp-a-09"].offline
    with pytest.raises(ValueError):
        apply_fault({"type": "offline", "device": "esp-zz"}, zones, devs, ForecastOverride(), clock.now())


def test_forecast_sees_tomorrow_rain_and_can_be_forced():
    _, clock, zones, devs = setup([DAY, RAINY])
    assert zones["z1"].rain_next_hours(24) == pytest.approx(6.0)   # de 6h à 6h : 0h-6h du jour pluvieux (1 mm/h)
    f = ForecastOverride()
    apply_fault({"type": "rain_forecast", "mm": 20, "hours": 6}, zones, devs, f, clock.now())
    assert f.active(clock.now()) and f.mm == 20


def test_fallback_brokers_from_config():
    _, _, _, devs = setup()
    assert [h for h, _ in devs["esp-a-01"].brokers] == ["127.0.0.1", "fog-b"]
