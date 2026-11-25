"""Les scénarios jugés sans broker : on rejoue les messages que le système enverrait."""

from agrifog_common import load_config

from app.scenarios import Ctx, Runner, _next_version


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(fog="fog-a", http=None):
    cfg = load_config(fog)
    sent, clock = [], Clock()
    ctx = lambda: Ctx(fog, cfg["neighbor"], cfg, "http://peer:8080", "http://ota:8003", http=http)  # noqa: E731

    def execute(action):
        sent.append(action)
        return str(action)

    return Runner(ctx, execute, clock), sent, clock


def test_leak_detected_then_repaired():
    r, sent, clock = make()
    r.start("fuite")
    assert sent[0][1] == "fog-a/sim/fault" and sent[0][2] == {"type": "leak", "zone": "z2", "lpm": 6}
    r.on_message("fog-a/alert", {"type": "leak", "zone": "z1", "msg": "autre zone"})   # pas la bonne zone
    assert r.runs["fuite"].status == "en cours"
    clock.t += 42
    r.on_message("fog-a/alert", {"type": "leak", "zone": "z2", "msg": "FUITE sur z2", "resolved": False})
    run = r.runs["fuite"]
    assert run.status == "réussi" and run.elapsed_s == 42 and "FUITE" in run.detail
    assert sent[-1][2] == {"type": "leak", "zone": "z2", "on": False}       # remise en état


def test_timeout_fails_and_still_cleans_up():
    r, sent, clock = make()
    r.start("muet")
    clock.t += 121
    r.tick()
    assert r.runs["muet"].status == "échoué"
    assert sent[-1][2]["type"] == "offline" and sent[-1][2]["on"] is False


def test_manual_watering_matches_its_own_order():
    r, sent, _ = make()
    r.start("manuel")
    cid = sent[0][2]["id"]
    r.on_message("fog-a/zone/z1/valve", {"id": "autre", "device": "esp-a-01", "until": "x"})
    assert r.runs["manuel"].status == "en cours"
    r.on_message("fog-a/zone/z1/valve", {"id": cid, "device": "esp-a-01", "until": "2025-07-01T06:10:00Z"})
    assert r.runs["manuel"].status == "réussi"


def test_quota_agreement_from_known_state():
    r, _, _ = make()
    alloc = {"day": "2025-07-01", "allocation": {"fog-a": 4000, "fog-b": 2000}, "mode": "négocié"}
    r.on_message("domaine/quota/allocation/fog-a", alloc, retained=True)
    r.on_message("domaine/quota/allocation/fog-b", alloc, retained=True)
    r.start("quota")                                   # l'état déjà connu suffit
    assert r.runs["quota"].status == "réussi"


def test_quota_disagreement_is_a_failure():
    r, _, _ = make()
    r.start("quota")
    r.on_message("domaine/quota/allocation/fog-a", {"day": "d", "allocation": {"fog-a": 4000, "fog-b": 2000}})
    r.on_message("domaine/quota/allocation/fog-b", {"day": "d", "allocation": {"fog-a": 3000, "fog-b": 3000}})
    assert r.runs["quota"].status == "échoué" and "désaccord" in r.runs["quota"].detail


def test_neighbour_alive_needs_a_live_message_not_an_old_retained_one():
    r, _, _ = make()
    r.on_message("domaine/fog/fog-b/status", {"alive": True}, retained=True)
    r.start("voisin")
    assert r.runs["voisin"].status == "en cours"
    r.on_message("domaine/fog/fog-b/status", {"alive": True, "version": "0.3.0"})
    assert r.runs["voisin"].status == "réussi"


def test_shared_pump_overlap_is_a_failure():
    r, sent, _ = make()
    r.start("pompe_commune")
    assert [a[1] for a in sent] == ["fog-a/pump/request"] * 2
    r.on_message("domaine/pump/claim/fog-b", {"using": True, "zone": "fog-b/z1"})
    r.on_message("domaine/pump/claim/fog-a", {"using": True, "zone": "fog-a/z1"})
    assert r.runs["pompe_commune"].status == "échoué"


def test_shared_pump_served_in_turn():
    r, _, _ = make()
    r.start("pompe_commune")
    r.on_message("domaine/pump/claim/fog-a", {"using": True, "zone": "fog-a/z1"})
    r.on_message("domaine/pump/claim/fog-a", {"using": False})
    r.on_message("domaine/pump/claim/fog-b", {"using": True, "zone": "fog-b/z2"})
    r.on_message("domaine/pump/claim/fog-b", {"using": False})
    r.on_message("domaine/pump/claim/fog-a", {"using": True, "zone": "fog-a/z2"})
    assert r.runs["pompe_commune"].status == "réussi"


def test_ota_canary_crash_uses_a_fresh_version():
    calls = []

    def http(method, url, body):
        calls.append((method, url, body))
        if url.endswith("/devices"):
            return {"versions": {"0.3.0": 10}}
        if url.endswith("/releases"):
            return [{"version": "0.3.0"}, {"version": "0.3.9"}, {"version": "0.3.10"}]
        return {}

    r, sent, _ = make("fog-b", http=http)
    r.start("ota_crash")
    defect = sent[0]
    assert defect[3] == {"base": "0.3.0", "version": "0.3.11", "kind": "crash"}
    r.on_message("fog-b/ota/campaign", {"version": "0.3.11", "state": "canary"})
    assert r.runs["ota_crash"].status == "en cours"
    r.on_message("fog-b/ota/campaign", {"version": "0.3.11", "state": "rolled_back", "canary": "esp-b-01"})
    assert r.runs["ota_crash"].status == "réussi"


def test_service_down_fails_cleanly():
    def http(method, url, body):
        raise ConnectionError("ota-manager injoignable")

    r, _, _ = make("fog-b", http=http)
    r.start("ota_sensor")
    assert r.runs["ota_sensor"].status == "échoué" and "injoignable" in r.runs["ota_sensor"].detail


def test_next_version_sorts_numerically():
    assert _next_version([{"version": "0.3.9"}, {"version": "0.3.10"}, {"version": "0.3.2"}]) == "0.3.11"


def test_every_scenario_is_described():
    r, _, _ = make()
    snap = r.snapshot()
    assert len(snap) == 15 and all(s["does"] and s["expects"] for s in snap)


def test_api_lists_and_rejects_unknown():
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    assert len(c.get("/api/scenarios").json()) == 15
    assert c.post("/api/scenarios/nexiste-pas").status_code == 404
    assert c.get("/").status_code == 200


def test_sensor_test_refused_on_a_full_soil():
    r, sent, _ = make()
    r.on_message("fog-a/zone/z1/telemetry", {"device": "esp-a-01", "soil_moisture": 29.5})
    r.start("capteur")
    assert r.runs["capteur"].status == "non testable" and "--moisture 24" in r.runs["capteur"].detail
    assert not sent


def test_wrong_sensor_accused_is_a_failure():
    r, sent, _ = make()
    r.on_message("fog-a/zone/z1/telemetry", {"device": "esp-a-01", "soil_moisture": 24.0})
    r.start("capteur")
    stuck = sent[0][2]["device"]
    assert stuck == "esp-a-02" and sent[1][2]["seconds"] == 7200
    r.on_message("fog-a/alert", {"type": "sensor", "zone": "z1", "device": "esp-a-01", "msg": "…"})
    assert r.runs["capteur"].status == "échoué" and "esp-a-01" in r.runs["capteur"].detail
