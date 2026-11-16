"""Expérience 6 — mise à jour OTA : tout d'un coup (big bang) vs canary + rollback.

    python exp_ota.py               (depuis le dossier experiments/)

Le vrai ota-manager de fog-b pilote les 10 ESP32 simulés de la parcelle olivier. Chaque ESP32 se
réveille toutes les 10 s réelles (1 h simulée) et se comporte comme le firmware :
  - à son réveil il prend l'ordre de mise à jour, installe, redémarre ;
  - firmware « crash » : 3 démarrages ratés, puis le bootloader revient seul à l'ancienne version ;
  - firmware « sensor » : il tourne (heartbeat) mais ne mesure plus rien -> personne ne s'en aperçoit
    sans supervision : seul ota-manager (canary) le détecte et renvoie l'ancienne version.
Trois firmwares (sain, crash, sensor) × deux stratégies, 20 tirages des heures de réveil.

Indicateurs :
  - ESP32 exposés : combien ont installé le mauvais firmware ;
  - mesures perdues : réveils sans mesure d'humidité (1 réveil = 1 h simulée de parcelle aveugle) ;
  - temps pour que toute la flotte tourne sur une version saine (la nouvelle si elle est saine).
Résultats : results/exp6_ota.csv
"""

import hashlib
import random
import tempfile
from pathlib import Path

from platform_lib import Clock, service, sim_min, write_csv

service("ota_app", "ota-manager")
from ota_app.catalog import Catalog  # noqa: E402
from ota_app.manager import OtaManager  # noqa: E402

from agrifog_common import load_config  # noqa: E402

RUNS, WAKE_S, HORIZON_S, OBSERVE_S = 20, 10, 900, 60
BLOB = b"agrifog firmware 0.3.0"


class Esp:
    def __init__(self, name: str, phase: float):
        self.name, self.next_wake = name, phase
        self.version, self.previous, self.defect = "0.3.0", None, None
        self.boot_failures, self.pending = 0, None
        self.blind_wakes = 0
        self.bad_since, self.bad_until = None, None


def run(firmware: str, strategy: str, seed: int) -> dict:
    rng, clock = random.Random(seed), Clock()
    cat = Catalog(Path(tempfile.mkdtemp(prefix="exp-ota-")), download=lambda url: BLOB)
    cat.add("0.3.0", "https://x/fw.bin", hashlib.sha256(BLOB).hexdigest())
    target = "0.3.1"
    if firmware == "sain":
        cat.add("0.3.1", "https://x/fw.bin", hashlib.sha256(BLOB).hexdigest())
    else:
        cat.make_defect("0.3.0", target, firmware)
    fleet: dict[str, Esp] = {}
    m = OtaManager(load_config("fog-b"), "fog-b", cat, "http://fog-b:8003", clock=clock,
                   publish=lambda t, p: fleet[t.split("/")[2]].__setattr__("pending", p)
                   if t.endswith("/ota") and t.split("/")[2] in fleet else None)
    fleet.update({d: Esp(d, clock.t + rng.uniform(0, WAKE_S)) for d in m.known if d not in m.public})
    for e in fleet.values():
        m.on_heartbeat(e.name, {"fw_version": e.version})
        m.on_telemetry(e.name)
    t0 = clock.t
    if strategy == "big bang":
        m.deploy(target, [d for d in fleet])
    else:
        m.start_campaign(target, observe_s=OBSERVE_S)
    exposed, safe_at = set(), None
    healthy = firmware == "sain"

    while clock.t - t0 < HORIZON_S:
        clock.t += 1
        for e in fleet.values():
            if clock.t < e.next_wake:
                continue
            e.next_wake += WAKE_S
            wake(e, m, exposed, None if healthy else firmware)
        m.tick()
        if healthy and safe_at is None and all(e.version == target for e in fleet.values()):
            safe_at = clock.t - t0
    if not healthy:     # flotte saine = le dernier ESP32 exposé a quitté le mauvais firmware
        if any(e.bad_since is not None and e.bad_until is None for e in fleet.values()):
            safe_at = None
        else:
            safe_at = max([e.bad_until - t0 for e in fleet.values() if e.bad_until is not None], default=0)
    return {"exposed": len(exposed), "blind_wakes": sum(e.blind_wakes for e in fleet.values()),
            "safe_s": safe_at, "campaign": (m.status() or {}).get("state", "")}


def wake(e: Esp, m: OtaManager, exposed: set, bad_kind: str | None) -> None:
    if e.defect == "crash":                              # le firmware plante au démarrage
        e.boot_failures += 1
        e.blind_wakes += 1
        if e.boot_failures >= 3:                         # le bootloader revient à l'ancienne partition
            bad, e.version, e.defect, e.boot_failures = e.version, e.previous, None, 0
            e.bad_until = m.clock()
            m.on_status(e.name, {"state": "rolled_back", "version": bad, "running": e.version, "from": e.version})
        return
    if e.pending and e.pending["version"] != e.version:
        order, e.pending = e.pending, None
        e.previous, e.version = e.version, order["version"]
        was_bad, e.defect = e.defect, bad_kind if e.version == "0.3.1" else None
        if e.defect:
            exposed.add(e.name)
            e.bad_since, e.bad_until = m.clock(), None
        elif was_bad:                                    # rollback envoyé par ota-manager
            e.bad_until = m.clock()
        m.on_status(e.name, {"state": "installed", "version": e.version, "from": e.previous, "id": order["id"]})
        if e.defect == "crash":                          # redémarre... et plante
            e.boot_failures, e.blind_wakes = 1, e.blind_wakes + 1
            return
    m.on_heartbeat(e.name, {"fw_version": e.version})
    if e.defect == "sensor":
        e.blind_wakes += 1
    else:
        m.on_telemetry(e.name)


def main() -> None:
    rows = []
    for firmware in ("sain", "crash", "sensor"):
        for strategy in ("big bang", "canary"):
            res = [run(firmware, strategy, seed) for seed in range(RUNS)]
            safe = [r["safe_s"] for r in res if r["safe_s"] is not None]
            rows.append({"firmware": firmware, "strategy": strategy, "runs": RUNS,
                         "exposed_mean": round(sum(r["exposed"] for r in res) / RUNS, 1),
                         "blind_hours_sim_mean": round(sum(r["blind_wakes"] for r in res) / RUNS, 1),
                         "fleet_safe_runs": len(safe),
                         "fleet_safe_s_mean": round(sum(safe) / len(safe)) if safe else "",
                         "fleet_safe_h_sim_mean": round(sim_min(sum(safe) / len(safe)) / 60, 1) if safe else "",
                         "campaign_end": res[0]["campaign"]})
    path = write_csv("exp6_ota.csv", rows)
    print(f"Expérience 6 — OTA sur les 10 ESP32 de fog-b, {RUNS} tirages, horizon {HORIZON_S} s "
          f"({sim_min(HORIZON_S) / 60 / 24:.1f} jours simulés)\n")
    print(f"{'firmware':9} {'stratégie':10} {'ESP32':>6} {'mesures perdues':>16} {'flotte saine après':>22}")
    print(f"{'':9} {'':10} {'exposés':>6} {'(h de parcelle)':>16}")
    for r in rows:
        safe = (f"{r['fleet_safe_s_mean']} s = {r['fleet_safe_h_sim_mean']} h sim"
                if r["fleet_safe_runs"] == RUNS else "jamais (reste aveugle)")
        print(f"{r['firmware']:9} {r['strategy']:10} {r['exposed_mean']:6.1f} {r['blind_hours_sim_mean']:16.1f} {safe:>22}")
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
