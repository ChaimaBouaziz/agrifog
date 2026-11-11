"""Real federated learning between the fogs, over the MQTT bridge.

Each fog runs:      FOG_ID=fog-a python -m app.federated
The aggregator (config `federated.aggregator`, fog-a by default) also drives the rounds.

    aggregator --- domaine/fl/global {run, round, weights} ------------------> every fog
    every fog: its zones train on their LOCAL data, the fog averages them (FedAvg level 1)
    every fog  --- domaine/fl/update/<fog> {run, round, n, weights, mae} ---> aggregator
    aggregator: FedAvg level 2 (weighted by n) -> next round ... -> final model
Only weights travel. If the neighbour stays silent, the aggregator goes on alone
(offline-first) and takes it back as soon as it answers again.
At the end each fog saves data/models/moisture_model.json (used by the irrigation engine).
"""

import argparse
import json
import threading
import time

import paho.mqtt.client as mqtt

from agrifog_common import fog_id, load_config, topics
from agrifog_common.config import REPO_ROOT
from agrifog_common.ml import save_model

from .features import CropInfo, load_history, split_by_time, zone_samples
from .fedavg import fedavg, fog_round
from .model import from_lists, init_weights, mae_pct, n_bytes, to_lists, train

MODEL_PATH = REPO_ROOT / "data" / "models" / "moisture_model.json"
MISSES_BEFORE_SOLO = 3


class Participant:
    """Trains this fog's zones on each global model it receives."""

    def __init__(self, fog: str, cfg: dict, publish, epochs: int, train_frac: float):
        self.fog, self.publish, self.epochs = fog, publish, epochs
        crop = CropInfo.from_config(cfg)
        hist = load_history(REPO_ROOT / "data" / f"history_{fog}.csv")
        split = {z: split_by_time(*zone_samples(rows, crop), train_frac) for z, rows in hist.items()}
        self.train = {z: tr for z, (tr, te) in split.items()}
        self.test = {z: te for z, (tr, te) in split.items()}
        self.seen: tuple[str, int] | None = None
        self.done = threading.Event()
        self.lock = threading.Lock()

    def mae(self, w) -> float:
        return sum(mae_pct(w, *self.test[z]) for z in self.test) / len(self.test)

    def on_global(self, msg: dict) -> None:
        with self.lock:
            key = (msg["run"], msg["round"])
            if self.seen is not None and self.seen[0] == msg["run"] and key[1] <= self.seen[1]:
                return
            self.seen = key
            w = from_lists(msg["weights"])
            mae = self.mae(w)
            if msg.get("final"):
                self._finish(w, msg, mae)
                return
            w_fog, n = fog_round(w, self.train, self.epochs, seed=msg["round"])
            self.publish(topics.fl_update(self.fog), {"run": msg["run"], "round": msg["round"], "fog": self.fog,
                                                       "n": n, "weights": to_lists(w_fog), "mae": round(mae, 4)})
            if msg["round"] % 10 == 0 or msg["round"] == 1:
                print(f"[tour {msg['round']:3d}] modèle global : erreur {mae:.2f} % sur mes jours de test")

    def _finish(self, w, msg: dict, mae: float) -> None:
        rounds = msg["round"] - 1
        local = {z: train(init_weights(0), *self.train[z], epochs=rounds * self.epochs, seed=0) for z in self.train}
        mae_local = sum(mae_pct(local[z], *self.test[z]) for z in self.test) / len(self.test)
        save_model(MODEL_PATH, w, {"run": msg["run"], "rounds": rounds, "fogs": msg.get("fogs", []),
                                   "mae_federated": round(mae, 3), "mae_local": round(mae_local, 3),
                                   "trained_on": time.strftime("%Y-%m-%d %H:%M")})
        print(f"\n=== {self.fog} : modèle fédéré final ({rounds} tours, fogs {', '.join(msg.get('fogs', []))})")
        print(f"    erreur à +6 h sur mes jours de test : fédéré {mae:.2f} %  |  mes zones seules {mae_local:.2f} %")
        print(f"    modèle enregistré : {MODEL_PATH}")
        self.done.set()


class Aggregator:
    def __init__(self, fogs: list[str], publish, rounds: int, timeout_s: float):
        self.fogs, self.publish, self.rounds, self.timeout = fogs, publish, rounds, timeout_s
        self.run_id = time.strftime("%Y%m%dT%H%M%S")
        self.updates: dict[int, dict[str, dict]] = {}
        self.cond = threading.Condition()
        self.misses = {f: 0 for f in fogs}

    def on_update(self, msg: dict) -> None:
        if msg.get("run") != self.run_id:
            return
        with self.cond:
            self.updates.setdefault(msg["round"], {})[msg["fog"]] = msg
            self.cond.notify_all()

    def run(self) -> None:
        w, sent = init_weights(0), 0
        for r in range(1, self.rounds + 1):
            self.publish(topics.FL_GLOBAL, {"run": self.run_id, "round": r, "weights": to_lists(w)}, True)
            sent += n_bytes(w)
            expected = [f for f in self.fogs if self.misses[f] < MISSES_BEFORE_SOLO]
            deadline = time.time() + self.timeout
            with self.cond:
                while time.time() < deadline:
                    got = self.updates.get(r, {})
                    if all(f in got for f in expected) or (got and not expected):
                        break
                    self.cond.wait(timeout=0.2)
                got = dict(self.updates.get(r, {}))
            for f in self.fogs:
                if f in got:
                    if self.misses[f] >= MISSES_BEFORE_SOLO:
                        print(f"[tour {r}] {f} est de retour")
                    self.misses[f] = 0
                else:
                    self.misses[f] += 1
                    if self.misses[f] == MISSES_BEFORE_SOLO:
                        print(f"[tour {r}] {f} ne répond plus : on continue sans lui (il peut revenir)")
            if not got:
                print(f"[tour {r}] aucune mise à jour, on réessaie")
                continue
            w, n = fedavg([(from_lists(u["weights"]), u["n"]) for u in got.values()])
            sent += sum(n_bytes(w) for _ in got)
            if r % 10 == 0 or r == 1:
                maes = "  ".join(f"{f} {u['mae']:.2f} %" for f, u in sorted(got.items()))
                print(f"[agrégateur tour {r:3d}] {len(got)} fog(s), {n} échantillons | erreurs : {maes}")
        participants = sorted({f for rnd in self.updates.values() for f in rnd})
        self.publish(topics.FL_GLOBAL, {"run": self.run_id, "round": self.rounds + 1, "final": True,
                                        "fogs": participants, "weights": to_lists(w)}, True)
        print(f"[agrégateur] terminé : {self.rounds} tours, {sent / 1024:.0f} Ko de poids échangés au total")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=100)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=20.0, help="attente max d'un fog par tour (s)")
    ap.add_argument("--train-frac", type=float, default=0.3)
    args = ap.parse_args()

    cfg, fog = load_config(), fog_id()
    aggregator_fog = cfg.get("federated", {}).get("aggregator", "fog-a")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"fed-learning-{fog}")
    publish = lambda topic, payload, retain=False: client.publish(topic, json.dumps(payload), qos=1, retain=retain)

    part = Participant(fog, cfg, publish, args.epochs, args.train_frac)
    agg = Aggregator(sorted({fog, cfg["neighbor"]}), publish, args.rounds, args.timeout) if fog == aggregator_fog else None
    print(f"fed-learning {fog} : {sum(len(x) for x, _ in part.train.values())} échantillons d'entraînement "
          f"sur {len(part.train)} zones (ils ne quittent pas ce PC)"
          + (" — agrégateur" if agg else f" — agrégateur : {aggregator_fog}"))

    def on_connect(c, u, f, rc, p):
        c.subscribe(topics.FL_GLOBAL, qos=1)
        if agg:
            c.subscribe(topics.fl_update("+"), qos=1)

    def on_message(c, u, m):
        msg = json.loads(m.payload)
        if m.topic == topics.FL_GLOBAL:
            threading.Thread(target=part.on_global, args=(msg,), daemon=True).start()
        elif agg:
            agg.on_update(msg)

    client.on_connect, client.on_message = on_connect, on_message
    client.connect(cfg["mqtt"]["host"], cfg["mqtt"]["port"])
    client.loop_start()
    if agg:
        time.sleep(1)
        threading.Thread(target=agg.run, daemon=True).start()
    else:
        print("en attente des tours de l'agrégateur…")
    try:
        part.done.wait()
        time.sleep(1)
    except KeyboardInterrupt:
        pass
    client.loop_stop()


if __name__ == "__main__":
    main()
