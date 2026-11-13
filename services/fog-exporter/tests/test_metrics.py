from agrifog_common.config import load_config

from app.metrics import FogMetrics


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(fog="fog-b"):
    clock = Clock()
    return FogMetrics(load_config(fog), fog, clock=clock), clock


def val(m, name, **labels):
    return m.registry.get_sample_value(name, labels)


def test_static_metrics_from_config():
    m, _ = make("fog-b")
    assert val(m, "agrifog_moisture_threshold_pct", fog="fog-b", zone="z1") == 11
    assert val(m, "agrifog_field_capacity_pct", fog="fog-b", zone="zw") == 18
    assert val(m, "agrifog_quota_daily_liters", fog="fog-b") == 6000


def test_telemetry_and_heartbeat():
    m, clock = make()
    m.handle("fog-b/zone/zw/telemetry", {"ts": "x", "device": "esp-b-wokwi", "soil_moisture": 5.0,
                                          "air_temp": 24, "air_humidity": 40, "flow_lpm": 20, "water_l_total": 201})
    lbl = dict(fog="fog-b", zone="zw", device="esp-b-wokwi")
    assert val(m, "agrifog_soil_moisture_pct", **lbl) == 5.0
    assert val(m, "agrifog_flow_lpm", **lbl) == 20
    assert val(m, "agrifog_water_total_liters", **lbl) == 201
    clock.t = 1010
    m.handle("fog-b/device/esp-b-wokwi/heartbeat", {"ts": "x", "fw_version": "0.2.0", "battery_v": 4.09,
                                                    "rssi": -73, "uptime_s": 752})
    assert val(m, "agrifog_device_battery_volts", fog="fog-b", device="esp-b-wokwi") == 4.09
    assert val(m, "agrifog_device_firmware_info", fog="fog-b", device="esp-b-wokwi", version="0.2.0") == 1
    assert val(m, "agrifog_device_last_seen_timestamp_seconds", fog="fog-b", device="esp-b-wokwi") == 1010


def test_decision_command_valve():
    m, _ = make()
    m.handle("fog-b/zone/zw/decision", {"action": "water", "kind": "urgent", "moisture": 5.0, "et0": 6.1,
                                         "ai_moisture_6h": 4.9, "reason": "URGENCE"})
    m.handle("fog-b/zone/zw/cmd", {"id": "c1", "action": "water", "seconds": 3600})
    m.handle("fog-b/zone/zw/valve", {"id": "c1", "device": "esp-b-wokwi", "state": "open"})
    z = dict(fog="fog-b", zone="zw")
    assert val(m, "agrifog_decisions_total", action="water", kind="urgent", **z) == 1
    assert val(m, "agrifog_ai_moisture_6h_pct", **z) == 4.9
    assert val(m, "agrifog_water_commands_total", **z) == 1
    assert val(m, "agrifog_water_commanded_seconds_total", **z) == 3600
    assert val(m, "agrifog_valve_acks_total", **z) == 1


def test_alert_raise_and_resolve():
    m, _ = make()
    m.handle("fog-b/alert", {"type": "leak", "zone": "z2", "device": None, "msg": "fuite"})
    lbl = dict(fog="fog-b", type="leak", zone="z2", device="")
    assert val(m, "agrifog_alert_active", **lbl) == 1
    assert val(m, "agrifog_alerts_total", fog="fog-b", type="leak") == 1
    m.handle("fog-b/alert", {"type": "leak", "zone": "z2", "device": None, "resolved": "2025-07-01T06:00:00Z"})
    assert val(m, "agrifog_alert_active", **lbl) == 0
    assert val(m, "agrifog_alerts_total", fog="fog-b", type="leak") == 1   # la résolution n'en compte pas une nouvelle


def test_quota_budget_usage_and_agreement():
    m, _ = make()
    m.handle("fog-b/quota/budget", {"day": "d", "allocated_l": 2500, "mode": "négocié", "given_l": 300, "received_l": 0})
    m.handle("fog-b/quota/usage", {"day": "d", "used_l": 1200})
    assert val(m, "agrifog_quota_allocated_liters", fog="fog-b") == 2500
    assert val(m, "agrifog_quota_used_liters", fog="fog-b") == 1200
    assert val(m, "agrifog_quota_negotiated", fog="fog-b") == 1
    m.handle("fog-b/quota/budget", {"day": "d", "allocated_l": 3000, "mode": "isolé (voisin injoignable : 50 %)"})
    assert val(m, "agrifog_quota_negotiated", fog="fog-b") == 0
    alloc = {"fog-a": 3500, "fog-b": 2500}
    m.handle("domaine/quota/allocation/fog-a", {"allocation": alloc})
    m.handle("domaine/quota/allocation/fog-b", {"allocation": alloc})
    assert val(m, "agrifog_quota_allocation_liters", computed_by="fog-a", fog="fog-b") == \
        val(m, "agrifog_quota_allocation_liters", computed_by="fog-b", fog="fog-b") == 2500


def test_bridge_state_and_neighbour_status():
    m, clock = make()
    m.handle("$SYS/broker/connection/741e34dbdd0e.relay-wokwi/state", "1")
    assert val(m, "agrifog_bridge_up", fog="fog-b", connection="relay-wokwi") == 1
    m.handle("$SYS/broker/connection/741e34dbdd0e.relay-wokwi/state", "0")
    assert val(m, "agrifog_bridge_up", fog="fog-b", connection="relay-wokwi") == 0
    clock.t = 2000
    m.handle("domaine/fog/fog-a/status", {"fog": "fog-a", "alive": True, "unix": 1990})
    assert val(m, "agrifog_fog_alive", fog="fog-a") == 1
    assert val(m, "agrifog_fog_last_seen_timestamp_seconds", fog="fog-a") == 1990
    m.handle("domaine/fog/fog-a/status", {"fog": "fog-a", "alive": False})
    assert val(m, "agrifog_fog_alive", fog="fog-a") == 0


def test_unknown_topics_are_ignored():
    m, _ = make()
    assert m.handle("fog-b/zone/z1/somethingelse", {}) is None
    assert m.handle("fog-a/zone/z1/telemetry", {"soil_moisture": 1}) is None   # pas mon fog
    assert m.handle("fog-b/zone/z1/cmd", {"action": "stop"}) is None
