"""OTA campaigns of one fog (v1: push a release to chosen ESP32s and follow them).

    deploy("0.3.0", ["esp-b-01"])  ->  {fog}/device/esp-b-01/ota   {id, version, url, sha256, size}
    ESP32                         ->  {fog}/device/esp-b-01/ota/status   downloading / installed / failed…
    ESP32 heartbeat               ->  fw_version : the truth about what is really running

Semaine 5 : canary (1 ESP32, puis toute la flotte si ses heartbeats restent bons) + rollback.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from agrifog_common import topics

from .catalog import Catalog

Publish = Callable[[str, dict], None]


class OtaManager:
    def __init__(self, cfg: dict, fog: str, catalog: Catalog, base_url: str,
                 publish: Publish = lambda t, p: None, clock: Callable[[], float] = time.time):
        self.fog, self.catalog, self.base_url = fog, catalog, base_url.rstrip("/")
        self.publish, self.clock = publish, clock
        self.lock = threading.Lock()
        self.known = [d for z in cfg.get("zones", []) for d in z.get("devices", [])]
        # ESP32 réels (Wokwi) : ils ne voient pas le fog -> ils téléchargent depuis la source publique
        self.public = {d for z in cfg.get("zones", []) if z.get("simulated") is False for d in z.get("devices", [])}
        self.devices: dict[str, dict] = {}

    def _dev(self, device: str) -> dict:
        return self.devices.setdefault(device, {"version": None, "last_seen": None, "ota": None})

    # ------------------------------------------------------------------ campaign
    def deploy(self, version: str, devices: list[str] | str) -> list[dict]:
        rel = self.catalog.get(version)                     # KeyError si la version n'existe pas
        targets = self.known if devices == "all" or devices == ["all"] else list(devices)
        unknown = [d for d in targets if d not in self.known]
        if unknown:
            raise ValueError(f"ESP32 inconnus de {self.fog} : {', '.join(unknown)}")
        orders = []
        for dev in targets:
            url = rel.url if dev in self.public else f"{self.base_url}/firmware/{version}.bin"
            order = {"id": f"ota-{version}-{dev}-{int(self.clock())}", "version": version,
                     "url": url, "sha256": rel.sha256, "size": rel.size}
            self.publish(topics.ota(self.fog, dev), order)
            with self.lock:
                self._dev(dev)["ota"] = {"id": order["id"], "version": version, "state": "sent", "detail": ""}
            orders.append(order)
        return orders

    # ------------------------------------------------------------------ feedback
    def on_heartbeat(self, device: str, msg: dict) -> None:
        with self.lock:
            d = self._dev(device)
            d["version"], d["last_seen"] = msg.get("fw_version"), self.clock()

    def on_status(self, device: str, msg: dict) -> None:
        with self.lock:
            self._dev(device)["ota"] = {k: msg.get(k) for k in ("id", "version", "from", "state", "detail")}
            if msg.get("state") == "installed":
                self._dev(device)["version"] = msg.get("version")

    def snapshot(self) -> dict:
        with self.lock:
            devs = {d: dict(self._dev(d)) for d in sorted(set(self.known) | set(self.devices))}
        versions: dict[str, int] = {}
        for d in devs.values():
            if d["version"]:
                versions[d["version"]] = versions.get(d["version"], 0) + 1
        return {"fog": self.fog, "versions": versions, "devices": devs}
