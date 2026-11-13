"""OTA campaigns of one fog.

v1 (semaine 4) : deploy(version, devices) -> ordre OTA, suivi des statuts et des versions.
v2 (semaine 5) : campagne CANARY + ROLLBACK.

    start_campaign("0.3.1")
      1. canary   : la version part sur UN seul ESP32
      2. observe  : pendant observe_s, il doit être installé, envoyer des heartbeats ET des mesures
      3a. sain    -> rollout : la version part sur le reste de la flotte -> done
      3b. malade  -> la version est BLOQUÉE (plus jamais déployée) ; si le canary tourne encore
                     dessus, ota-manager lui renvoie l'ancienne version (rollback) -> rolled_back
                     (s'il a planté au démarrage, son bootloader est déjà revenu tout seul)
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from agrifog_common import topics

from .catalog import Catalog

Publish = Callable[[str, dict], None]
ACTIVE = ("canary", "rollout")


class OtaManager:
    def __init__(self, cfg: dict, fog: str, catalog: Catalog, base_url: str,
                 publish: Publish = lambda t, p: None, clock: Callable[[], float] = time.time):
        self.fog, self.catalog, self.base_url = fog, catalog, base_url.rstrip("/")
        self.publish, self.clock = publish, clock
        self.lock = threading.RLock()
        self.known = [d for z in cfg.get("zones", []) for d in z.get("devices", [])]
        # ESP32 réels (Wokwi) : ils ne voient pas le fog -> ils téléchargent depuis la source publique
        self.public = {d for z in cfg.get("zones", []) if z.get("simulated") is False for d in z.get("devices", [])}
        self.period_s = float(cfg.get("simulation", {}).get("wake_period_s", 10))
        self.devices: dict[str, dict] = {}
        self.campaign: dict | None = None

    def _dev(self, device: str) -> dict:
        return self.devices.setdefault(device, {"version": None, "last_seen": None, "last_telemetry": None,
                                                "installed_at": None, "ota": None})

    # ------------------------------------------------------------------ v1 : ordre direct
    def deploy(self, version: str, devices: list[str] | str, force: bool = False) -> list[dict]:
        rel = self.catalog.get(version)                     # KeyError si la version n'existe pas
        if rel.blocked and not force:
            raise ValueError(f"version {version} bloquée : {rel.blocked}")
        targets = self.known if devices == "all" or devices == ["all"] else list(devices)
        unknown = [d for d in targets if d not in self.known]
        if unknown:
            raise ValueError(f"ESP32 inconnus de {self.fog} : {', '.join(unknown)}")
        orders = []
        for dev in targets:
            if dev in self.public and not rel.url:
                continue                                    # version locale (démo) : pas d'URL publique
            url = rel.url if dev in self.public else f"{self.base_url}/firmware/{version}.bin"
            order = {"id": f"ota-{version}-{dev}-{int(self.clock())}", "version": version,
                     "url": url, "sha256": rel.sha256, "size": rel.size}
            self.publish(topics.ota(self.fog, dev), order)
            with self.lock:
                d = self._dev(dev)
                d["ota"] = {"id": order["id"], "version": version, "state": "sent", "detail": ""}
                d["installed_at"] = None
            orders.append(order)
        return orders

    # ------------------------------------------------------------------ v2 : canary + rollback
    def start_campaign(self, version: str, canary: str | None = None, observe_s: float = 60) -> dict:
        with self.lock:
            if self.campaign and self.campaign["state"] in ACTIVE:
                raise ValueError(f"une campagne est déjà en cours ({self.campaign['version']})")
            canary = canary or next(d for d in self.known if d not in self.public)
            previous = self._dev(canary)["version"]
            self.deploy(version, [canary])                  # refuse une version bloquée
            now = self.clock()
            self.campaign = {"version": version, "canary": canary, "previous": previous,
                             "observe_s": observe_s, "started": now, "state": "canary", "detail": "",
                             "events": []}
            self._event(f"canary : {version} envoyée à {canary} (version actuelle {previous})")
            return self.status()

    def _event(self, text: str, state: str | None = None) -> None:
        c = self.campaign
        if state:
            c["state"] = state
            c["detail"] = text
        c["events"].append({"t": round(self.clock() - c["started"], 1), "text": text})
        print(f"[campagne {c['version']}] {text}")
        self.publish(topics.ota_campaign(self.fog), {k: c[k] for k in ("version", "canary", "state", "detail")})

    def _fail(self, reason: str) -> None:
        c = self.campaign
        self.catalog.block(c["version"], reason)
        self._event(f"canary malade : {reason} -> version {c['version']} bloquée, déploiement arrêté")
        dev = self._dev(c["canary"])
        if dev["version"] == c["version"] and c["previous"]:
            try:
                self.deploy(c["previous"], [c["canary"]], force=True)
            except KeyError:
                return self._event(f"rollback impossible : {c['previous']} absente du catalogue", "aborted")
            self._event(f"rollback : {c['previous']} renvoyée à {c['canary']}", "rolled_back")
        elif dev["version"] == c["previous"]:
            self._event(f"{c['canary']} est déjà revenu sur {c['previous']} (rollback de l'ESP32)", "rolled_back")
        else:
            self._event("campagne arrêtée", "aborted")

    def tick(self) -> None:
        """Called every few seconds: move the campaign forward."""
        with self.lock:
            c = self.campaign
            if not c or c["state"] not in ACTIVE:
                return
            now, v = self.clock(), c["version"]
            if c["state"] == "canary":
                dev = self._dev(c["canary"])
                ota = dev["ota"] or {}
                if ota.get("version") == v and ota.get("state") in ("failed", "rolled_back"):
                    return self._fail(f"{ota['state']} ({ota.get('detail', '')})")
                t0 = dev["installed_at"]
                if t0 is None:
                    if now - c["started"] > max(c["observe_s"], 6 * self.period_s):
                        return self._fail("pas installée (canary muet)")
                    return
                # 5 réveils sans nouvelles : laisse au bootloader le temps de revenir seul (3 démarrages)
                silent = dev["last_seen"] is None or now - dev["last_seen"] > 5 * self.period_s
                measures = dev["last_telemetry"] is not None and dev["last_telemetry"] > t0
                if silent and now - t0 > 5 * self.period_s:
                    return self._fail(f"plus de heartbeat depuis {now - (dev['last_seen'] or t0):.0f} s")
                if now - t0 >= c["observe_s"]:
                    if not measures:
                        return self._fail("plus aucune mesure depuis l'installation")
                    rest = [d for d in self.known if d != c["canary"]]
                    sent = self.deploy(v, rest)
                    self._event(f"canary sain pendant {c['observe_s']:.0f} s -> déploiement sur {len(sent)} ESP32",
                                "rollout")
            elif c["state"] == "rollout":
                todo = [d for d in self.known if d not in self.public and self._dev(d)["version"] != v]
                if not todo:
                    self._event(f"toute la flotte est en {v}", "done")

    def status(self) -> dict | None:
        with self.lock:
            return dict(self.campaign) if self.campaign else None

    # ------------------------------------------------------------------ retours des ESP32
    def on_heartbeat(self, device: str, msg: dict) -> None:
        with self.lock:
            d = self._dev(device)
            d["version"], d["last_seen"] = msg.get("fw_version"), self.clock()

    def on_telemetry(self, device: str) -> None:
        with self.lock:
            self._dev(device)["last_telemetry"] = self.clock()

    def on_status(self, device: str, msg: dict) -> None:
        with self.lock:
            d = self._dev(device)
            d["ota"] = {k: msg.get(k) for k in ("id", "version", "from", "state", "detail")}
            if msg.get("state") == "installed":
                d["version"], d["installed_at"] = msg.get("version"), self.clock()
            elif msg.get("state") == "rolled_back":
                d["version"] = msg.get("running") or msg.get("from")

    def snapshot(self) -> dict:
        with self.lock:
            devs = {d: {k: v for k, v in self._dev(d).items() if k in ("version", "ota")}
                    for d in sorted(set(self.known) | set(self.devices))}
            campaign = self.status()
        versions: dict[str, int] = {}
        for d in devs.values():
            if d["version"]:
                versions[d["version"]] = versions.get(d["version"], 0) + 1
        return {"fog": self.fog, "versions": versions, "devices": devs, "campaign": campaign}
