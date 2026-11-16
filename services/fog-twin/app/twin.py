"""Le fog jumeau : sauvegarde mutuelle, surveillance du voisin, reprise de sa parcelle.

Trois liens :
  - broker local (MQTT)   : statut du voisin domaine/fog/<voisin>/status (via le pont) ; on y publie
                            notre état domaine/twin/<fog> ; le moteur de reprise y pilote les ESP32 du voisin
  - témoin cloud (MQTT)   : <prefix>/witness/<fog> sur test.mosquitto.org, toutes les 5 s, par les deux
                            fogs : départage « voisin en panne » / « lien entre fogs coupé »
  - voisin (HTTP)         : POST /backup (sauvegarde toutes les 10 s), POST /takeover (rendre la parcelle)
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

import httpx
import paho.mqtt.client as mqtt

from agrifog_common import topics
from agrifog_common.config import REPO_ROOT

from .brain import View, assess
from .takeover import Takeover

BACKUP_PERIOD_S = 10
WITNESS_PERIOD_S = 5
STATUS_PERIOD_S = 10
RELEASE_RETRY_S = 10
RELEASE_TRIES = 12          # 2 minutes pour retrouver le voisin au démarrage


class BackupStore:
    """Dernière sauvegarde reçue de chaque fog, gardée sur disque (survit à un redémarrage)."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, fog: str, backup: dict) -> None:
        tmp = self.dir / f"{fog}.json.tmp"
        tmp.write_text(json.dumps(backup), encoding="utf-8")
        tmp.replace(self.dir / f"{fog}.json")           # écriture atomique

    def get(self, fog: str) -> dict | None:
        path = self.dir / f"{fog}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


class FogTwin:
    def __init__(self, cfg: dict, fog: str, clock: Callable[[], float] = time.time,
                 http: httpx.Client | None = None, mqtt_enabled: bool = True):
        self.cfg, self.fog, self.neighbor, self.clock = cfg, fog, cfg["neighbor"], clock
        tw = cfg.get("twin", {})
        self.dead_after = float(tw.get("takeover_after_s", 30))
        self.witness_host = os.environ.get("TWIN_WITNESS", tw.get("witness_broker", ""))
        self.witness_prefix = tw.get("witness_prefix", "agrifog")
        self.peer_url = os.environ.get("TWIN_PEER_URL", f"http://{self.neighbor}:8080").rstrip("/")
        self.engine_url = os.environ.get("ENGINE_URL", "http://127.0.0.1:8001").rstrip("/")
        self.store = BackupStore(os.environ.get("TWIN_DATA_DIR", Path(REPO_ROOT) / "data" / "twin"))
        self.http = http or httpx.Client(timeout=3)
        self.mqtt_enabled = mqtt_enabled
        self.local: mqtt.Client | None = None
        self.local_ok = False
        self.neighbor_last: float | None = None
        self.neighbor_alive = True
        self.witness_self_last: float | None = None
        self.neighbor_witness_last: float | None = None
        self.maintenance = False
        self.takeover: Takeover | None = None
        self.handover: dict | None = None       # litres utilisés pendant la dernière reprise, à rendre
        self.state, self.reason = "démarrage", ""
        self.backup_ok: bool | None = None
        self.restored: dict | None = None
        self.lock = threading.RLock()

    # ------------------------------------------------------------- entrées
    def on_neighbor_status(self, msg: dict, retained: bool = False) -> None:
        with self.lock:
            if retained:                  # un vieux statut retained ne prouve pas que le voisin vit
                return
            self.neighbor_alive = bool(msg.get("alive", True))
            if self.neighbor_alive:
                self.neighbor_last = self.clock()

    def on_witness(self, fog: str, msg: dict) -> None:
        with self.lock:
            if fog == self.fog:
                self.witness_self_last = self.clock()
            elif fog == self.neighbor and msg.get("local_ok", True):
                self.neighbor_witness_last = self.clock()

    def on_takeover_call(self, origin: str, action: str) -> dict:
        """POST /takeover du voisin : 'request' (maintenance) ou 'release' (je suis revenu)."""
        with self.lock:
            if origin != self.neighbor:
                raise ValueError(f"{origin} n'est pas notre voisin ({self.neighbor})")
            if action == "request":
                self.maintenance = True
                self.step()
                return {"accepted": True, "state": self.state}
            if action == "release":
                self.maintenance = False
                if self.takeover:
                    self._stop_takeover(f"{self.neighbor} est revenu et redemande sa parcelle")
                hand, self.handover = self.handover, None
                self.step()
                return {"released": hand is not None, "used_l": hand["used_l"] if hand else None}
            raise ValueError("action = request ou release")

    # ------------------------------------------------------------- décision
    def view(self) -> View:
        now = self.clock()
        return View(now=now, neighbor=self.neighbor, neighbor_last=self.neighbor_last,
                    neighbor_alive=self.neighbor_alive, witness_enabled=bool(self.witness_host),
                    witness_ok=self.witness_self_last is not None and now - self.witness_self_last <= self.dead_after,
                    neighbor_witness_last=self.neighbor_witness_last, maintenance=self.maintenance,
                    dead_after=self.dead_after)

    def step(self) -> None:
        with self.lock:
            state, reason = assess(self.view())
            if state == "reprise" and not self.takeover:
                self._start_takeover(reason)
            elif state in ("normal", "partition", "voisin-inconnu") and self.takeover:
                self._stop_takeover(reason)
            # "isole" pendant une reprise : on continue (le voisin était en panne, rien ne dit qu'il est revenu)
            changed = state != self.state
            if changed:
                print(f"[twin] {state:14} | {reason}")
            self.state, self.reason = state, reason
            if changed:
                self.publish_status()

    def _start_takeover(self, reason: str) -> None:
        backup = self.store.get(self.neighbor)
        mq = self.cfg["mqtt"]
        self.takeover = Takeover(self.neighbor, mq["host"], mq["port"], backup, self.mqtt_enabled)
        age = f"sauvegarde d'il y a {self.clock() - backup['unix']:.0f} s" if backup else "aucune sauvegarde"
        print(f"[twin] ▶ REPRISE de la parcelle {self.neighbor} ({age}) : {reason}")
        self._alert(f"reprise de la parcelle {self.neighbor} : {reason}", resolved=False)

    def _stop_takeover(self, reason: str) -> None:
        self.handover = self.takeover.stop()
        self.takeover = None
        print(f"[twin] ■ fin de reprise de {self.neighbor} : {reason} "
              f"(litres utilisés : {self.handover['used_l']})")
        self._alert(f"fin de reprise de la parcelle {self.neighbor} : {reason}", resolved=True)

    # ------------------------------------------------------------- sauvegarde / retour
    def backup_once(self) -> None:
        """Envoie l'état de NOTRE irrigation-engine au voisin."""
        try:
            data = self.http.get(f"{self.engine_url}/zones").json()
            payload = {"fog": self.fog, "unix": round(self.clock(), 1), "data": data}
            self.http.post(f"{self.peer_url}/backup", json=payload).raise_for_status()
            ok, err = True, ""
        except (httpx.HTTPError, ValueError) as e:
            ok, err = False, str(e) or type(e).__name__
        if ok != self.backup_ok:
            print(f"[twin] sauvegarde vers {self.peer_url} : " + ("OK" if ok else f"impossible ({err})"))
        self.backup_ok = ok

    def reclaim(self) -> bool:
        """Au démarrage : « je suis revenu ». Récupère les litres arrosés par le voisin pendant la reprise."""
        try:
            r = self.http.post(f"{self.peer_url}/takeover", json={"from": self.fog, "action": "release"})
            r.raise_for_status()
            answer = r.json()
        except (httpx.HTTPError, ValueError):
            return False
        self.restored = answer
        used = answer.get("used_l") or {}
        if used:
            day = max(used)
            self._publish(topics.quota_usage(self.fog), {"day": day, "used_l": used[day]}, retain=True)
            print(f"[twin] parcelle rendue par {self.neighbor} : {used[day]:.0f} L déjà utilisés le {day}")
        else:
            print(f"[twin] {self.neighbor} joint : pas de reprise en cours, rien à restaurer")
        return True

    # ------------------------------------------------------------- publications
    def publish_status(self) -> None:
        self._publish(topics.twin_status(self.fog), {
            "fog": self.fog, "state": self.state, "reason": self.reason,
            "takeover": self.takeover.target if self.takeover else None, "unix": round(self.clock(), 1)},
            retain=True)

    def _alert(self, msg: str, resolved: bool) -> None:
        self._publish(topics.alert(self.fog), {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                               "type": "takeover", "zone": None, "device": None,
                                               "msg": msg, "resolved": resolved})

    def _publish(self, topic: str, payload: dict, retain: bool = False) -> None:
        if self.local is not None:
            self.local.publish(topic, json.dumps(payload), qos=1, retain=retain)

    def snapshot(self) -> dict:
        with self.lock:
            now = self.clock()
            age = lambda t: None if t is None else round(now - t, 1)
            return {"fog": self.fog, "neighbor": self.neighbor, "state": self.state, "reason": self.reason,
                    "neighbor_status_age_s": age(self.neighbor_last),
                    "witness": {"broker": self.witness_host or None, "self_age_s": age(self.witness_self_last),
                                "neighbor_age_s": age(self.neighbor_witness_last)},
                    "backup_to_neighbor_ok": self.backup_ok,
                    "backup_from_neighbor": (lambda b: b and {"age_s": age(b["unix"])})(self.store.get(self.neighbor)),
                    "maintenance": self.maintenance, "restored": self.restored,
                    "takeover": self.takeover.snapshot() if self.takeover else None,
                    "handover": self.handover}

    # ------------------------------------------------------------- boucles
    def run(self, stop: threading.Event) -> None:
        mq = self.cfg["mqtt"]
        self.local = _connect_local(self, mq["host"], mq["port"])
        if self.witness_host:
            threading.Thread(target=_run_witness, args=(self, stop), daemon=True).start()
        next_backup = next_status = next_release = 0.0
        release_tries = 0
        while not stop.wait(1):
            now = self.clock()
            self.step()
            if now >= next_status:
                self.publish_status()
                next_status = now + STATUS_PERIOD_S
            if now >= next_backup:
                self.backup_once()
                next_backup = now + BACKUP_PERIOD_S
            if release_tries < RELEASE_TRIES and now >= next_release:
                release_tries = RELEASE_TRIES if self.reclaim() else release_tries + 1
                next_release = now + RELEASE_RETRY_S
        with self.lock:
            if self.takeover:
                self._stop_takeover("fog-twin arrêté")
        self._publish(topics.twin_status(self.fog), {"fog": self.fog, "state": "arrêté", "reason": "",
                                                     "takeover": None, "unix": round(self.clock(), 1)}, True)
        self.local.disconnect()
        self.local.loop_stop()


def _connect_local(twin: FogTwin, host: str, port: int) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"fog-twin-{twin.fog}")
    client.will_set(topics.twin_status(twin.fog), json.dumps({"fog": twin.fog, "state": "arrêté",
                                                              "reason": "", "takeover": None}), qos=1, retain=True)
    status_topic = topics.fog_status(twin.neighbor)

    def on_connect(c, u, f, rc, p):
        twin.local_ok = True
        print(f"[mqtt] broker local connecté ({rc}), surveille {status_topic}")
        c.subscribe(status_topic, qos=1)

    def on_disconnect(c, u, f, rc, p):
        twin.local_ok = False

    def on_message(c, u, m):
        try:
            twin.on_neighbor_status(json.loads(m.payload), retained=m.retain)
        except ValueError:
            pass

    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
    while True:
        try:
            client.connect(host, port)
            break
        except OSError as e:
            print(f"[mqtt] broker local {host}:{port} injoignable ({e}), nouvel essai dans 5 s")
            time.sleep(5)
    client.loop_start()
    return client


def _run_witness(twin: FogTwin, stop: threading.Event) -> None:
    """Le témoin cloud : chacun y signe sa présence toutes les 5 s (et vérifie que sa signature revient)."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"agrifog-twin-{twin.fog}-{uuid.uuid4().hex[:6]}")
    base = f"{twin.witness_prefix}/witness"

    def on_connect(c, u, f, rc, p):
        print(f"[témoin] connecté à {twin.witness_host} ({rc})")
        c.subscribe(f"{base}/+", qos=0)

    def on_message(c, u, m):
        try:
            twin.on_witness(m.topic.rsplit("/", 1)[1], json.loads(m.payload))
        except (ValueError, IndexError):
            pass

    client.on_connect, client.on_message = on_connect, on_message
    client.reconnect_delay_set(1, 10)
    host, _, port = twin.witness_host.partition(":")      # "54.36.178.49" ou "hôte:port"
    connected = False
    while not stop.is_set():
        if not connected:
            try:
                client.connect(host, int(port or 1883), keepalive=20)
                client.loop_start()
                connected = True
            except OSError as e:
                print(f"[témoin] {twin.witness_host} injoignable ({e})")
                stop.wait(WITNESS_PERIOD_S)
                continue
        client.publish(f"{base}/{twin.fog}", json.dumps({"fog": twin.fog, "local_ok": twin.local_ok,
                                                        "unix": round(time.time(), 1)}), qos=0)
        stop.wait(WITNESS_PERIOD_S)
    client.disconnect()
    client.loop_stop()
