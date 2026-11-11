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

from . import anomalies as an
from .decision import Crop, PendingCheck, ZoneSpec, check_delivery, decide
from .zone_state import ZoneState, parse_ts

CHECK_DELAY = timedelta(minutes=30)   # vérif débitmètre 30 min (simulées) après la fin
ACK_TIMEOUT = timedelta(hours=3)      # le boîtier de la vanne doit confirmer avant
FORECAST_MAX_AGE = timedelta(hours=36)
Publish = Callable[[str, dict], None]


class IrrigationEngine:
    def __init__(self, cfg: dict, fog: str, publish: Publish | None = None):
        self.fog = fog
        self.lat = cfg["location"]["latitude"]
        c, s = cfg["crop"], cfg["soil"]
        self.crop = Crop(c["name"], c["kc"], c["moisture_threshold_pct"],
                         s["field_capacity_pct"], c["root_depth_mm"])
        self.zones = {z["id"]: ZoneSpec(z["id"], z.get("area_m2", 200), z.get("flow_lpm", 20))
                      for z in cfg["zones"]}
        self.state = {zid: ZoneState(zid) for zid in self.zones}
        self.devices = {d: z["id"] for z in cfg["zones"] for d in z["devices"]}
        self.last_seen: dict[str, datetime] = {}
        self.rain_expected_mm, self.forecast_ts = 0.0, None
        self.alerts = an.AlertBook()
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
            changed = st.last_decision is None or st.last_decision["kind"] != d.kind or d.action == "water"
            st.last_decision = {"ts": msg["ts"], "action": d.action, "kind": d.kind, "reason": d.reason,
                                "moisture": round(moisture, 1), "et0": round(et0, 2),
                                "et0_source": et0_src, "rain_next_24h_mm": self._rain(now)}
            if d.action == "water":
                self._water(st, d, now)
            if changed:
                self.publish(topics.decision(self.fog, zone_id), st.last_decision)
                print(f"[{now:%m-%d %H:%M}] {zone_id} {d.action:5} | {d.reason}")

    def _rain(self, now: datetime) -> float:
        if self.forecast_ts is None or now - self.forecast_ts > FORECAST_MAX_AGE:
            return 0.0  # pas de prévision récente (internet coupé) : on décide sans
        return self.rain_expected_mm

    def _water(self, st: ZoneState, d, now: datetime) -> None:
        cmd_id = f"{self.fog}-{st.id}-{now:%Y%m%dT%H%M}"
        self.publish(topics.cmd(self.fog, st.id), {"id": cmd_id, "action": "water", "seconds": d.seconds})
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
            if not st.cmd or msg.get("id") != st.cmd["id"]:
                return
            st.valve_opened, st.valve_until = parse_ts(msg["opened"]), parse_ts(msg["until"])
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
        if not self.last_seen:  # premier message : on démarre le chronomètre de tous les ESP32
            self.last_seen = {d: now for d in self.devices}
        if device in self.devices:
            self.last_seen[device] = max(now, self.last_seen.get(device, now))
            self._resolve(("offline", device), ts)

    def _check_offline(self, now: datetime, ts: str) -> None:
        for d in an.offline_devices(self.last_seen, now):
            self._alert(("offline", d), {"ts": ts, "type": "offline", "zone": self.devices[d], "device": d,
                        "msg": f"{d} ne donne plus de nouvelles depuis {now - self.last_seen[d]} (nœud hors service ?)"})

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
                    "alerts": list(self.alerts.active.values())}


# ---------------------------------------------------------------------- MQTT
def run_mqtt(engine: IrrigationEngine, host: str, port: int, stop: threading.Event | None = None):
    fog = engine.fog
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"irrigation-engine-{fog}")
    engine.publish = lambda topic, payload: client.publish(topic, json.dumps(payload), qos=1)
    subs = [topics.telemetry(fog, "+"), topics.valve(fog, "+"), topics.heartbeat(fog, "+"),
            topics.weather_forecast(fog)]

    def on_connect(c, u, f, rc, p):
        print(f"[mqtt] connecté ({rc}), abonné à {', '.join(subs)}")
        for s in subs:
            c.subscribe(s, qos=1)

    def on_message(c, u, m):
        try:
            parts, payload = m.topic.split("/"), json.loads(m.payload)
            if m.topic == topics.weather_forecast(fog):
                engine.on_forecast(payload)
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
