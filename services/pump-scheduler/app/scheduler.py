"""Pompe commune aux deux fogs : exclusion mutuelle sans coordinateur, par messages MQTT retained.

    FOG_ID=fog-a python -m app.scheduler          # sans API

Chaque fog publie SA demande sur domaine/pump/claim/<fog> (personne n'écrit chez l'autre, donc pas
de conflit entre les deux brokers du pont). Les deux fogs appliquent la même règle :

  1. l'autre pompe (using, bail pas expiré)            -> on attend ;
  2. les deux attendent : le premier entré dans la file (since) passe, égalité -> ordre des noms ;
  3. après un arrosage, s'il reste des zones à arroser, on se remet EN FIN de file -> alternance ;
  4. une demande non rafraîchie depuis CLAIM_TTL_S = fog mort -> ignorée.

La pompe est réservée de l'envoi de l'ordre jusqu'à la FIN de l'arrosage : la vanne n'exécute l'ordre
qu'à son prochain réveil (deep sleep), puis confirme sur {fog}/zone/<z>/valve (opened, until).

Avant d'utiliser la pompe on attend SETTLE_S : le temps que la demande de l'autre fog, envoyée au
même moment, traverse le pont. Les durées sont en secondes RÉELLES (l'arrosage, en secondes simulées,
est divisé par la vitesse de simulation) et mesurées avec l'horloge du fog qui les lit : un décalage
d'horloge entre les deux PC ne change rien.

Abonnements :  +/pump/request (le fog local, et le fog voisin quand fog-twin le reprend)
               +/zone/+/valve (accusés des vannes)   domaine/pump/claim/+
Publications : {fog}/zone/<z>/cmd   domaine/pump/claim/<fog>   domaine/pump/token
"""

import json
import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable

import paho.mqtt.client as mqtt

from agrifog_common import fog_id, load_config, topics

SETTLE_S = 1.5          # la demande de l'autre fog a eu le temps d'arriver
REFRESH_S = 2.0         # on republie sa demande (preuve de vie)
CLAIM_TTL_S = 20.0      # demande muette depuis 20 s : fog mort, on l'ignore
MARGIN_S = 0.5          # marge réelle après un arrosage (pont, débit qui retombe)
MAX_WAIT_S = 70.0       # au-delà, irrigation-engine a abandonné l'ordre (8 h simulées) : on le jette
Publish = Callable[[str, dict, bool], None]


def parse(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


class PumpScheduler:
    def __init__(self, fog: str, speed: float, wake_period_s: float = 10.0, publish: Publish | None = None,
                 clock: Callable[[], float] = time.time):
        self.fog, self.speed, self.clock = fog, speed, clock
        self.ack_wait_s = wake_period_s * 1.5 + 1      # la vanne doit avoir confirmé avant
        self.publish: Publish = publish or (lambda topic, payload, retain: None)
        self.queue: deque[dict] = deque()      # arrosages en attente de la pompe
        self.seen_ids: deque[str] = deque(maxlen=200)
        self.others: dict[str, dict] = {}      # demandes des autres fogs (+ heure locale de réception)
        self.mine: dict | None = None          # notre demande en cours
        self.using: dict | None = None         # arrosage en cours : requête + fin du bail
        self.waiting_reason: str | None = None
        self.history: deque[dict] = deque(maxlen=30)
        self.lock = threading.Lock()

    # ------------------------------------------------------------- entrées
    def on_request(self, msg: dict) -> None:
        """Un irrigation-engine veut arroser une zone (QoS 1 : un même id peut arriver deux fois)."""
        with self.lock:
            if msg["id"] in self.seen_ids:
                return
            self.seen_ids.append(msg["id"])
            self.queue.append({**msg, "fog": msg.get("fog", self.fog), "received": self.clock()})
            print(f"[pompe] demande {msg['id']} : {msg.get('fog', self.fog)}/{msg['zone']} "
                  f"{msg['seconds'] // 60} min simulées ({len(self.queue)} en file)")

    def on_valve(self, msg: dict) -> None:
        """La vanne a exécuté notre ordre : la pompe tourne jusqu'à `until` (temps simulé)."""
        with self.lock:
            u = self.using
            if not u or msg.get("id") != u["id"] or u.get("opened"):
                return
            now = self.clock()
            run_s = (parse(msg["until"]) - parse(msg["opened"])) / self.speed
            u["opened"], u["until"] = now, now + run_s + MARGIN_S
            self._claim(now, want=True, since=self.mine["since"])
            print(f"[pompe] vanne {u['fog']}/{u['zone']} ouverte : pompe occupée {run_s:.1f} s")

    def on_claim(self, fog: str, msg: dict, retained: bool = False) -> None:
        if fog == self.fog:
            return
        with self.lock:
            # un message retained peut dater d'avant notre démarrage : il ne compte qu'une fois rafraîchi
            received = 0.0 if retained else self.clock()
            self.others[fog] = {**msg, "received": received}

    # ------------------------------------------------------------- boucle
    def tick(self) -> None:
        now = self.clock()
        with self.lock:
            if self.using:
                if now >= self.using["until"]:
                    self._finish(now)
                else:
                    self._refresh(now)
                    return
            self._drop_stale(now)
            if not self.queue:
                if self.mine:                       # plus rien à arroser : on sort de la file
                    self._claim(now, want=False)
                    self.mine = None
                return
            if self.mine is None or not self.mine["want"]:
                self._claim(now, want=True, since=now)
                return
            self._refresh(now)
            if now - self.mine["since"] < SETTLE_S:
                return
            reason = self._blocker(now)
            if reason:
                if reason != self.waiting_reason:
                    print(f"[pompe] attente : {reason}")
                self.waiting_reason = reason
                return
            self.waiting_reason = None
            self._start(now)

    def _blocker(self, now: float) -> str | None:
        for fog, c in sorted(self.others.items()):
            if now - c["received"] > CLAIM_TTL_S:
                continue                                       # muet : mort ou pont coupé depuis longtemps
            if c.get("using") and c["received"] + c.get("remaining_s", 0) > now:
                return f"pompe utilisée par {fog} ({c.get('zone')})"
            if c.get("want") and not c.get("using") and (c["since"], fog) < (self.mine["since"], self.fog):
                return f"{fog} attend depuis plus longtemps : son tour d'abord"
        return None

    def _start(self, now: float) -> None:
        req = self.queue.popleft()
        # tant que la vanne n'a pas confirmé : réveil de la vanne (au pire) + durée de l'arrosage
        real_s = self.ack_wait_s + req["seconds"] / self.speed + MARGIN_S
        self.using = {**req, "start": now, "until": now + real_s, "opened": None}
        self.publish(topics.cmd(req["fog"], req["zone"]),
                     {"id": req["id"], "action": "water", "seconds": req["seconds"]}, False)
        self._claim(now, want=True, since=self.mine["since"])
        self.publish(topics.PUMP_TOKEN, {"holder": self.fog, "zone": f"{req['fog']}/{req['zone']}",
                                         "seconds": req["seconds"], "until_unix": round(now + real_s, 2),
                                         "ts": req.get("ts")}, True)
        waited = now - req["received"]
        print(f"[pompe] ▶ {req['fog']}/{req['zone']} : pompe à nous, {req['seconds'] // 60} min simulées "
              f"(attente {waited:.1f} s)")

    def _finish(self, now: float) -> None:
        u = self.using
        if not u.get("opened"):
            print(f"[pompe] ⚠ {u['fog']}/{u['zone']} : la vanne n'a pas confirmé l'ordre {u['id']}")
        self.history.append({"fog": self.fog, "zone": f"{u['fog']}/{u['zone']}", "id": u["id"],
                             "start_unix": round(u["start"], 3), "end_unix": round(min(now, u["until"]), 3), "confirmed": bool(u.get("opened")),
                             "waited_s": round(u["start"] - u["received"], 2), "ts": u.get("ts")})
        self.using = None
        self.publish(topics.PUMP_TOKEN, {"holder": None, "zone": None, "seconds": 0,
                                         "until_unix": None, "ts": u.get("ts")}, True)
        print(f"[pompe] ■ {u['fog']}/{u['zone']} terminé, pompe libérée")
        if self.queue:                       # encore des zones : en fin de file, le voisin passe d'abord
            self._claim(now, want=True, since=now)
        else:
            self._claim(now, want=False)
            self.mine = None

    def _drop_stale(self, now: float) -> None:
        while self.queue and now - self.queue[0]["received"] > MAX_WAIT_S:
            req = self.queue.popleft()
            print(f"[pompe] ✗ {req['id']} abandonné : pompe indisponible depuis {MAX_WAIT_S:.0f} s")

    def _claim(self, now: float, want: bool, since: float | None = None) -> None:
        using = self.using is not None
        self.mine = {"fog": self.fog, "want": want, "since": since if since is not None else now,
                     "using": using,
                     "zone": f"{self.using['fog']}/{self.using['zone']}" if using else None,
                     "remaining_s": round(self.using["until"] - now, 2) if using else 0,
                     "queue": len(self.queue), "unix": round(now, 2)}
        self.publish(topics.pump_claim(self.fog), self.mine, True)

    def _refresh(self, now: float) -> None:
        if self.mine and now - self.mine["unix"] >= REFRESH_S:
            self._claim(now, want=self.mine["want"], since=self.mine["since"])

    # ------------------------------------------------------------- API
    def snapshot(self) -> dict:
        now = self.clock()
        with self.lock:
            return {"fog": self.fog,
                    "pump": ({"holder": self.fog, "zone": f"{self.using['fog']}/{self.using['zone']}",
                              "remaining_s": round(self.using["until"] - now, 1)} if self.using
                             else {"holder": self._other_user(now)}),
                    "queue": [{"id": r["id"], "zone": f"{r['fog']}/{r['zone']}", "seconds": r["seconds"],
                               "waiting_s": round(now - r["received"], 1)} for r in self.queue],
                    "waiting_reason": self.waiting_reason,
                    "neighbours": {f: {k: v for k, v in c.items() if k != "received"} | {
                        "age_s": round(now - c["received"], 1) if c["received"] else None}
                        for f, c in self.others.items()},
                    "history": list(self.history)}

    def _other_user(self, now: float) -> str | None:
        for fog, c in self.others.items():
            if c.get("using") and c["received"] + c.get("remaining_s", 0) > now:
                return fog
        return None


# ---------------------------------------------------------------------- MQTT
def run_mqtt(sched: PumpScheduler, host: str, port: int, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    fog = sched.fog
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"pump-scheduler-{fog}")
    sched.publish = lambda topic, payload, retain: client.publish(topic, json.dumps(payload), qos=1,
                                                                  retain=retain)
    # si le fog s'arrête brutalement : sa demande disparaît (sinon elle bloquerait l'autre jusqu'au TTL)
    client.will_set(topics.pump_claim(fog), json.dumps({"fog": fog, "want": False, "using": False,
                                                        "since": 0, "remaining_s": 0}), qos=1, retain=True)
    subs = [topics.pump_request("+"), topics.valve("+", "+"), topics.pump_claim("+")]

    def on_connect(c, u, f, rc, p):
        print(f"[mqtt] connecté ({rc}), abonné à {', '.join(subs)}")
        for s in subs:
            c.subscribe(s, qos=1)

    def on_message(c, u, m):
        try:
            payload = json.loads(m.payload)
            parts = m.topic.split("/")
            if parts[1] == "pump" and parts[2] == "request":
                if m.retain:
                    return                                 # une vieille demande ne relance pas un arrosage
                sched.on_request(payload)
            elif parts[1] == "zone" and parts[3] == "valve":
                sched.on_valve(payload)
            elif parts[:3] == ["domaine", "pump", "claim"]:
                sched.on_claim(parts[3], payload, retained=m.retain)
        except (ValueError, KeyError, IndexError) as e:
            print(f"[mqtt] message ignoré sur {m.topic} : {e}")

    client.on_connect, client.on_message = on_connect, on_message
    while not stop.is_set():
        try:
            client.connect(host, port)
            break
        except OSError as e:
            print(f"[mqtt] broker {host}:{port} injoignable ({e}), nouvel essai dans 5 s")
            stop.wait(5)
    client.loop_start()
    while not stop.wait(0.2):
        sched.tick()
    client.publish(topics.pump_claim(fog), json.dumps({"fog": fog, "want": False, "using": False,
                                                       "since": 0, "remaining_s": 0}), qos=1, retain=True)
    client.disconnect()
    client.loop_stop()


def main() -> None:
    cfg, fog = load_config(), fog_id()
    sim = cfg.get("simulation", {})
    print(f"pump-scheduler {fog} : pompe commune, 1 s réelle = {sim.get('speed', 360):g} s simulées")
    try:
        run_mqtt(PumpScheduler(fog, sim.get("speed", 360), sim.get("wake_period_s", 10)),
                 cfg["mqtt"]["host"], cfg["mqtt"]["port"])
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
