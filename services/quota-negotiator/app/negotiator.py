"""quota-negotiator of one fog: shares the daily water quota with the neighbour fog.

Daily schedule, in SIMULATED time (read from the telemetry `ts`):
    03:00  publish my request    domaine/quota/request/<me>   {day, need_l, priority}
    04:00  decide (or as soon as the neighbour's request is there):
             both requests -> allocate()  (same result on both fogs, no leader)
             neighbour silent -> isolated mode, 50 % of the quota
           publish  domaine/quota/allocation/<me>  (shared)  and  <me>/quota/budget (local)
    10:00  give back what I won't need this evening:  domaine/quota/release/<me>

    FOG_ID=fog-a python -m app.negotiator
"""

import json
import math
import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

import paho.mqtt.client as mqtt

from agrifog_common import fog_id, load_config, topics

from .allocation import (CropParams, ZoneNeedInput, allocate, evening_need_l, fog_need_l,
                         isolated_share, priority, spare_to_release)

REQUEST_HOUR, RELEASE_HOUR, EVENING_HOUR = 3, 10, 18
WAIT_NEIGHBOUR = timedelta(hours=1)
STALE = timedelta(hours=3)
Publish = Callable[[str, dict, bool], None]


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def et_fraction(h_from: float, h_to: float) -> float:
    """Share of the daily ET between two hours of the same day (daylight sine 6h-18h)."""
    def cum(h: float) -> float:
        h = min(max(h, 6.0), 18.0)
        return (1 - math.cos(math.pi * (h - 6) / 12)) / 2
    return max(0.0, cum(h_to) - cum(h_from))


@dataclass
class Day:
    my_request: dict | None = None
    their_request: dict | None = None
    requested_at: datetime | None = None
    allocation: dict | None = None
    mode: str | None = None
    allocated_l: float = 0.0
    used_l: float = 0.0
    given_l: float = 0.0
    received_l: float = 0.0
    release_done: bool = False


@dataclass
class QuotaNegotiator:
    cfg: dict
    fog: str
    publish: Publish = lambda topic, payload, retain=False: None
    days: dict[date, Day] = field(default_factory=dict)
    now: datetime | None = None
    et0: float = 5.0
    rain_24h: float = 0.0
    readings: dict[str, dict[str, tuple[datetime, float]]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        c, s = self.cfg["crop"], self.cfg["soil"]
        self.neighbour = self.cfg["neighbor"]
        self.quota_l = float(self.cfg["domain"]["daily_quota_l"])
        self.crop = CropParams(c["kc"], c["moisture_threshold_pct"], s["field_capacity_pct"],
                               s["wilting_point_pct"], c["root_depth_mm"], c.get("stress_sensitivity", 1.0))
        self.areas = {z["id"]: z.get("area_m2", 200.0) for z in self.cfg["zones"]}

    # ----------------------------------------------------------- inputs
    def on_telemetry(self, zone: str, msg: dict) -> None:
        with self.lock:
            t = parse_ts(msg["ts"])
            self.readings.setdefault(zone, {})[msg["device"]] = (t, float(msg["soil_moisture"]))
            self._tick(t)

    def on_decision(self, msg: dict) -> None:
        with self.lock:
            self.et0 = float(msg.get("et0", self.et0))

    def on_forecast(self, msg: dict) -> None:
        with self.lock:
            self.rain_24h = float(msg.get("rain_next_24h_mm", 0))

    def on_usage(self, msg: dict) -> None:
        with self.lock:
            self._day(date.fromisoformat(msg["day"])).used_l = float(msg["used_l"])

    def on_neighbour_request(self, msg: dict) -> None:
        with self.lock:
            self._day(date.fromisoformat(msg["day"])).their_request = msg
            if self.now:
                self._tick(self.now)

    def on_neighbour_release(self, msg: dict) -> None:
        with self.lock:
            d = date.fromisoformat(msg["day"])
            day = self._day(d)
            if day.allocation is None:
                return
            day.received_l += float(msg["liters"])
            day.allocated_l += float(msg["liters"])
            print(f"[{d}] {self.neighbour} nous cède {float(msg['liters']):.0f} L -> budget {day.allocated_l:.0f} L")
            self._publish_budget(d, day)

    # --------------------------------------------------------- schedule
    def _day(self, d: date) -> Day:
        return self.days.setdefault(d, Day())

    def _zones(self) -> list[ZoneNeedInput]:
        out = []
        for zone, by_dev in self.readings.items():
            fresh = [v for t, v in by_dev.values() if self.now - t <= STALE]
            if fresh and zone in self.areas:
                out.append(ZoneNeedInput(statistics.median(fresh), self.areas[zone]))
        return out

    def _tick(self, now: datetime) -> None:
        self.now = now if self.now is None else max(self.now, now)
        now = self.now
        d, day, h = now.date(), self._day(now.date()), now.hour + now.minute / 60
        zones = self._zones()
        if not zones:
            return

        if day.my_request is None and h >= REQUEST_HOUR:
            need = fog_need_l(zones, self.crop, self.et0, self.rain_24h)
            day.my_request = {"day": d.isoformat(), "fog": self.fog, "need_l": round(need, 1),
                              "priority": priority(zones, self.crop), "ts": now.isoformat()}
            day.requested_at = now
            # "retained" : un voisin qui démarre en retard retrouve la demande du jour sur le broker
            self.publish(topics.quota_request(self.fog), day.my_request, True)
            print(f"[{d}] demande : {need:.0f} L, priorité {day.my_request['priority']}")

        if day.my_request and day.allocation is None:
            theirs = day.their_request
            if theirs is not None:
                alloc = allocate(self.quota_l, {self.fog: day.my_request, self.neighbour: theirs})
                day.mode = "négocié"
            elif now >= day.requested_at + WAIT_NEIGHBOUR:
                alloc = {self.fog: isolated_share(self.quota_l)}
                day.mode = "isolé (voisin injoignable : 50 %)"
            else:
                return
            day.allocation, day.allocated_l = alloc, alloc[self.fog]
            self.publish(topics.quota_allocation(self.fog),
                         {"day": d.isoformat(), "quota_l": self.quota_l, "allocation": alloc,
                          "mode": day.mode, "computed_by": self.fog}, True)
            self._publish_budget(d, day)
            print(f"[{d}] répartition {day.mode} : {alloc}")

        if day.allocation and not day.release_done and h >= RELEASE_HOUR:
            day.release_done = True
            evening = evening_need_l(zones, self.crop, self.et0, et_fraction(h, EVENING_HOUR))
            spare = spare_to_release(day.allocated_l, day.used_l, evening)
            # on ne cède que ce qui MANQUE au voisin (son besoin annoncé - sa part) : si les deux fogs
            # ont assez, personne ne cède (évite les cessions croisées à 10 h) ; et on ne rend pas
            # d'eau à un voisin qui vient de nous céder la sienne.
            theirs = day.their_request or {}
            missing = max(0.0, float(theirs.get("need_l", 0)) - day.allocation.get(self.neighbour, 0.0))
            spare = round(min(spare, missing), 1)
            if spare > 0 and day.mode == "négocié" and day.received_l == 0:
                day.allocated_l -= spare
                day.given_l += spare
                self.publish(topics.quota_release(self.fog), {"day": d.isoformat(), "fog": self.fog,
                                                               "liters": spare}, False)
                self._publish_budget(d, day)
                print(f"[{d}] cession de {spare:.0f} L à {self.neighbour} (besoin du soir {evening:.0f} L)")

    def _publish_budget(self, d: date, day: Day) -> None:
        self.publish(topics.quota_budget(self.fog),
                     {"day": d.isoformat(), "allocated_l": round(day.allocated_l, 1), "mode": day.mode,
                      "given_l": day.given_l, "received_l": day.received_l}, True)

    def snapshot(self) -> dict:
        with self.lock:
            return {d.isoformat(): {"mode": x.mode, "allocated_l": x.allocated_l, "used_l": x.used_l,
                                    "given_l": x.given_l, "received_l": x.received_l,
                                    "my_request": x.my_request, "their_request": x.their_request}
                    for d, x in sorted(self.days.items())[-7:]}


# ---------------------------------------------------------------------- MQTT
def run_mqtt(neg: QuotaNegotiator, host: str, port: int, stop: threading.Event | None = None) -> None:
    fog, nb = neg.fog, neg.neighbour
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"quota-negotiator-{fog}")
    neg.publish = lambda topic, payload, retain=False: client.publish(topic, json.dumps(payload), qos=1, retain=retain)
    handlers = {
        topics.telemetry(fog, "+"): lambda parts, p: neg.on_telemetry(parts[2], p),
        topics.decision(fog, "+"): lambda parts, p: neg.on_decision(p),
        topics.weather_forecast(fog): lambda parts, p: neg.on_forecast(p),
        topics.quota_usage(fog): lambda parts, p: neg.on_usage(p),
        topics.quota_request(nb): lambda parts, p: neg.on_neighbour_request(p),
        topics.quota_release(nb): lambda parts, p: neg.on_neighbour_release(p),
    }

    def on_connect(c, u, f, rc, p):
        print(f"[mqtt] connecté ({rc})")
        for t in handlers:
            c.subscribe(t, qos=1)

    def on_message(c, u, m):
        try:
            for pattern, handler in handlers.items():
                if mqtt.topic_matches_sub(pattern, m.topic):
                    handler(m.topic.split("/"), json.loads(m.payload))
                    return
        except (ValueError, KeyError) as e:
            print(f"[mqtt] message ignoré sur {m.topic} : {e}")

    client.on_connect, client.on_message = on_connect, on_message
    while True:
        try:
            client.connect(host, port)
            break
        except OSError as e:
            print(f"[mqtt] broker injoignable ({e}), nouvel essai dans 5 s")
            time.sleep(5)
    client.loop_start()
    (stop or threading.Event()).wait()
    client.loop_stop()


def main() -> None:
    cfg, fog = load_config(), fog_id()
    neg = QuotaNegotiator(cfg, fog)
    print(f"quota-negotiator {fog} : quota du domaine {neg.quota_l:.0f} L/jour partagé avec {neg.neighbour}")
    try:
        run_mqtt(neg, cfg["mqtt"]["host"], cfg["mqtt"]["port"])
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
