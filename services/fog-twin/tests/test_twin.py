"""Le fog jumeau de fog-b face aux pannes de fog-a (horloge et réseau simulés)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from agrifog_common import load_config

from app.brain import View, assess
from app.twin import FogTwin


# ------------------------------------------------------------------ brain
@pytest.mark.parametrize("view, expected", [
    (View(now=100, neighbor="fog-a"), "voisin-inconnu"),
    (View(now=100, neighbor="fog-a", neighbor_last=90, witness_ok=True), "normal"),
    (View(now=100, neighbor="fog-a", neighbor_last=50, witness_ok=True, neighbor_witness_last=98), "partition"),
    (View(now=100, neighbor="fog-a", neighbor_last=50, witness_ok=False), "isole"),
    (View(now=100, neighbor="fog-a", neighbor_last=50, witness_ok=True, neighbor_witness_last=40), "reprise"),
    (View(now=100, neighbor="fog-a", neighbor_last=99, neighbor_alive=False, witness_ok=True), "reprise"),
    (View(now=100, neighbor="fog-a", neighbor_last=50, witness_enabled=False), "reprise"),
    (View(now=100, neighbor="fog-a", neighbor_last=99, maintenance=True), "reprise"),
])
def test_brain(view, expected):
    assert assess(view)[0] == expected


# ------------------------------------------------------------------ twin
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


ENGINE_SNAPSHOT = {"zones": {}, "budget": {"day": "2025-07-01", "allocated_l": 3200, "mode": "négocié"},
                   "used_l": {"2025-07-01": 1200.0}, "alerts": []}


def make_twin(tmp_path, monkeypatch, peer=None):
    monkeypatch.setenv("TWIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TWIN_PEER_URL", "http://fog-a:8080")
    monkeypatch.setenv("ENGINE_URL", "http://engine")
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, str(request.url)))
        if request.url.host == "engine":
            return httpx.Response(200, json=ENGINE_SNAPSHOT)
        if peer is None:
            raise httpx.ConnectError("voisin injoignable")
        return peer(request)

    clock = Clock()
    twin = FogTwin(load_config("fog-b"), "fog-b", clock=clock,
                   http=httpx.Client(transport=httpx.MockTransport(handler)), mqtt_enabled=False)
    return twin, clock, calls


def alive(twin, clock, seconds, witness=True):
    """fog-a vit : son statut (toutes les 10 s) et sa signature au témoin (toutes les 5 s)."""
    end = clock.t + seconds
    while clock.t < end:
        clock.t += 5
        if int(clock.t) % 10 == 0:
            twin.on_neighbor_status({"fog": "fog-a", "alive": True})
        if witness:
            twin.on_witness("fog-a", {"local_ok": True})
        twin.on_witness("fog-b", {"local_ok": True})
        twin.step()


def silent(twin, clock, seconds, neighbor_witness=False, self_witness=True):
    end = clock.t + seconds
    while clock.t < end:
        clock.t += 5
        if neighbor_witness:
            twin.on_witness("fog-a", {"local_ok": True})
        if self_witness:
            twin.on_witness("fog-b", {"local_ok": True})
        twin.step()


def test_dead_neighbour_parcel_is_taken_over_with_its_backup(tmp_path, monkeypatch):
    twin, clock, _ = make_twin(tmp_path, monkeypatch)
    twin.store.save("fog-a", {"fog": "fog-a", "unix": 990, "data": ENGINE_SNAPSHOT})
    alive(twin, clock, 60)
    assert twin.state == "normal" and twin.takeover is None
    silent(twin, clock, 20)
    assert twin.takeover is None                       # 20 s de silence : on patiente encore
    silent(twin, clock, 20)
    assert twin.state == "reprise" and twin.takeover.target == "fog-a"
    eng = twin.takeover.engine
    assert eng.fog == "fog-a" and eng.crop.name == "tomate"            # la config du VOISIN
    assert eng.used_l["2025-07-01"] == 1200 and eng.budget["allocated_l"] == 3200   # son état restauré


def test_partition_is_not_mistaken_for_a_dead_neighbour(tmp_path, monkeypatch):
    twin, clock, _ = make_twin(tmp_path, monkeypatch)
    alive(twin, clock, 60)
    silent(twin, clock, 120, neighbor_witness=True)    # pont coupé, mais fog-a parle au témoin
    assert twin.state == "partition" and twin.takeover is None


def test_isolated_fog_does_not_take_over(tmp_path, monkeypatch):
    twin, clock, _ = make_twin(tmp_path, monkeypatch)
    alive(twin, clock, 60)
    silent(twin, clock, 120, self_witness=False)       # nous ne joignons plus rien : nous sommes coupés
    assert twin.state == "isole" and twin.takeover is None


def test_takeover_ends_when_the_neighbour_comes_back_and_its_litres_are_handed_back(tmp_path, monkeypatch):
    twin, clock, _ = make_twin(tmp_path, monkeypatch)
    alive(twin, clock, 60)
    silent(twin, clock, 60)
    assert twin.takeover
    twin.takeover.engine.used_l["2025-07-01"] = 1500.0     # 1500 L arrosés pendant la reprise
    alive(twin, clock, 20)
    assert twin.state == "normal" and twin.takeover is None
    answer = twin.on_takeover_call("fog-a", "release")     # fog-a revenu redemande sa parcelle
    assert answer == {"released": True, "used_l": {"2025-07-01": 1500.0}}
    assert twin.on_takeover_call("fog-a", "release")["released"] is False   # déjà rendu


def test_planned_maintenance(tmp_path, monkeypatch):
    twin, clock, _ = make_twin(tmp_path, monkeypatch)
    alive(twin, clock, 30)
    twin.on_takeover_call("fog-a", "request")            # fog-a : « je m'arrête, reprends ma parcelle »
    assert twin.state == "reprise" and twin.takeover
    alive(twin, clock, 30)                               # tant que la maintenance dure, on garde la parcelle
    assert twin.takeover
    assert twin.on_takeover_call("fog-a", "release")["released"] is True
    assert twin.takeover is None


def test_only_the_neighbour_can_call_takeover(tmp_path, monkeypatch):
    twin, _, _ = make_twin(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        twin.on_takeover_call("fog-z", "request")


def test_backup_is_sent_to_the_neighbour(tmp_path, monkeypatch):
    received = []

    def peer(request):
        received.append(request.read())
        return httpx.Response(200, json={"stored": "fog-b"})

    twin, _, calls = make_twin(tmp_path, monkeypatch, peer=peer)
    twin.backup_once()
    assert ("POST", "http://fog-a:8080/backup") in calls and twin.backup_ok
    assert b'"used_l"' in received[0]


def test_back_from_outage_restores_the_litres_used_by_the_neighbour(tmp_path, monkeypatch):
    def peer(request):
        return httpx.Response(200, json={"released": True, "used_l": {"2025-07-01": 900.0}})

    published = []
    twin, _, _ = make_twin(tmp_path, monkeypatch, peer=peer)
    twin._publish = lambda topic, payload, retain=False: published.append((topic, payload, retain))
    assert twin.reclaim()
    assert ("fog-b/quota/usage", {"day": "2025-07-01", "used_l": 900.0}, True) in published


def test_backup_api_round_trip():
    from app.main import app, twin
    c, n = TestClient(app), twin.neighbor
    assert c.post("/backup", json={"fog": n, "unix": 1.0, "data": {"used_l": {}}}).status_code == 200
    assert c.get(f"/backup/{n}").json()["unix"] == 1.0
    assert c.get("/backup/fog-z").status_code == 404
    assert c.post("/backup", json={"fog": "fog-z", "unix": 1.0, "data": {}}).status_code == 400
    assert c.post("/takeover", json={"from": "fog-z", "action": "request"}).status_code == 400
