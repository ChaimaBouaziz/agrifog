"""The irrigation engine of one fog: telemetry in, decisions / commands / alerts out.

    FOG_ID=fog-a python -m app.engine          # moteur seul, sans API

Subscriptions:  {fog}/zone/+/telemetry   {fog}/zone/+/valve   {fog}/device/+/heartbeat
                {fog}/weather/forecast
Publications:   {fog}/zone/<z>/cmd   {fog}/zone/<z>/decision   {fog}/alert
"""

import json
import threading
import time
from datetime import datetime, timedelta
from typing import Callable

import paho.mqtt.client as mqtt

from agrifog_common import fog_id, load_config, topics
from agrifog_common.config import REPO_ROOT
from agrifog_common.ml import HORIZON_H, TARGET_SCALE, feature_vector, load_model, predict

from . import anomalies as an
from .decision import Crop, Decision, PendingCheck, ZoneSpec, check_delivery, decide
from .zone_state import ZoneState, parse_ts

CHECK_DELAY = timedelta(minutes=30)   # vérif débitmètre 30 min (simulées) après la fin
ACK_TIMEOUT = timedelta(hours=3)      # le boîtier de la vanne doit confirmer avant
FORECAST_MAX_AGE = timedelta(hours=36)
MODEL_PATH = REPO_ROOT / "data" / "models" / "moisture_model.json"   # produit par fed-learning
Publish = Callable[[str, dict], None]


class IrrigationEngine:
    def __init__(self, cfg: dict, fog: str, publish: Publish | None = None):
        self.fog = fog
        self.lat = cfg["location"]["latitude"]
        c, s = cfg["crop"], cfg["soil"]
        self.crop = Crop(c["name"], c["kc"], c["moisture_threshold_pct"],
                         s["field_capacity_pct"], c["root_depth_mm"])
        self.wp = s["wilting_point_pct"]
        self.model, self._model_mtime = None, None
        self.zones = {z["id"]: ZoneSpec(z["id"], z.get("area_m2", 200), z.get("flow_lpm", 20))
                      for z in cfg["zones"]}
        self.state = {zid: ZoneState(zid) for zid in self.zones}
        self.devices = {d: z["id"] for z in cfg["zones"] for d in z["devices"]}
        # zones simulées (temps simulé) et zones réelles, ex. Wokwi (temps réel) : deux horloges
        self.domain = {d: ("sim" if z.get("simulated", True) else "real") for z in cfg["zones"] for d in z["devices"]}
        self.domain_now: dict[str, datetime] = {}
        self.last_seen: dict[str, datetime] = {}
        self.rain_expected_mm, self.forecast_ts = 0.0, None
        self.alerts = an.AlertBook()
        self.budget: dict | None = None       # quota du jour, donné par le quota-negotiator
        quota = cfg.get("domain", {}).get("daily_quota_l")
        self.provisional_l = quota / 2 if quota else None   # avant la négociation : part égale
        self.used_l: dict[str, float] = {}    # litres commandés par jour
        self.publish: Publish = publish or (lambda topic, payload: None)
        self.lock = threading.Lock()

    # ------------------------------------------------------------- telemetry
    def on_telemetry(self, zone_id: str, msg: dict) -> None:
        if zone_id not in self.state:
            return
        with self.lock:
            st = self.state[zone_id]
            now = st.update(msg)
            self._seen(msg.get("device"), now, msg["ts"])
            self._check_ack_timeout(st, now)
            self._check_flow(st, now, float(msg.get("flow_lpm", 0)), msg["ts"])
            self._check_delivery(st, now)
            self._check_rise(st, now)
            self._check_offline(now, msg["ts"])

            moisture = st.moisture()
            if moisture is None:
                return
            et0, et0_src = st.temps.et0(now.date(), self.lat)
            d = decide(moisture, now, et0, self.crop, self.zones[zone_id],
                       self._rain(now), st.watering_until)
            if d.action == "water":
                d = self._apply_quota(d, now, self.zones[zone_id])
            ai = self._ai_prediction(st, now, et0)
            if ai is not None and d.kind in ("water", "urgent", "no_need", "rain"):
                d.reason += f" | IA fédérée : {ai:.1f} % dans {HORIZON_H} h sans arrosage"
            changed = st.last_decision is None or st.last_decision["kind"] != d.kind or d.action == "water"
            st.last_decision = {"ts": msg["ts"], "action": d.action, "kind": d.kind, "reason": d.reason,
                                "moisture": round(moisture, 1), "et0": round(et0, 2),
                                "et0_source": et0_src, "rain_next_24h_mm": self._rain(now),
                                "ai_moisture_6h": None if ai is None else round(ai, 2)}
            if d.action == "water":
                self._water(st, d, now)
            if changed:
                self.publish(topics.decision(self.fog, zone_id), st.last_decision)
                print(f"[{now:%m-%d %H:%M}] {zone_id} {d.action:5} | {d.reason}")

    def _ai_prediction(self, st: ZoneState, now: datetime, et0: float) -> float | None:
        """Moisture in 6 h if we do NOT water, according to the federated model (if trained)."""
        try:
            mtime = MODEL_PATH.stat().st_mtime
        except OSError:
            return None
        if mtime != self._model_mtime:                 # nouveau modèle publié : on le recharge
            loaded = load_model(MODEL_PATH)
            self.model, self._model_mtime = (loaded[0] if loaded else None), mtime
        moisture = st.moisture()
        if self.model is None or moisture is None or st.air is None:
            return None
        x = feature_vector(moisture, st.air[0], st.air[1], now, et0, self.crop.kc, self.crop.fc_pct,
                           self.wp, self.crop.root_mm, irrig_next=0.0,
                           rain_next=self._rain(now) * HORIZON_H / 24)
        return moisture + float(predict(self.model, x[None, :])[0]) * TARGET_SCALE

    def _rain(self, now: datetime) -> float:
        if self.forecast_ts is None or now - self.forecast_ts > FORECAST_MAX_AGE:
            return 0.0  # pas de prévision récente (internet coupé) : on décide sans
        return self.rain_expected_mm

    def _apply_quota(self, d: Decision, now: datetime, zone: ZoneSpec) -> Decision:
        """Respect the daily budget negotiated with the neighbour (no budget = no limit)."""
        day = now.date().isoformat()
        if self.budget and self.budget.get("day") == day:
            allocated, label = self.budget["allocated_l"], "alloués"
        elif self.provisional_l is not None:      # pas encore négocié (avant 4h) : part égale
            allocated, label = self.provisional_l, "provisoires (part égale)"
        else:
            return d
        used = self.used_l.get(day, 0.0)
        left = allocated - used
        if left < 50:
            return Decision("wait", f"quota du jour épuisé ({used:.0f} L utilisés sur "
                                    f"{allocated:.0f} L {label})", "quota")
        if d.liters <= left:
            return d
        seconds = int(left / zone.flow_lpm * 60)
        return Decision("water", f"{d.reason} — limité par le quota : {left:.0f} L restants",
                        d.kind, seconds=seconds, liters=left, dose_mm=left / zone.area_m2)

    def _water(self, st: ZoneState, d, now: datetime) -> None:
        cmd_id = f"{self.fog}-{st.id}-{now:%Y%m%dT%H%M}"
        self.publish(topics.cmd(self.fog, st.id), {"id": cmd_id, "action": "water", "seconds": d.seconds})
        day = now.date().isoformat()
        self.used_l[day] = self.used_l.get(day, 0.0) + d.liters
        self.publish(topics.quota_usage(self.fog), {"day": day, "used_l": round(self.used_l[day], 1)})
        # estimation en attendant l'accusé du boîtier (évite de renvoyer l'ordre)
        st.watering_until = now + timedelta(seconds=d.seconds)
        st.cmd = {"id": cmd_id, "sent": now, "liters": d.liters, "dose_mm": d.dose_mm,
                  "before": {dev: v for dev, (t, v) in st.moisture_by_device.items()}}

    # ------------------------------------------------------------ valve ack
    def on_valve(self, zone_id: str, msg: dict) -> None:
        if zone_id not in self.state:
            return
        with self.lock:
            st = self.state[zone_id]
            opened, until = parse_ts(msg["opened"]), parse_ts(msg["until"])
            if not st.cmd or msg.get("id") != st.cmd["id"]:
                # ordre externe (bouton Telegram, opérateur) : on le suit comme les nôtres,
                # sinon son eau passerait pour une fuite et une pompe en panne passerait inaperçue
                spec = self.zones[zone_id]
                liters = spec.flow_lpm * (until - opened).total_seconds() / 60
                st.cmd = {"id": msg.get("id"), "sent": opened, "liters": liters,
                          "dose_mm": liters / spec.area_m2,
                          "before": {dev: v for dev, (t, v) in st.moisture_by_device.items()}}
                day = opened.date().isoformat()
                self.used_l[day] = self.used_l.get(day, 0.0) + liters
                self.publish(topics.quota_usage(self.fog), {"day": day, "used_l": round(self.used_l[day], 1)})
                print(f"[{opened:%m-%d %H:%M}] {zone_id} arrosage manuel ({msg.get('id')}) : {liters:.0f} L")
            st.valve_opened, st.valve_until = opened, until
            st.watering_until = st.valve_until
            st.pending = PendingCheck(st.cmd["id"], float(msg.get("water_l_total", st.water_l_total or 0)),
                                      st.cmd["liters"], st.valve_until + CHECK_DELAY)
            print(f"[{st.valve_opened:%m-%d %H:%M}] {zone_id} vanne ouverte par {msg.get('device')} "
                  f"jusqu'à {st.valve_until:%H:%M}")

    def _check_ack_timeout(self, st: ZoneState, now: datetime) -> None:
        if st.cmd and st.valve_opened is None and now - st.cmd["sent"] > ACK_TIMEOUT:
            self._alert(("valve", st.id), {"ts": now.isoformat(), "type": "valve", "zone": st.id,
                        "msg": f"la vanne de {st.id} n'a pas confirmé l'ordre {st.cmd['id']}"})
            st.cmd, st.watering_until = None, None

    # ------------------------------------------------------------ anomalies
    def _check_flow(self, st: ZoneState, now: datetime, flow: float, ts: str) -> None:
        if an.detect_leak(flow, now, st.valve_opened, st.valve_until):
            self._alert(("leak", st.id), {"ts": ts, "type": "leak", "zone": st.id,
                        "msg": f"FUITE sur {st.id} : {flow:.1f} L/min alors que la vanne est fermée"})
        elif flow <= an.FLOW_EPS_LPM:
            self._resolve(("leak", st.id), ts)
        if an.detect_pump_failure(flow, now, st.valve_opened, st.valve_until):
            self._alert(("pump", st.id), {"ts": ts, "type": "pump", "zone": st.id,
                        "msg": f"vanne de {st.id} ouverte mais débit nul : pompe en panne ou réservoir vide ?"})
        elif an.valve_should_be_open(now, st.valve_opened, st.valve_until) and flow > an.FLOW_EPS_LPM:
            self._resolve(("pump", st.id), ts)        # l'eau coule de nouveau pendant l'arrosage

    def _check_delivery(self, st: ZoneState, now: datetime) -> None:
        if st.pending is None or st.water_l_total is None:
            return
        res = check_delivery(st.pending, now, st.water_l_total)
        if res is None:
            return
        status, message = res
        delivered = st.water_l_total - st.pending.start_total_l
        if status == "fail":
            self._alert(("pump", st.id), {"ts": now.isoformat(), "type": "pump", "zone": st.id, "msg": message})
        else:
            print(f"[{now:%m-%d %H:%M}] {st.id} débit OK | {message}")
            self._resolve(("pump", st.id), now.isoformat())
            area = self.zones[st.id].area_m2
            st.rise_check = {"after": st.valve_until + an.RISE_CHECK_AFTER, "until": st.valve_until,
                             "before": st.cmd["before"],
                             "expected_pct": delivered / area / self.crop.root_mm * 100}
        st.pending, st.cmd, st.valve_opened, st.valve_until = None, None, None, None

    def _check_rise(self, st: ZoneState, now: datetime) -> None:
        rc = st.rise_check
        if rc is None or now < rc["after"]:
            return
        # mesures prises après l'arrosage ; on attend que chaque capteur ait parlé (2 h max de plus)
        after = {d: v for d, (t, v) in st.moisture_by_device.items() if t >= rc["until"]}
        if set(rc["before"]) - set(after) and now < rc["after"] + an.RISE_CHECK_AFTER:
            return
        for kind, device, msg in an.check_rise(rc["before"], after, rc["expected_pct"]):
            key = ("no_rise", st.id) if kind == "blocked" else ("sensor", device)
            self._alert(key, {"ts": now.isoformat(), "type": "no_rise" if kind == "blocked" else "sensor",
                              "zone": st.id, "device": device, "msg": msg})
        st.rise_check = None

    def _seen(self, device: str | None, now: datetime, ts: str) -> None:
        if device not in self.devices:
            return
        dom = self.domain[device]
        if dom not in self.domain_now and dom == "sim":
            # 1er message simulé : on démarre le chronomètre de tous les ESP32 simulés
            self.last_seen.update({d: now for d, x in self.domain.items() if x == "sim"})
        self.domain_now[dom] = max(now, self.domain_now.get(dom, now))
        self.last_seen[device] = max(now, self.last_seen.get(device, now))   # un ESP32 réel : dès qu'il parle
        self._resolve(("offline", device), ts)

    def _check_offline(self, now: datetime, ts: str) -> None:
        for dom, dom_now in self.domain_now.items():
            seen = {d: t for d, t in self.last_seen.items() if self.domain[d] == dom}
            for d in an.offline_devices(seen, dom_now):
                self._alert(("offline", d), {"ts": ts, "type": "offline", "zone": self.devices[d], "device": d,
                            "msg": f"{d} ne donne plus de nouvelles depuis {dom_now - seen[d]} (nœud hors service ?)"})

    def _alert(self, key: tuple, payload: dict) -> None:
        msg = self.alerts.raise_(key, payload)
        if msg:
            self.publish(topics.alert(self.fog), msg)
            print(f"  ⚠ ALERTE {msg['type']:8} | {msg['msg']}")

    def _resolve(self, key: tuple, ts: str) -> None:
        msg = self.alerts.resolve(key, ts)
        if msg:
            self.publish(topics.alert(self.fog), msg)
            print(f"  ✓ résolu  {msg['type']:8} | {msg.get('zone') or ''} {msg.get('device') or ''}")

    # ------------------------------------------------------- other inputs
    def on_heartbeat(self, device: str, msg: dict) -> None:
        with self.lock:
            self._seen(device, parse_ts(msg["ts"]), msg["ts"])

    def on_budget(self, msg: dict) -> None:
        with self.lock:
            self.budget = msg

    def on_forecast(self, msg: dict) -> None:
        with self.lock:
            self.rain_expected_mm = float(msg.get("rain_next_24h_mm", 0))
            self.forecast_ts = parse_ts(msg["ts"])

    def snapshot(self) -> dict:
        with self.lock:
            return {"zones": {zid: {"moisture": st.moisture(), "water_l_total": st.water_l_total,
                                    "last_decision": st.last_decision}
                              for zid, st in self.state.items()},
                    "rain_next_24h_mm": self.rain_expected_mm,
                    "budget": self.budget, "used_l": self.used_l,
                    "alerts": list(self.alerts.active.values())}


# ---------------------------------------------------------------------- MQTT
def run_mqtt(engine: IrrigationEngine, host: str, port: int, stop: threading.Event | None = None):
    fog = engine.fog
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"irrigation-engine-{fog}")
    engine.publish = lambda topic, payload: client.publish(topic, json.dumps(payload), qos=1)
    subs = [topics.telemetry(fog, "+"), topics.valve(fog, "+"), topics.heartbeat(fog, "+"),
            topics.weather_forecast(fog), topics.quota_budget(fog)]

    def on_connect(c, u, f, rc, p):
        print(f"[mqtt] connecté ({rc}), abonné à {', '.join(subs)}")
        for s in subs:
            c.subscribe(s, qos=1)

    def on_message(c, u, m):
        try:
            parts, payload = m.topic.split("/"), json.loads(m.payload)
            if m.topic == topics.weather_forecast(fog):
                engine.on_forecast(payload)
            elif m.topic == topics.quota_budget(fog):
                engine.on_budget(payload)
            elif parts[1] == "device":
                engine.on_heartbeat(parts[2], payload)
            elif parts[3] == "telemetry":
                engine.on_telemetry(parts[2], payload)
            elif parts[3] == "valve":
                engine.on_valve(parts[2], payload)
        except (ValueError, KeyError, IndexError) as e:
            print(f"[mqtt] message ignoré sur {m.topic} : {e}")

    client.on_connect, client.on_message = on_connect, on_message
    while True:
        try:
            client.connect(host, port)
            break
        except OSError as e:
            print(f"[mqtt] broker {host}:{port} injoignable ({e}), nouvel essai dans 5 s")
            time.sleep(5)
    client.loop_start()
    (stop or threading.Event()).wait()
    client.loop_stop()


def main() -> None:
    cfg, fog = load_config(), fog_id()
    engine = IrrigationEngine(cfg, fog)
    print(f"irrigation-engine {fog} : culture {engine.crop.name}, seuil {engine.crop.threshold_pct} %, "
          f"zones {', '.join(engine.zones)}")
    try:
        run_mqtt(engine, cfg["mqtt"]["host"], cfg["mqtt"]["port"])
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
