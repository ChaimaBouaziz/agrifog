"""Commandes du fog jumeau (depuis services/fog-twin, avec FOG_ID réglé).

    python -m app.cli status          # état de NOTRE jumeau (normal, partition, reprise…)
    python -m app.cli maintenance     # maintenance prévue : demander au voisin de reprendre notre parcelle
    python -m app.cli back            # fin de maintenance : récupérer notre parcelle (et ses litres)
"""

import argparse
import json
import os

import httpx

from agrifog_common import fog_id, load_config


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["status", "maintenance", "back"])
    ap.add_argument("--local", default=os.environ.get("TWIN_URL", "http://127.0.0.1:8080"))
    a = ap.parse_args()
    fog = fog_id()
    peer = os.environ.get("TWIN_PEER_URL", f"http://{load_config()['neighbor']}:8080").rstrip("/")
    if a.cmd == "status":
        r = httpx.get(f"{a.local}/twin", timeout=5)
    else:
        action = "request" if a.cmd == "maintenance" else "release"
        r = httpx.post(f"{peer}/takeover", json={"from": fog, "action": action}, timeout=10)
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
