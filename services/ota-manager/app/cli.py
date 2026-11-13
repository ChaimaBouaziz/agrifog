"""Petit client de l'API ota-manager (évite les guillemets JSON dans PowerShell).

    python -m app.cli release fw-v0.3.0              # ajoute la release GitHub au catalogue du fog
    python -m app.cli deploy 0.3.0 esp-b-01          # un ESP32 (ou plusieurs, ou "all")
    python -m app.cli devices                        # versions installées + dernier statut OTA
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("OTA_API", "http://127.0.0.1:8003")


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"erreur {e.code} : {json.loads(e.read()).get('detail', '')}")
    except urllib.error.URLError as e:
        sys.exit(f"ota-manager injoignable sur {API} ({e.reason}) : est-il lancé ?")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("release", help="ajouter une release GitHub (tag fw-vX.Y.Z)")
    r.add_argument("tag")
    d = sub.add_parser("deploy", help="envoyer une version à des ESP32")
    d.add_argument("version")
    d.add_argument("devices", nargs="+")
    sub.add_parser("devices", help="état des ESP32")
    sub.add_parser("releases", help="catalogue du fog")
    a = ap.parse_args()

    if a.cmd == "release":
        rel = call("POST", "/releases", {"github_tag": a.tag})
        print(f"version {rel['version']} ajoutée : {rel['size']} octets, SHA-256 {rel['sha256'][:16]}… vérifié")
    elif a.cmd == "deploy":
        res = call("POST", "/deploy", {"version": a.version, "devices": "all" if a.devices == ["all"] else a.devices})
        for o in res["orders"]:
            print(f"ordre {o['id']} -> {o['url']}")
    elif a.cmd == "releases":
        for rel in call("GET", "/releases"):
            print(f"{rel['version']:>8}  {rel['size']:>8} o  {rel['sha256'][:16]}…  {rel['source']}")
    else:
        snap = call("GET", "/devices")
        print(f"{snap['fog']} : versions {snap['versions']}")
        for dev, s in snap["devices"].items():
            ota = s["ota"] or {}
            print(f"  {dev:<12} v{s['version'] or '?':<7} {ota.get('state', ''):<12} {ota.get('detail') or ''}")


if __name__ == "__main__":
    main()
