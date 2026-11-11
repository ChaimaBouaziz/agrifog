from agrifog_common import load_config

from app.engine import IrrigationEngine

DEVICES_Z1 = ("esp-a-01", "esp-a-02", "esp-a-03")


def tel(hhmm: str, device: str, moisture: float, total: float = 0.0, flow: float = 0.0,
        temp: float = 25.0, day: int = 1) -> dict:
    return {"ts": f"2025-07-{day:02d}T{hhmm}:00Z", "device": device, "soil_moisture": moisture,
            "air_temp": temp, "air_humidity": 50, "flow_lpm": flow, "water_l_total": total}


def make_engine():
    sent = []
    eng = IrrigationEngine(load_config("fog-a"), "fog-a", publish=lambda t, p: sent.append((t, p)))
    return eng, sent


def cmds(sent):
    return [p for t, p in sent if t.endswith("/cmd")]


def alerts(sent, kind=None):
    return [p for t, p in sent if t == "fog-a/alert" and not p["resolved"] and (kind is None or p["type"] == kind)]


def water_z1(eng, sent, moisture=21.0, total=500.0):
    """Dry zone at 6h -> command -> the valve device acknowledges it."""
    for dev in DEVICES_Z1:   # mesures de la nuit (hors fenêtre : pas d'ordre)
        eng.on_telemetry("z1", tel("04:00", dev, moisture, total))
    for dev in DEVICES_Z1:
        eng.on_telemetry("z1", tel("06:00", dev, moisture, total))
    cmd = cmds(sent)[-1]
    end_min = 6 * 60 + 5 + cmd["seconds"] // 60
    eng.on_valve("z1", {"id": cmd["id"], "device": "esp-a-01", "opened": "2025-07-01T06:05:00Z",
                        "until": f"2025-07-01T{end_min // 60:02d}:{end_min % 60:02d}:00Z",
                        "water_l_total": total})
    return cmd, end_min


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def test_dry_zone_in_the_morning_gets_one_command():
    eng, sent = make_engine()
    for dev in DEVICES_Z1:
        eng.on_telemetry("z1", tel("06:00", dev, 24.0))
    assert len(cmds(sent)) == 1
    assert cmds(sent)[0]["action"] == "water" and cmds(sent)[0]["seconds"] > 0 and cmds(sent)[0]["id"]


def test_one_faulty_sensor_does_not_trigger_watering():
    eng, sent = make_engine()
    eng.on_telemetry("z1", tel("06:00", "esp-a-01", 29.0))
    eng.on_telemetry("z1", tel("06:00", "esp-a-02", 29.2))
    eng.on_telemetry("z1", tel("06:00", "esp-a-03", 5.0))   # capteur faussé
    assert not cmds(sent)


def test_light_rain_does_not_stop_watering():
    eng, sent = make_engine()
    eng.on_forecast({"ts": "2025-07-01T05:00:00Z", "rain_next_24h_mm": 4})
    for dev in DEVICES_Z1:
        eng.on_telemetry("z1", tel("06:00", dev, 22.0))
    assert cmds(sent)


def test_rain_forecast_postpones_watering():
    eng, sent = make_engine()
    eng.on_forecast({"ts": "2025-07-01T05:00:00Z", "rain_next_24h_mm": 40})
    for dev in DEVICES_Z1:
        eng.on_telemetry("z1", tel("06:00", dev, 22.0))
    assert not cmds(sent)
    assert "pluie" in eng.snapshot()["zones"]["z1"]["last_decision"]["reason"]


def test_old_forecast_is_ignored_offline_first():
    eng, sent = make_engine()
    eng.on_forecast({"ts": "2025-06-28T05:00:00Z", "rain_next_24h_mm": 25})  # 3 jours : périmée
    for dev in DEVICES_Z1:
        eng.on_telemetry("z1", tel("06:00", dev, 22.0))
    assert cmds(sent)


def test_leak_is_detected_when_valve_is_closed():
    eng, sent = make_engine()
    eng.on_telemetry("z2", tel("13:00", "esp-a-04", 27.0, flow=6.0))
    assert alerts(sent, "leak")
    eng.on_telemetry("z2", tel("13:30", "esp-a-04", 27.0, flow=0.0))
    assert [p for t, p in sent if t == "fog-a/alert" and p["resolved"] and p["type"] == "leak"]


def test_flow_during_watering_is_not_a_leak():
    eng, sent = make_engine()
    cmd, end = water_z1(eng, sent)
    eng.on_telemetry("z1", tel("06:30", "esp-a-02", 21.5, total=800, flow=20.0))
    assert not alerts(sent, "leak")


def test_pump_failure_while_valve_open():
    eng, sent = make_engine()
    water_z1(eng, sent)
    eng.on_telemetry("z1", tel("06:30", "esp-a-02", 21.0, total=500, flow=0.0))
    assert alerts(sent, "pump")


def test_missing_water_after_watering_raises_one_alert():
    eng, sent = make_engine()
    cmd, end = water_z1(eng, sent, total=500)
    eng.on_telemetry("z1", tel(hhmm(end + 40), "esp-a-01", 21.0, total=500))   # compteur immobile
    assert len(alerts(sent, "pump")) == 1


def test_stuck_sensor_is_spotted_after_watering():
    eng, sent = make_engine()
    cmd, end = water_z1(eng, sent, moisture=21.0, total=500)
    liters = cmd["seconds"] / 60 * 20
    t = end + 40
    eng.on_telemetry("z1", tel(hhmm(t), "esp-a-01", 23.0, total=500 + liters))        # débit OK
    t = end + 130                                                                      # +2 h : contrôle
    eng.on_telemetry("z1", tel(hhmm(t), "esp-a-02", 23.0, total=500 + liters))
    eng.on_telemetry("z1", tel(hhmm(t), "esp-a-03", 21.0, total=500 + liters))        # figé
    eng.on_telemetry("z1", tel(hhmm(t + 1), "esp-a-01", 23.0, total=500 + liters))
    sensor = alerts(sent, "sensor")
    assert len(sensor) == 1 and sensor[0]["device"] == "esp-a-03"


def test_silent_device_is_reported_offline():
    eng, sent = make_engine()
    for dev in eng.devices:
        eng.on_heartbeat(dev, {"ts": "2025-07-01T10:00:00Z"})
    for dev in eng.devices:
        if dev != "esp-a-09":
            eng.on_heartbeat(dev, {"ts": "2025-07-01T14:00:00Z"})
    eng.on_telemetry("z3", tel("14:00", "esp-a-07", 27.0))
    off = alerts(sent, "offline")
    assert [a["device"] for a in off] == ["esp-a-09"]


def test_snapshot_exposes_reason_and_alerts():
    eng, _ = make_engine()
    eng.on_telemetry("z2", tel("13:00", "esp-a-04", 26.0, flow=4.0))
    snap = eng.snapshot()
    assert "hors heures fraîches" in snap["zones"]["z2"]["last_decision"]["reason"]
    assert snap["alerts"][0]["type"] == "leak"


def test_quota_limits_watering_and_counts_usage():
    eng, sent = make_engine()
    eng.on_budget({"day": "2025-07-01", "allocated_l": 1000})
    for dev in DEVICES_Z1:
        eng.on_telemetry("z1", tel("06:00", dev, 21.0))
    cmd = cmds(sent)[-1]
    assert cmd["seconds"] == 1000 / 20 * 60                       # 1000 L à 20 L/min
    usage = [p for t, p in sent if t == "fog-a/quota/usage"]
    assert usage[-1] == {"day": "2025-07-01", "used_l": 1000}
    for dev in ("esp-a-04", "esp-a-05", "esp-a-06"):
        eng.on_telemetry("z2", tel("06:10", dev, 21.0))
    assert len(cmds(sent)) == 1                                    # plus de quota pour z2
    assert "quota du jour épuisé" in eng.snapshot()["zones"]["z2"]["last_decision"]["reason"]


def test_no_budget_means_no_limit():
    eng, sent = make_engine()
    eng.on_budget({"day": "2025-06-30", "allocated_l": 10})        # budget d'hier : ignoré
    for dev in DEVICES_Z1:
        eng.on_telemetry("z1", tel("06:00", dev, 21.0))
    assert cmds(sent)[-1]["seconds"] == 7200
