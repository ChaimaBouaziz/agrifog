"""Expérience 5 — fog jumeau : reprise juste, sans split-brain, avec ou sans témoin cloud.

    python exp_twin.py               (depuis le dossier experiments/)

Le vrai fog-twin de fog-b (horloge simulée, réseau simulé) face à fog-a.

A. Les pannes, une par une (avec / sans témoin cloud) :
   - fog-a en panne (PC éteint)        -> il FAUT reprendre sa parcelle ;
   - lien coupé entre fogs (Tailscale) -> il ne faut PAS : fog-a vit, deux moteurs piloteraient
     la même parcelle (split-brain) ;
   - fog-b coupé d'internet            -> il ne faut PAS : c'est nous qui sommes isolés.
   Indicateurs : décision prise, temps de détection.
B. Coupures brèves du pont pendant 24 h de fonctionnement réel (une toutes les ~10 min, 2 à 60 s : le
   réseau, lui, vit en temps réel) : combien de reprises à tort selon le seuil de silence
   (10, 20, 30, 60 s), avec et sans témoin ?
   -> compromis détection rapide / fausses reprises.
Résultats : results/exp5_twin_decisions.csv, results/exp5_twin_blips.csv
"""

import os
import random
import tempfile

from platform_lib import Clock, sim_min, write_csv, service

service("twin_app", "fog-twin")
from twin_app import twin as twin_mod  # noqa: E402

from agrifog_common import load_config  # noqa: E402

STATUS_S, WITNESS_S = 10, 5


class Net:
    """Ce que fog-b reçoit : statut de fog-a par le pont, signatures au témoin cloud."""

    def __init__(self, witness: bool, dead_after: float):
        os.environ["TWIN_DATA_DIR"] = tempfile.mkdtemp(prefix="exp-twin-")
        os.environ["TWIN_WITNESS"] = "54.36.178.49" if witness else ""
        cfg = load_config("fog-b")
        cfg["twin"] = {**cfg.get("twin", {}), "takeover_after_s": dead_after}
        self.clock = Clock()
        self.twin = twin_mod.FogTwin(cfg, "fog-b", clock=self.clock, mqtt_enabled=False)
        self.twin.backup_once = lambda: None
        self.fog_a_alive = self.bridge_up = self.internet_b = True
        self.takeovers, self.first_takeover = 0, None
        self.t = 0

    def second(self) -> None:
        self.t += 1
        self.clock.t += 1
        if self.fog_a_alive and self.bridge_up and self.internet_b and self.t % STATUS_S == 0:
            self.twin.on_neighbor_status({"fog": "fog-a", "alive": True})
        if self.internet_b and self.t % WITNESS_S == 0:
            self.twin.on_witness("fog-b", {"local_ok": True})          # notre signature nous revient
            if self.fog_a_alive:
                self.twin.on_witness("fog-a", {"local_ok": True})
        before = self.twin.takeover is not None
        self.twin.step()
        if self.twin.takeover is not None and not before:
            self.takeovers += 1
            self.first_takeover = self.first_takeover or self.t

    def run(self, seconds: int) -> None:
        for _ in range(seconds):
            self.second()


SCENARIOS = {
    "fog-a en panne": ("fog_a_alive", True),
    "lien coupé entre fogs": ("bridge_up", False),
    "fog-b coupé d'internet": ("internet_b", False),
}


def part_a(dead_after: float = 30) -> list[dict]:
    rows = []
    for scenario, (attr, should_take_over) in SCENARIOS.items():
        for witness in (True, False):
            net = Net(witness, dead_after)
            net.run(60)                                    # tout va bien
            if attr == "internet_b":
                net.bridge_up = False                       # sans internet, plus de Tailscale non plus
            setattr(net, attr, False)
            t_fail = net.t
            net.run(180)
            took = net.takeovers > 0
            rows.append({"scenario": scenario, "witness": "avec témoin" if witness else "sans témoin",
                         "expected": "reprise" if should_take_over else "pas de reprise",
                         "decision": f"{'reprise' if took else 'pas de reprise'} (état : {net.twin.state})",
                         "correct": took == should_take_over,
                         "detection_s": (net.first_takeover - t_fail) if took else "",
                         "detection_min_sim": round(sim_min(net.first_takeover - t_fail)) if took else ""})
    return rows


def part_b(hours: int = 24, seed: int = 7) -> list[dict]:
    rows = []
    seconds = hours * 3600
    for dead_after in (10, 20, 30, 60):
        for witness in (True, False):
            rng = random.Random(seed)
            net = Net(witness, dead_after)
            net.run(30)
            blips = 0
            next_blip = net.t + int(rng.expovariate(1 / 600))     # une coupure toutes les ~10 min
            while net.t < seconds:
                if net.t >= next_blip:
                    net.bridge_up = False
                    blips += 1
                    net.run(int(rng.uniform(2, 60)))
                    net.bridge_up = True
                    next_blip = net.t + int(rng.expovariate(1 / 600))
                net.second()
            rows.append({"threshold_s": dead_after, "threshold_min_sim": round(sim_min(dead_after)),
                         "witness": "avec témoin" if witness else "sans témoin",
                         "blips": blips, "false_takeovers_24h": net.takeovers})
    return rows


def main() -> None:
    a = part_a()
    pa = write_csv("exp5_twin_decisions.csv", a)
    print("Expérience 5A — décisions du fog jumeau de fog-b (seuil de silence : 30 s = 3 h simulées)\n")
    print(f"{'scénario':24} {'témoin':12} {'attendu':15} {'décision':38} {'détection':>10}")
    for r in a:
        det = f"{r['detection_s']} s" if r["detection_s"] != "" else "–"
        mark = "✓" if r["correct"] else "✗ SPLIT-BRAIN" if r["expected"] == "pas de reprise" else "✗"
        print(f"{r['scenario']:24} {r['witness']:12} {r['expected']:15} {r['decision']:38} {det:>10}  {mark}")
    b = part_b()
    pb = write_csv("exp5_twin_blips.csv", b)
    print(f"\nExpérience 5B — 24 h de fonctionnement, {b[0]['blips']} coupures brèves du pont (2 à 60 s)\n")
    print(f"{'seuil de silence':>18} {'= simulé':>9} {'avec témoin':>12} {'sans témoin':>12}   (reprises à tort)")
    for th in (10, 20, 30, 60):
        w = next(r for r in b if r["threshold_s"] == th and r["witness"] == "avec témoin")
        n = next(r for r in b if r["threshold_s"] == th and r["witness"] == "sans témoin")
        print(f"{th:16d} s {w['threshold_min_sim']:6d} min {w['false_takeovers_24h']:12d} {n['false_takeovers_24h']:12d}")
    print(f"\n-> {pa}\n-> {pb}")


if __name__ == "__main__":
    main()
