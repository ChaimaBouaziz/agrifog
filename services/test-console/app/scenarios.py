"""Les scénarios de test : ce qu'on provoque, et ce que le système doit faire en réponse.

Chaque scénario décrit, en français :
  - `steps`  : les actions à faire (messages MQTT, appels HTTP), calculées au lancement ;
  - `check`  : la réaction attendue, vérifiée sur chaque message MQTT reçu (et sur le dernier
               message connu de chaque topic, au lancement) ; elle renvoie le détail de la réussite,
               ou "ECHEC: …" si le système fait le contraire de ce qui est attendu ;
  - `cleanup`: ce qu'on remet en état après (réparer la panne injectée…).
Fonctions pures : testées sans broker (tests/test_scenarios.py).
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from agrifog_common import topics

# une action : ("mqtt", topic, payload, retain) | ("http", méthode, url, json)
Action = tuple


class NotTestable(Exception):
    """La situation actuelle ne permet pas ce test (sol trop humide, service absent…)."""


@dataclass
class Ctx:
    fog: str
    neighbor: str
    cfg: dict
    peer_twin_url: str
    ota_url: str
    last: dict = field(default_factory=dict)        # dernier message reçu par topic
    http: Callable[[str, str, dict | None], dict] | None = None
    memo: dict = field(default_factory=dict)        # état propre à un lancement

    def zone(self, i: int) -> dict:
        zones = [z for z in self.cfg["zones"] if z.get("simulated", True)]
        return zones[min(i, len(zones) - 1)]

    def fault(self, **f) -> Action:
        return ("mqtt", topics.sim_fault(self.fog), f, False)

    def water(self, zone: str, seconds: int) -> Action:
        cid = f"{self.fog}-{zone}-console-{int(time.time())}"
        self.memo.setdefault("cmd_ids", []).append(cid)
        return ("mqtt", topics.cmd(self.fog, zone), {"id": cid, "action": "water", "seconds": seconds}, False)


@dataclass
class Scenario:
    id: str
    group: str
    title: str
    does: str                       # ce que la console provoque
    expects: str                    # ce que le système doit faire
    steps: Callable[[Ctx], list]
    check: Callable[[Ctx, str, dict], str | None] | None
    timeout_s: float = 60
    cleanup: Callable[[Ctx], list] | None = None
    needs: str = ""                 # "voisin" | "ota" : service requis, affiché à l'écran


def _alert(ctx: Ctx, topic: str, msg: dict, kind: str, zone: str | None = None, device: str | None = None):
    if topic != topics.alert(ctx.fog) or msg.get("type") != kind or msg.get("resolved"):
        return None
    if zone and msg.get("zone") != zone:
        return None
    if device and msg.get("device") != device:
        return None
    return f"alerte « {kind} » : {msg.get('msg', '')}"


# ---------------------------------------------------------------- décision et météo
def _rain(ctx):
    return [ctx.fault(type="rain_forecast", mm=20, hours=12)]


def _rain_check(ctx, topic, msg):
    if topic == topics.weather_forecast(ctx.fog) and float(msg.get("rain_next_24h_mm") or 0) >= 15:
        return f"prévision reçue : {msg['rain_next_24h_mm']} mm dans les 24 h"
    return None


def _manual(ctx):
    return [ctx.water(ctx.zone(0)["id"], 600)]


def _valve_ack(ctx, topic, msg):
    z = ctx.zone(0)["id"]
    if topic == topics.valve(ctx.fog, z) and msg.get("id") in ctx.memo.get("cmd_ids", []):
        return f"vanne {msg.get('device')} ouverte jusqu'à {str(msg.get('until'))[11:16]} (heure simulée)"
    return None


def _decision_check(ctx, topic, msg):
    if topic.startswith(f"{ctx.fog}/zone/") and topic.endswith("/decision") and msg.get("reason"):
        return f"{topic.split('/')[2]} → {msg.get('action')} : {str(msg['reason']).split(' | IA')[0]}"
    return None


# ---------------------------------------------------------------- anomalies
def _leak(ctx):
    return [ctx.fault(type="leak", zone=ctx.zone(1)["id"], lpm=6)]


def _pump(ctx):
    z = ctx.zone(0)["id"]
    return [ctx.fault(type="pump_fail", zone=z), ctx.water(z, 1800)]


def _sensor(ctx):
    z = ctx.zone(0)
    fc = ctx.cfg["soil"]["field_capacity_pct"]
    last = ctx.last.get(topics.telemetry(ctx.fog, z["id"])) or {}
    moisture = last.get("soil_moisture")
    if moisture is None or moisture > fc - 3:
        raise NotTestable(f"sol de {z['id']} trop humide ({moisture if moisture is not None else '?'} % pour une "
                          f"capacité au champ de {fc} %) : l'eau drainerait au lieu de faire monter l'humidité. "
                          f"Relancez quand le sol est plus sec, ou démarrez le simulateur avec --moisture {fc - 6:.0f}")
    dev = z["devices"][1]
    ctx.memo["device"] = dev
    return [ctx.fault(type="sensor_stuck", device=dev), ctx.water(z["id"], 7200)]


def _sensor_check(ctx, topic, msg):
    found = _alert(ctx, topic, msg, "sensor", ctx.zone(0)["id"])
    if found and msg.get("device") != ctx.memo.get("device"):
        return f"ECHEC: c'est {msg.get('device')} qui est accusé, alors que {ctx.memo.get('device')} est le capteur figé"
    return found


def _offline(ctx):
    dev = ctx.zone(2)["devices"][-1]
    ctx.memo["device"] = dev
    return [ctx.fault(type="offline", device=dev)]


# ---------------------------------------------------------------- coopération
def _neighbor_alive(ctx, topic, msg):
    if topic == topics.fog_status(ctx.neighbor) and msg.get("alive") and ctx.memo.get("live"):
        return f"{ctx.neighbor} en ligne (version {msg.get('version', '?')}, CPU {msg.get('cpu', '?')} %)"
    return None


def _quota_check(ctx, topic, msg):
    a = ctx.last.get(topics.quota_allocation(ctx.fog))
    b = ctx.last.get(topics.quota_allocation(ctx.neighbor))
    if not a or not b or a.get("day") != b.get("day"):
        return None
    if a.get("allocation") == b.get("allocation"):
        parts = ", ".join(f"{f} {v:.0f} L" for f, v in sorted(a["allocation"].items()))
        return f"accord le {a['day']} ({a.get('mode')}) : {parts}"
    return f"ECHEC: désaccord le {a['day']} : {a.get('allocation')} contre {b.get('allocation')}"


def _pump_req(ctx):
    acts = []
    for i in (0, 1):
        z = ctx.zone(i)["id"]
        cid = f"{ctx.fog}-{z}-console-pump-{int(time.time())}"
        acts.append(("mqtt", topics.pump_request(ctx.fog),
                     {"id": cid, "fog": ctx.fog, "zone": z, "seconds": 600, "ts": "console"}, False))
    return acts


def _pump_check(ctx, topic, msg):
    mine, theirs = topics.pump_claim(ctx.fog), topics.pump_claim(ctx.neighbor)
    if topic not in (mine, theirs):
        return None
    me, other = ctx.last.get(mine) or {}, ctx.last.get(theirs) or {}
    if me.get("using") and other.get("using"):
        return f"ECHEC: les deux fogs pompent en même temps ({me.get('zone')} et {other.get('zone')})"
    if me.get("using"):
        ctx.memo["served"] = ctx.memo.get("served", set()) | {me.get("zone")}
        if len(ctx.memo["served"]) >= 2:
            return f"pompe obtenue tour à tour pour {', '.join(sorted(ctx.memo['served']))}, sans chevauchement"
    return None


def _twins_check(ctx, topic, msg):
    a, b = ctx.last.get(topics.twin_status(ctx.fog)), ctx.last.get(topics.twin_status(ctx.neighbor))
    if a and b and a.get("state") == "normal" and b.get("state") == "normal":
        return "les deux jumeaux sont « normal » : chacun voit l'autre et le témoin cloud"
    return None


def _maintenance(ctx):
    return [("http", "POST", f"{ctx.peer_twin_url}/takeover", {"from": ctx.fog, "action": "request"})]


def _maintenance_check(ctx, topic, msg):
    if topic == topics.twin_status(ctx.neighbor) and msg.get("takeover") == ctx.fog and ctx.memo.get("live"):
        return f"{ctx.neighbor} pilote maintenant la parcelle de {ctx.fog} ({msg.get('state')})"
    return None


def _maintenance_end(ctx):
    return [("http", "POST", f"{ctx.peer_twin_url}/takeover", {"from": ctx.fog, "action": "release"})]


# ---------------------------------------------------------------- OTA
def _next_version(releases: list[dict]) -> str:
    def key(v):
        return tuple(int(p) for p in v.split("."))
    vs = sorted((r["version"] for r in releases), key=key)
    major, minor, patch = key(vs[-1]) if vs else (0, 3, 0)
    return f"{major}.{minor}.{patch + 1}"


def _ota(kind):
    def steps(ctx):
        devices = ctx.http("GET", f"{ctx.ota_url}/devices", None)
        versions = devices.get("versions") or {}
        base = max(versions, key=versions.get) if versions else "0.3.0"
        version = _next_version(ctx.http("GET", f"{ctx.ota_url}/releases", None))
        ctx.memo["version"] = version
        return [("http", "POST", f"{ctx.ota_url}/releases/defect", {"base": base, "version": version, "kind": kind}),
                ("http", "POST", f"{ctx.ota_url}/campaigns", {"version": version, "observe_s": 60})]
    return steps


def _ota_check(ctx, topic, msg):
    if topic != topics.ota_campaign(ctx.fog) or msg.get("version") != ctx.memo.get("version"):
        return None
    if msg.get("state") == "rolled_back":
        return f"version {msg['version']} bloquée, canary {msg.get('canary')} revenu : {msg.get('detail', '')}"
    if msg.get("state") == "done":
        return f"ECHEC: le firmware défectueux {msg['version']} a été déployé sur toute la flotte"
    return None


def catalog() -> list[Scenario]:
    return [
        Scenario("pluie", "Décision et météo", "Pluie annoncée",
                 "force une prévision de 20 mm de pluie pour les 12 prochaines heures",
                 "le simulateur publie la nouvelle prévision ; à la prochaine heure fraîche, une zone qui aurait arrosé reporte l'arrosage",
                 _rain, _rain_check, 30),
        Scenario("manuel", "Décision et météo", "Arrosage manuel",
                 "envoie un ordre d'arrosage de 10 min à la première zone (comme /arroser sur Telegram)",
                 "la vanne exécute l'ordre à son prochain réveil et confirme",
                 _manual, _valve_ack, 45),
        Scenario("decision", "Décision et météo", "Décision expliquée",
                 "rien : on écoute le moteur",
                 "le moteur publie une décision avec sa raison lisible (dès qu'une situation change)",
                 lambda ctx: [], _decision_check, 240),
        Scenario("fuite", "Anomalies", "Fuite d'eau",
                 "ouvre une fuite de 6 L/min sur la deuxième zone, vanne fermée",
                 "irrigation-engine lève l'alerte « leak » sur cette zone",
                 _leak, lambda c, t, m: _alert(c, t, m, "leak", c.zone(1)["id"]), 90,
                 cleanup=lambda c: [c.fault(type="leak", zone=c.zone(1)["id"], on=False)]),
        Scenario("pompe", "Anomalies", "Pompe en panne",
                 "casse la pompe de la première zone, puis demande un arrosage de 30 min",
                 "la vanne s'ouvre mais rien ne coule : alerte « pump »",
                 _pump, lambda c, t, m: _alert(c, t, m, "pump", c.zone(0)["id"]), 120,
                 cleanup=lambda c: [c.fault(type="pump_fail", zone=c.zone(0)["id"], on=False)]),
        Scenario("capteur", "Anomalies", "Capteur défectueux",
                 "fige le deuxième capteur de la première zone, puis arrose 2 h",
                 "les autres capteurs montent, pas celui-ci : alerte « sensor » sur CE capteur",
                 _sensor, lambda c, t, m: _sensor_check(c, t, m), 150,
                 cleanup=lambda c: [c.fault(type="sensor_stuck", device=c.memo.get("device"), on=False)]),
        Scenario("muet", "Anomalies", "Capteur muet",
                 "empêche le dernier ESP32 de la troisième zone de se réveiller",
                 "après ~3 réveils manqués (30 s), alerte « offline »",
                 _offline, lambda c, t, m: _alert(c, t, m, "offline", device=c.memo.get("device")), 120,
                 cleanup=lambda c: [c.fault(type="offline", device=c.memo.get("device"), on=False)]),
        Scenario("reparer", "Anomalies", "Tout réparer",
                 "répare toutes les pannes injectées", "plus aucune panne dans le simulateur",
                 lambda c: [c.fault(type="clear")], None, 5),
        Scenario("voisin", "Coopération", "Le voisin est en ligne",
                 "rien : on écoute le pont MQTT",
                 "le signe de vie du voisin arrive par le pont (toutes les 10 s)",
                 lambda ctx: [], _neighbor_alive, 25, needs="voisin"),
        Scenario("quota", "Coopération", "Accord sur le quota",
                 "rien : on compare les deux répartitions du jour",
                 "les deux fogs ont calculé exactement la même répartition, chacun de son côté",
                 lambda ctx: [], _quota_check, 300, needs="voisin"),
        Scenario("jumeaux", "Coopération", "Jumeaux en bon état",
                 "rien : on lit l'état des deux fog-twin",
                 "les deux sont « normal »",
                 lambda ctx: [], _twins_check, 30, needs="voisin"),
        Scenario("pompe_commune", "Coopération", "Pompe commune",
                 "demande la pompe pour deux zones à la fois",
                 "pump-scheduler les sert l'une après l'autre ; jamais en même temps que le voisin",
                 _pump_req, _pump_check, 90, needs="voisin"),
        Scenario("maintenance", "Coopération", "Maintenance : le voisin reprend la parcelle",
                 "demande au fog-twin du voisin de reprendre notre parcelle, puis la récupère",
                 "le voisin annonce qu'il pilote notre parcelle (état « reprise »)",
                 _maintenance, _maintenance_check, 30, cleanup=_maintenance_end, needs="voisin"),
        Scenario("ota_crash", "Mises à jour OTA", "Firmware qui plante au démarrage",
                 "fabrique une version défectueuse (crash) et lance une campagne canary",
                 "le canary plante, son bootloader revient seul en arrière : campagne « rolled_back », version bloquée",
                 _ota("crash"), _ota_check, 150, needs="ota"),
        Scenario("ota_sensor", "Mises à jour OTA", "Firmware qui ne mesure plus",
                 "fabrique une version qui tourne sans mesurer et lance une campagne canary",
                 "ota-manager voit un canary vivant mais muet : il renvoie l'ancienne version (« rolled_back »)",
                 _ota("sensor"), _ota_check, 180, needs="ota"),
    ]


@dataclass
class Run:
    scenario: Scenario
    ctx: Ctx
    started: float
    status: str = "en cours"         # en cours | réussi | échoué
    detail: str = ""
    elapsed_s: float | None = None
    log: list = field(default_factory=list)


class Runner:
    """Lance les scénarios et juge leurs résultats au fil des messages."""

    def __init__(self, make_ctx: Callable[[], Ctx], execute: Callable[[Action], str],
                 clock: Callable[[], float] = time.time):
        self.scenarios = {s.id: s for s in catalog()}
        self.make_ctx, self.execute, self.clock = make_ctx, execute, clock
        self.runs: dict[str, Run] = {}
        self.last: dict[str, dict] = {}
        self.lock = threading.RLock()      # messages MQTT et clics de la page arrivent en parallèle

    def start(self, sid: str) -> Run:
        with self.lock:
            return self._start(sid)

    def _start(self, sid: str) -> Run:
        s = self.scenarios[sid]
        ctx = self.make_ctx()
        ctx.last, ctx.memo = self.last, {}
        run = Run(s, ctx, self.clock())
        self.runs[sid] = run
        try:
            for action in s.steps(ctx):
                run.log.append(self.execute(action))
        except NotTestable as e:
            run.status, run.detail, run.elapsed_s = "non testable", str(e), 0.0
            return run
        except Exception as e:  # noqa: BLE001 — un service absent ne doit pas tuer la console
            return self._finish(run, "échoué", f"action impossible : {e}")
        ctx.memo["live"] = True
        if s.check is None:
            return self._finish(run, "réussi", "action envoyée")
        for topic, msg in list(self.last.items()):    # l'état déjà connu suffit peut-être
            if run.status == "en cours":
                self._judge(run, topic, msg, live=False)
        return run

    def on_message(self, topic: str, msg: dict, retained: bool = False) -> None:
        with self.lock:
            self._on_message(topic, msg, retained)

    def _on_message(self, topic: str, msg: dict, retained: bool) -> None:
        self.last[topic] = msg
        for run in list(self.runs.values()):
            if run.status == "en cours":
                self._judge(run, topic, msg, live=not retained)

    def tick(self) -> None:
        with self.lock:
            self._tick()

    def _tick(self) -> None:
        now = self.clock()
        for run in list(self.runs.values()):
            if run.status == "en cours" and now - run.started > run.scenario.timeout_s:
                self._finish(run, "échoué", f"aucune réaction attendue en {run.scenario.timeout_s:.0f} s")

    def _judge(self, run: Run, topic: str, msg: dict, live: bool) -> None:
        run.ctx.memo["live"] = live
        result = run.scenario.check(run.ctx, topic, msg)
        if result:
            if result.startswith("ECHEC:"):
                self._finish(run, "échoué", result[6:].strip())
            else:
                self._finish(run, "réussi", result)

    def _finish(self, run: Run, status: str, detail: str) -> Run:
        run.status, run.detail = status, detail
        run.elapsed_s = round(self.clock() - run.started, 1)
        if run.scenario.cleanup:
            try:
                for action in run.scenario.cleanup(run.ctx):
                    run.log.append(self.execute(action))
            except Exception as e:  # noqa: BLE001
                run.log.append(f"remise en état impossible : {e}")
        return run

    def snapshot(self) -> list[dict]:
        with self.lock:
            return self._snapshot()

    def _snapshot(self) -> list[dict]:
        out = []
        for s in self.scenarios.values():
            r = self.runs.get(s.id)
            out.append({"id": s.id, "group": s.group, "title": s.title, "does": s.does, "expects": s.expects,
                        "timeout_s": s.timeout_s, "needs": s.needs,
                        "status": r.status if r else "jamais lancé", "detail": r.detail if r else "",
                        "elapsed_s": (r.elapsed_s if r and r.elapsed_s is not None
                                      else round(self.clock() - r.started, 1) if r else None),
                        "log": r.log if r else []})
        return out
