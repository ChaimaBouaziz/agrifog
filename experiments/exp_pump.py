"""Expérience 4 — pompe commune : sans coordination vs pump-scheduler, selon la latence du pont.

    python exp_pump.py               (depuis le dossier experiments/)

Matin d'été, heure fraîche : les 3 zones de chaque fog veulent arroser en même temps (tomate 60 min,
olivier 90 min). Deux pump-scheduler (le vrai code) reliés par un pont MQTT simulé dont on fait varier
la latence ; les vannes n'exécutent l'ordre qu'à leur réveil (0 à 10 s réelles, comme les ESP32).

Indicateurs (sur 30 matins tirés au hasard) :
  - chevauchement : minutes (simulées) où DEUX vannes de fogs différents tirent sur le forage en même temps
    -> forage surchargé, pression insuffisante, arrosages ratés ;
  - durée du tour d'arrosage (du 1er ordre à la fin du dernier arrosage) et attente moyenne (minutes simulées) ;
  - pompe réservée à vide : part du tour où la pompe attend qu'une vanne se réveille.
On laisse ici chaque ordre attendre aussi longtemps qu'il faut (MAX_WAIT_S levé) : on mesure l'ordonnancement.
Résultats : results/exp4_pump.csv
"""

import random
from datetime import datetime, timedelta, timezone

from platform_lib import SPEED, Clock, service, sim_min, write_csv

ps = service("pump_app", "pump-scheduler")
from pump_app import scheduler as sched_mod  # noqa: E402

RUNS = 30
DEMAND = {"fog-a": 40, "fog-b": 40}  # minutes simulées par zone (dose habituelle aux heures fraîches)
STEP = 0.05


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


class Field:
    """Deux fogs, un pont (latence), des vannes qui dorment."""

    def __init__(self, latency: float, coordinated: bool, rng: random.Random, wake_s: float):
        self.clock, self.rng, self.latency, self.coordinated = Clock(), rng, latency, coordinated
        self.wake_s = wake_s
        self.inflight, self.acks, self.opens, self.nodes = [], [], [], {}
        for fog in DEMAND:
            s = sched_mod.PumpScheduler(fog, SPEED, wake_s, clock=self.clock)
            s.publish = lambda topic, payload, retain, src=fog: self._publish(src, topic, payload)
            self.nodes[fog] = s

    def _publish(self, src, topic, payload):
        now = self.clock()
        if topic.endswith("/cmd"):
            delay = self.rng.uniform(0, self.wake_s)                # la vanne se réveille
            self.acks.append((now + delay, src, payload))
        elif topic.startswith("domaine/pump/claim/") and self.coordinated:
            for fog, n in self.nodes.items():
                if fog != src:
                    jitter = self.rng.uniform(0.5, 1.5)
                    self.inflight.append((now + self.latency * jitter, n, src, payload))

    def run(self, seconds: float) -> None:
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t = round(self.clock.t + STEP, 4)
            now = self.clock.t
            for m in [m for m in self.inflight if m[0] <= now]:
                self.inflight.remove(m)
                m[1].on_claim(m[2], m[3])
            for a in [a for a in self.acks if a[0] <= now]:
                self.acks.remove(a)
                cmd = a[2]
                opened = datetime(2025, 7, 1, 5, tzinfo=timezone.utc)
                self.opens.append((a[1], now, now + cmd["seconds"] / SPEED))     # la vanne coule
                self.nodes[a[1]].on_valve({"id": cmd["id"], "opened": iso(opened),
                                           "until": iso(opened + timedelta(seconds=cmd["seconds"]))})
            for n in self.nodes.values():
                n.tick()


def overlap_s(opens) -> float:
    total = 0.0
    a = [(s, e) for f, s, e in opens if f == "fog-a"]
    b = [(s, e) for f, s, e in opens if f == "fog-b"]
    for s1, e1 in a:
        for s2, e2 in b:
            total += max(0.0, min(e1, e2) - max(s1, s2))
    return total


def morning(latency: float, coordinated: bool, settle: float, wake_s: float, seed: int) -> dict:
    sched_mod.SETTLE_S, sched_mod.MAX_WAIT_S = settle, 1e9
    rng = random.Random(seed)
    f = Field(latency, coordinated, rng, wake_s)
    t0 = f.clock.t
    requests = []
    for fog, minutes in DEMAND.items():
        for i, z in enumerate(("z1", "z2", "z3")):
            requests.append((rng.uniform(0, 3), fog, {"id": f"{fog}-{z}-{seed}", "fog": fog, "zone": z,
                                                      "seconds": minutes * 60, "ts": "x"}))
    for at, fog, req in sorted(requests, key=lambda r: r[0]):
        f.run(at - (f.clock.t - t0))
        f.nodes[fog].on_request(req)
    f.run(400)
    hist = [h for n in f.nodes.values() for h in n.history]
    waits = [h["waited_s"] for h in hist]
    end = max(e for _, _, e in f.opens) if f.opens else t0
    pumping = sum(e - s for _, s, e in f.opens)
    span = end - t0
    return {"served": len(hist), "overlap_min": sim_min(overlap_s(f.opens)),
            "makespan_min": sim_min(span), "wait_min": sim_min(sum(waits) / len(waits)) if waits else 0,
            "idle_pct": max(0.0, 100 * (1 - pumping / span)) if span else 0}


def main() -> None:
    configs = [("sans coordination", 0.2, False, 1.5, 10)]
    configs += [(f"pump-scheduler, pont {lat:g} s", lat, True, 1.5, 10) for lat in (0.1, 0.5, 1.0, 2.0, 3.0)]
    configs += [("pump-scheduler sans attente de 1,5 s (pont 0.5 s)", 0.5, True, 0.0, 10)]
    configs += [("pump-scheduler, vannes réveillées /2 s", 0.5, True, 1.5, 2)]
    rows = []
    for label, lat, coord, settle, wake in configs:
        res = [morning(lat, coord, settle, wake, seed) for seed in range(RUNS)]
        n = len(res)
        rows.append({"policy": label, "latency_s": lat, "settle_s": settle if coord else "",
                     "valve_wake_s": wake, "mornings": n,
                     "mornings_with_overlap": sum(r["overlap_min"] > 0.01 for r in res),
                     "overlap_min_mean": round(sum(r["overlap_min"] for r in res) / n, 1),
                     "makespan_min_mean": round(sum(r["makespan_min"] for r in res) / n, 1),
                     "wait_min_mean": round(sum(r["wait_min"] for r in res) / n, 1),
                     "pump_idle_pct": round(sum(r["idle_pct"] for r in res) / n, 1),
                     "served_mean": round(sum(r["served"] for r in res) / n, 1)})
    path = write_csv("exp4_pump.csv", rows)

    print(f"Expérience 4 — pompe commune, {RUNS} matins (6 arrosages de 40 min : 3 tomate + 3 olivier)\n")
    print(f"{'politique':50} {'matins avec':>12} {'chevauch.':>10} {'durée tour':>11} {'attente':>9} {'pompe':>8}")
    print(f"{'':50} {'chevauchement':>12} {'min/matin':>10} {'min':>11} {'min':>9} {'à vide':>8}")
    for r in rows:
        print(f"{r['policy']:50} {r['mornings_with_overlap']:6d}/{r['mornings']:<5d} {r['overlap_min_mean']:10.1f} "
              f"{r['makespan_min_mean']:11.0f} {r['wait_min_mean']:9.0f} {r['pump_idle_pct']:7.0f}%")
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
