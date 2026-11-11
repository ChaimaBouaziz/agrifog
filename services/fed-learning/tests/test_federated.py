"""The real protocol, with both fogs wired through a fake bus instead of MQTT."""

import threading

import pytest

from agrifog_common import load_config
from agrifog_common.config import REPO_ROOT

import app.federated as fed

pytestmark = pytest.mark.skipif(
    not all((REPO_ROOT / "data" / f"history_{f}.csv").exists() for f in ("fog-a", "fog-b")),
    reason="générer d'abord l'historique : python -m simulator.history --fog fog-a / fog-b")


def test_two_fogs_train_one_global_model(tmp_path, monkeypatch):
    monkeypatch.setattr(fed, "MODEL_PATH", tmp_path / "model.json")
    participants, agg = {}, None

    def bus(topic, payload, retain=False):
        if topic == "domaine/fl/global":
            for p in participants.values():
                p.on_global(payload)
        elif topic.startswith("domaine/fl/update/"):
            agg.on_update(payload)

    for f in ("fog-a", "fog-b"):
        participants[f] = fed.Participant(f, load_config(f), bus, epochs=1, train_frac=0.3)
    agg = fed.Aggregator(["fog-a", "fog-b"], bus, rounds=5, timeout_s=5)
    t = threading.Thread(target=agg.run)
    t.start()
    t.join(timeout=60)
    assert all(p.done.is_set() for p in participants.values())
    assert (tmp_path / "model.json").exists()
    assert set(agg.updates[5]) == {"fog-a", "fog-b"}


def test_aggregator_goes_on_alone_when_neighbour_is_silent():
    sent = []
    agg = fed.Aggregator(["fog-a", "fog-b"], lambda t, p, r=False: sent.append((t, p)), rounds=4, timeout_s=0.05)
    part = fed.Participant("fog-a", load_config("fog-a"), lambda t, p, r=False: agg.on_update(p), epochs=1,
                           train_frac=0.3)
    agg.publish = lambda t, p, r=False: (sent.append((t, p)), part.on_global(p) if t == "domaine/fl/global" else None)
    agg.run()
    final = [p for t, p in sent if p.get("final")]
    assert final and final[0]["fogs"] == ["fog-a"]
