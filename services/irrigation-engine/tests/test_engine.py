from agrifog_common import load_config

from app.engine import IrrigationEngine


def tel(ts: str, device: str, moisture: float, total: float = 0.0, temp: float = 25.0) -> dict:
    return {"ts": ts, "device": device, "soil_moisture": moisture, "air_temp": temp,
            "air_humidity": 50, "flow_lpm": 0.0, "water_l_total": total}


def make_engine():
    sent = []
    eng = IrrigationEngine(load_config("fog-a"), "fog-a", publish=lambda t, p: sent.append((t, p)))
    return eng, sent


def test_dry_zone_in_the_morning_gets_a_command():
    eng, sent = make_engine()
    for dev in ("esp-a-01", "esp-a-02", "esp-a-03"):
        eng.on_telemetry("z1", tel("2025-07-01T06:00:00Z", dev, 24.0))
    cmds = [p for t, p in sent if t == "fog-a/zone/z1/cmd"]
    assert len(cmds) == 1
    assert cmds[0]["action"] == "water" and cmds[0]["seconds"] > 0 and cmds[0]["id"]


def test_one_faulty_sensor_does_not_trigger_watering():
    eng, sent = make_engine()
    eng.on_telemetry("z1", tel("2025-07-01T06:00:00Z", "esp-a-01", 29.0))
    eng.on_telemetry("z1", tel("2025-07-01T06:00:00Z", "esp-a-02", 29.2))
    eng.on_telemetry("z1", tel("2025-07-01T06:00:00Z", "esp-a-03", 5.0))   # capteur faussé
    assert not [p for t, p in sent if t.endswith("/cmd")]


def test_missing_water_raises_an_alert():
    eng, sent = make_engine()
    eng.on_telemetry("z1", tel("2025-07-01T06:00:00Z", "esp-a-01", 21.0, total=500))
    cmd = next(p for t, p in sent if t.endswith("/cmd"))
    end = 6 * 60 + cmd["seconds"] // 60 + 40          # bien après la fin + 30 min
    ts = f"2025-07-01T{end // 60:02d}:{end % 60:02d}:00Z"
    eng.on_telemetry("z1", tel(ts, "esp-a-01", 21.0, total=500))   # le compteur n'a pas bougé
    alerts = [p for t, p in sent if t == "fog-a/alert"]
    assert alerts and alerts[0]["type"] == "flow"


def test_snapshot_exposes_reason():
    eng, _ = make_engine()
    eng.on_telemetry("z2", tel("2025-07-01T13:00:00Z", "esp-a-04", 26.0))
    snap = eng.snapshot()
    assert snap["z2"]["last_decision"]["action"] == "wait"
    assert "hors heures fraîches" in snap["z2"]["last_decision"]["reason"]
