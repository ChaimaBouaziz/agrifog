"""Canary campaign: healthy -> rollout ; crash -> device rolled back itself ; sensor -> rollback sent."""

import pytest

from agrifog_common import load_config

from test_ota_manager import fake_web
from app.catalog import Catalog
from app.manager import OtaManager


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def setup(tmp_path):
    cat = Catalog(tmp_path, download=fake_web())
    cat.add_github("fw-v0.3.0")
    clock, sent = Clock(), []
    m = OtaManager(load_config("fog-b"), "fog-b", cat, "http://fog:8003",
                   publish=lambda t, p: sent.append((t, p)), clock=clock)
    for d in m.known:                                   # toute la flotte tourne en 0.3.0
        m.on_heartbeat(d, {"fw_version": "0.3.0"})
        m.on_telemetry(d)
    return m, cat, clock, sent


def orders(sent):
    return [(t.split("/")[2], p["version"]) for t, p in sent if t.endswith("/ota")]


def run(m, clock, seconds, device="esp-b-01", heartbeat=None, telemetry=True, step=10):
    for _ in range(int(seconds // step)):
        clock.t += step
        if heartbeat:
            m.on_heartbeat(device, {"fw_version": heartbeat})
        if telemetry:
            m.on_telemetry(device)
        m.tick()


def test_healthy_canary_then_rollout_then_done(tmp_path):
    m, cat, clock, sent = setup(tmp_path)
    cat.make_defect("0.3.0", "0.3.1", "sensor")          # ota-manager ne lit pas le binaire : seul le
    m.start_campaign("0.3.1", observe_s=60)              # comportement observé du canary compte ici
    assert orders(sent) == [("esp-b-01", "0.3.1")]      # le canary seulement
    m.on_status("esp-b-01", {"state": "installed", "version": "0.3.1"})
    run(m, clock, 60, heartbeat="0.3.1")
    assert m.status()["state"] == "rollout"
    assert len(orders(sent)) == 1 + 9                   # puis les 9 autres ESP32 simulés (pas Wokwi : pas d'URL)
    for d in m.known:
        m.on_heartbeat(d, {"fw_version": "0.3.1"})
    m.tick()
    assert m.status()["state"] == "done"


def test_crashing_canary_rolls_back_by_itself_and_version_is_blocked(tmp_path):
    m, cat, clock, sent = setup(tmp_path)
    cat.make_defect("0.3.0", "0.3.1", "crash")
    m.start_campaign("0.3.1", observe_s=60)
    m.on_status("esp-b-01", {"state": "installed", "version": "0.3.1"})
    run(m, clock, 30, telemetry=False)                  # il plante : silence
    m.on_status("esp-b-01", {"state": "rolled_back", "version": "0.3.1", "running": "0.3.0",
                             "detail": "3 démarrages ratés"})
    m.tick()
    st = m.status()
    assert st["state"] == "rolled_back" and "déjà revenu" in st["detail"]
    assert orders(sent) == [("esp-b-01", "0.3.1")]      # la flotte n'a jamais reçu 0.3.1
    assert cat.get("0.3.1").blocked
    with pytest.raises(ValueError):
        m.deploy("0.3.1", ["esp-b-02"])                 # version bloquée


def test_sensor_defect_is_rolled_back_by_ota_manager(tmp_path):
    m, cat, clock, sent = setup(tmp_path)
    cat.make_defect("0.3.0", "0.3.1", "sensor")
    m.start_campaign("0.3.1", observe_s=60)
    m.on_status("esp-b-01", {"state": "installed", "version": "0.3.1"})
    run(m, clock, 60, heartbeat="0.3.1", telemetry=False)   # vivant, mais plus aucune mesure
    st = m.status()
    assert st["state"] == "rolled_back" and orders(sent)[-1] == ("esp-b-01", "0.3.0")
    assert "mesure" in cat.get("0.3.1").blocked


def test_one_campaign_at_a_time(tmp_path):
    m, cat, _, _ = setup(tmp_path)
    cat.make_defect("0.3.0", "0.3.1", "sensor")
    m.start_campaign("0.3.1")
    with pytest.raises(ValueError):
        m.start_campaign("0.3.0")
