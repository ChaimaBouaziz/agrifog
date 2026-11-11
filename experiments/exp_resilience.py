"""Experiment 3 — detection times, measured on the RUNNING system (real seconds).

Start first, on the fog: Docker (Mosquitto), the simulator and the irrigation engine, then:
    $env:FOG_ID="fog-a"; python exp_resilience.py --runs 3

For each fault: inject it on {fog}/sim/fault, start a stopwatch, stop it when the matching
alert arrives on {fog}/alert, then repair and time the "resolved" message.
Results: results/exp3_resilience.csv
"""

import argparse
import csv
import json
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs" / "agrifog_common"))

import paho.mqtt.client as mqtt  # noqa: E402

from agrifog_common import fog_id, load_config, topics  # noqa: E402

OUT = Path(__file__).parent / "results"


class Watcher:
    def __init__(self):
        self.alerts: list[tuple[float, dict]] = []
        self.cond = threading.Condition()

    def on_alert(self, msg: dict) -> None:
        with self.cond:
            self.alerts.append((time.time(), msg))
            self.cond.notify_all()

    def wait_for(self, since: float, match, timeout: float) -> float | None:
        deadline = time.time() + timeout
        with self.cond:
            while time.time() < deadline:
                for t, msg in self.alerts:
                    if t >= since and match(msg):
                        return t - since
                self.cond.wait(timeout=0.2)
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=120)
    args = ap.parse_args()

    cfg, fog = load_config(), fog_id()
    zones = [z["id"] for z in cfg["zones"]]
    leak_zone, pump_zone = zones[1], zones[0]
    silent_dev = cfg["zones"][-1]["devices"][-1]
    w = Watcher()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"exp-resilience-{fog}")
    client.on_connect = lambda c, u, f, rc, p: c.subscribe(topics.alert(fog), qos=1)
    client.on_message = lambda c, u, m: w.on_alert(json.loads(m.payload))
    client.connect(cfg["mqtt"]["host"], cfg["mqtt"]["port"])
    client.loop_start()
    fault = lambda payload: client.publish(topics.sim_fault(fog), json.dumps(payload), qos=1)

    scenarios = [
        ("fuite", {"type": "leak", "zone": leak_zone, "lpm": 6},
         lambda m: m["type"] == "leak" and m.get("zone") == leak_zone,
         {"type": "leak", "zone": leak_zone, "on": False}),
        ("nœud muet", {"type": "offline", "device": silent_dev},
         lambda m: m["type"] == "offline" and m.get("device") == silent_dev,
         {"type": "offline", "device": silent_dev, "on": False}),
        ("pompe en panne", {"type": "pump_fail", "zone": pump_zone},
         lambda m: m["type"] == "pump" and m.get("zone") == pump_zone,
         {"type": "pump_fail", "zone": pump_zone, "on": False}),
    ]
    print(f"Mesures sur {fog} ({args.runs} essais par panne). Le simulateur et le moteur doivent tourner.\n")
    rows = []
    for name, inject, match, repair in scenarios:
        for run in range(1, args.runs + 1):
            fault({"type": "clear"})
            time.sleep(3)
            t0 = time.time()
            fault(inject)
            if name == "pompe en panne":   # il faut un arrosage pour voir qu'il ne coule rien
                client.publish(topics.cmd(fog, pump_zone), json.dumps(
                    {"id": f"exp-{int(t0)}", "action": "water", "seconds": 3600}), qos=1)
            detect = w.wait_for(t0, lambda m: match(m) and not m.get("resolved"), args.timeout)
            t1 = time.time()
            fault(repair)
            resolve = w.wait_for(t1, lambda m: match(m) and m.get("resolved"), args.timeout) \
                if name != "pompe en panne" else None
            rows.append({"panne": name, "essai": run,
                         "detection_s": None if detect is None else round(detect, 1),
                         "resolution_s": None if resolve is None else round(resolve, 1)})
            print(f"  {name:15} essai {run} : détection "
                  f"{'pas d alerte' if detect is None else f'{detect:.1f} s'}"
                  + ("" if resolve is None else f", retour à la normale {resolve:.1f} s"))
    fault({"type": "clear"})
    client.loop_stop()

    OUT.mkdir(exist_ok=True)
    with open(OUT / "exp3_resilience.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    print("\nRésumé (secondes réelles) :")
    for name, *_ in scenarios:
        vals = [r["detection_s"] for r in rows if r["panne"] == name and r["detection_s"] is not None]
        if vals:
            print(f"  {name:15} détection moyenne {statistics.mean(vals):5.1f} s  (min {min(vals):.1f}, max {max(vals):.1f})")
    print("Ces temps dépendent de la période de réveil des ESP32 (--period) et de la vitesse de simulation.")


if __name__ == "__main__":
    main()
