"""Virtual ESP32 fleet: one thread per device, same cycle as the real firmware.

Cycle of a device (like the ESP32):
    wake up -> connect MQTT -> publish telemetry + heartbeat
    -> stay awake AWAKE_S seconds to receive a command -> disconnect -> deep sleep

Commands sent while a device sleeps are not lost: persistent MQTT session
(clean_session=False) + QoS 1, so the broker keeps them until the next wake-up.
"""

import json
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from agrifog_common import topics

FW_VERSION = "0.1.0"
AWAKE_S = 2.0
FLOW_LPM = 12.0  # débit quand la vanne de la zone est ouverte


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Zone:
    """Shared state of a zone. Soil model is a placeholder until week 2."""

    id: str
    moisture: float = 40.0
    watering_until: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def is_watering(self, t: float | None = None) -> bool:
        return (t or time.time()) < self.watering_until

    def water(self, seconds: float, t: float | None = None) -> None:
        t = t or time.time()
        with self.lock:
            self.watering_until = max(self.watering_until, t + seconds)

    def step(self, dt: float, t: float | None = None) -> None:
        # TODO semaine 2 : remplacer par le bilan hydrique (ET0, pluie, arrosage)
        with self.lock:
            if self.is_watering(t):
                self.moisture = min(60.0, self.moisture + 0.05 * dt)
            else:
                self.moisture = max(5.0, self.moisture - 0.01 * dt)


@dataclass
class VirtualESP32:
    fog: str
    device: str
    zone: Zone
    broker: tuple[str, int]
    period_s: float
    battery_v: float = 4.10
    boot_s: float = field(default_factory=time.time)
    cycles: int = 0

    # ---- payloads (pure functions, easy to test) ----
    def telemetry(self) -> dict:
        return {
            "ts": now_iso(),
            "device": self.device,
            "soil_moisture": round(self.zone.moisture + random.gauss(0, 0.5), 1),
            "air_temp": round(random.uniform(18, 30), 1),       # TODO semaine 2 : météo Sfax
            "air_humidity": round(random.uniform(40, 70), 1),
            "flow_lpm": FLOW_LPM if self.zone.is_watering() else 0.0,
        }

    def heartbeat(self) -> dict:
        return {
            "ts": now_iso(),
            "fw_version": FW_VERSION,
            "battery_v": round(self.battery_v, 3),
            "rssi": random.randint(-85, -55),
            "uptime_s": int(time.time() - self.boot_s),
            "cycles": self.cycles,
        }

    def on_command(self, payload: dict) -> None:
        if payload.get("action") == "water":
            self.zone.water(float(payload.get("seconds", 0)))
            print(f"[{self.device}] arrosage {self.zone.id} pendant {payload.get('seconds')} s")

    def drain_battery(self) -> None:
        # éveillé : ~80 mA, deep sleep : ~10 µA -> presque tout se joue pendant l'éveil
        self.battery_v = max(3.0, self.battery_v - 0.0004)

    # ---- one wake-up cycle ----
    def wake_once(self) -> None:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=self.device, clean_session=False
        )
        cmd_topic = topics.cmd(self.fog, self.zone.id)

        def on_connect(c, u, f, rc, p):
            c.subscribe(cmd_topic, qos=1)

        def on_message(c, u, msg):
            try:
                self.on_command(json.loads(msg.payload))
            except json.JSONDecodeError:
                print(f"[{self.device}] commande illisible : {msg.payload!r}")

        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(*self.broker)
        except OSError as e:
            print(f"[{self.device}] broker injoignable : {e}")  # TODO semaine 5 : broker voisin
            return
        client.loop_start()
        client.publish(topics.telemetry(self.fog, self.zone.id), json.dumps(self.telemetry()), qos=1)
        client.publish(topics.heartbeat(self.fog, self.device), json.dumps(self.heartbeat()), qos=1)
        time.sleep(AWAKE_S)
        client.disconnect()
        client.loop_stop()
        self.cycles += 1
        self.drain_battery()

    def run(self, stop: threading.Event) -> None:
        time.sleep(random.uniform(0, self.period_s))  # réveils décalés, comme sur le terrain
        while not stop.is_set():
            self.wake_once()
            stop.wait(self.period_s)  # deep sleep


def build_fleet(cfg: dict, fog: str, period_s: float, max_devices: int | None = None):
    broker = (cfg["mqtt"]["host"], cfg["mqtt"]["port"])
    zones = {z["id"]: Zone(z["id"]) for z in cfg["zones"]}
    devices = [
        VirtualESP32(fog, dev, zones[z["id"]], broker, period_s)
        for z in cfg["zones"]
        for dev in z["devices"]
    ]
    return zones, devices[:max_devices] if max_devices else devices