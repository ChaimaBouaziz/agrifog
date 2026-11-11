"""The irrigation engine of one fog: telemetry in, decisions / commands / alerts out.

    FOG_ID=fog-a python -m app.engine          # moteur seul, sans API
"""

import json
import threading
import time
from datetime import timedelta
from typing import Callable

import paho.mqtt.client as mqtt

from agrifog_common import fog_id, load_config, topics

from .decision import Crop, PendingCheck, ZoneSpec, check_delivery, decide
from .zone_state import ZoneState

CHECK_DELAY = timedelta(minutes=30)   # vérif débitmètre 30 min (simulées) après la fin
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
        self.rain_expected_mm = 0.0          # TODO semaine 4 : prévision Open-Meteo
        self.publish: Publish = publish or (lambda topic, payload: None)
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ core
    def on_telemetry(self, zone_id: str, msg: dict) -> None:
        if zone_id not in self.state:
            return
        with self.lock:
            st = self.state[zone_id]
            now = st.update(msg)
            self._check_flow(st, now)
            moisture = st.moisture()
            if moisture is None:
                return
            et0, et0_src = st.temps.et0(now.date(), self.lat)
            d = decide(moisture, now, et0, self.crop, self.zones[zone_id],
                       self.rain_expected_mm, st.watering_until)

            changed = st.last_decision is None or st.last_decision["kind"] != d.kind or d.action == "water"
            st.last_decision = {"ts": msg["ts"], "action": d.action, "kind": d.kind, "reason": d.reason,
                                "moisture": round(moisture, 1), "et0": round(et0, 2),
                                "et0_source": et0_src}
            if d.action == "water":
                self._water(st, d, now)
            if changed:
                self.publish(topics.decision(self.fog, zone_id), st.last_decision)
                print(f"[{now:%m-%d %H:%M}] {zone_id} {d.action:5} | {d.reason}")

    def _water(self, st: ZoneState, d, now) -> None:
        cmd_id = f"{self.fog}-{st.id}-{now:%Y%m%dT%H%M}"
        self.publish(topics.cmd(self.fog, st.id),
                     {"id": cmd_id, "action": "water", "seconds": d.seconds})
        st.watering_until = now + timedelta(seconds=d.seconds)
        st.pending = PendingCheck(cmd_id, st.water_l_total or 0.0, d.liters,
                                  st.watering_until + CHECK_DELAY)

    def _check_flow(self, st: ZoneState, now) -> None:
        if st.pending is None or st.water_l_total is None:
            return
        res = check_delivery(st.pending, now, st.water_l_total)
        if res is None:
            return
        status, message = res
        if status == "fail":
            self.publish(topics.alert(self.fog), {"ts": now.isoformat(), "type": "flow",
                                                  "zone": st.id, "msg": message})
            print(f"[{now:%m-%d %H:%M}] {st.id} ALERTE | {message}")
        else:
            print(f"[{now:%m-%d %H:%M}] {st.id} débit OK | {message}")
        st.pending = None

    def snapshot(self) -> dict:
        with self.lock:
            return {zid: {"moisture": st.moisture(), "water_l_total": st.water_l_total,
                          "last_decision": st.last_decision}
                    for zid, st in self.state.items()}


# ---------------------------------------------------------------------- MQTT
def run_mqtt(engine: IrrigationEngine, host: str, port: int, stop: threading.Event | None = None):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"irrigation-engine-{engine.fog}")
    engine.publish = lambda topic, payload: client.publish(topic, json.dumps(payload), qos=1)
    sub = topics.telemetry(engine.fog, "+")

    def on_connect(c, u, f, rc, p):
        print(f"[mqtt] connecté ({rc}), abonné à {sub}")
        c.subscribe(sub, qos=1)

    def on_message(c, u, m):
        try:
            zone_id = m.topic.split("/")[2]
            engine.on_telemetry(zone_id, json.loads(m.payload))
        except (ValueError, KeyError) as e:
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
