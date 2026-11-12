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
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

from agrifog_common import topics

from .clock import SimClock
from .soil import SoilBucket, bucket_from_config, et0_rate_mm_h
from .weather import air_conditions

FW_VERSION = "0.1.0"
AWAKE_S = 2.0
MAX_SUBSTEP = timedelta(minutes=15)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Zone:
    """One irrigation zone: soil bucket + real Sfax weather + a valve with a flowmeter.

    All times are SIMULATED (SimClock). A water command of N seconds opens the valve
    for N simulated seconds.
    """

    id: str
    soil: SoilBucket
    clock: SimClock
    weather: list[dict]
    area_m2: float = 200.0
    flow_lpm: float = 20.0
    water_l_total: float = 0.0        # compteur du débitmètre (comme un vrai, il ne fait que monter)
    leak_lpm: float = 0.0             # panne injectée : fuite sur la conduite
    pump_ok: bool = True              # panne injectée : pompe HS / réservoir vide
    _open_from: datetime | None = None
    _open_until: datetime | None = None
    _last_step: datetime | None = None
    _done_ids: set = field(default_factory=set, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def moisture(self) -> float:
        return self.soil.theta

    def today(self, t: datetime | None = None) -> dict:
        t = t or self.clock.now()
        day = (t.date() - self.clock.start.date()).days % len(self.weather)  # la saison boucle
        return self.weather[day]

    def air(self) -> tuple[float, float]:
        t = self.clock.now()
        return air_conditions(self.today(t), t.hour + t.minute / 60)

    def accept_command(self, cmd_id: str | None) -> bool:
        """False if this command id was already executed."""
        if cmd_id is None:
            return True
        with self.lock:
            if cmd_id in self._done_ids:
                return False
            self._done_ids.add(cmd_id)
            return True

    def is_watering(self, t: datetime | None = None) -> bool:
        t = t or self.clock.now()
        return self._open_until is not None and self._open_from <= t < self._open_until

    def water(self, seconds: float) -> tuple[datetime, datetime]:
        t = self.clock.now()
        with self.lock:
            if self.is_watering(t):  # QoS 1 : un doublon ne prolonge pas l'arrosage
                self._open_until = max(self._open_until, t + timedelta(seconds=seconds))
            else:
                self._open_from, self._open_until = t, t + timedelta(seconds=seconds)
            return self._open_from, self._open_until

    def current_flow(self) -> float:
        """What the flowmeter reads right now (L/min)."""
        valve = self.flow_lpm if (self.is_watering() and self.pump_ok) else 0.0
        return valve + self.leak_lpm

    def rain_next_hours(self, hours: float = 24) -> float:
        """Rain (mm) that will fall in the next `hours` — a perfect forecast."""
        t, total, step = self.clock.now(), 0.0, timedelta(hours=1)
        for _ in range(int(hours)):
            total += self.today(t)["rain_mm"] / 24
            t += step
        return round(total, 1)

    def _open_minutes(self, t0: datetime, t1: datetime) -> float:
        if self._open_until is None:
            return 0.0
        start, end = max(t0, self._open_from), min(t1, self._open_until)
        return max(0.0, (end - start).total_seconds() / 60)

    def step(self) -> None:
        """Advance the soil model from the last step to the current simulated time."""
        t1 = self.clock.now()
        with self.lock:
            t0 = self._last_step or t1
            self._last_step = t1
            # sous-pas de 15 min simulées max, pour suivre la courbe journalière de l'ET0
            while t0 < t1:
                ta = t0
                t0 = min(t1, ta + MAX_SUBSTEP)
                dt_h = (t0 - ta).total_seconds() / 3600
                day = self.today(ta)
                hour = ta.hour + ta.minute / 60 + dt_h / 2   # milieu du sous-pas
                minutes = (t0 - ta).total_seconds() / 60
                liters = self.flow_lpm * self._open_minutes(ta, t0) if self.pump_ok else 0.0
                self.water_l_total += liters + self.leak_lpm * minutes  # la fuite est perdue
                self.soil.step(
                    dt_h,
                    et0_mm_h=et0_rate_mm_h(day["et0_fao"], hour),
                    rain_mm=day["rain_mm"] / 24 * dt_h,   # pluie du jour étalée sur 24 h
                    irrig_mm=liters / self.area_m2,       # 1 L sur 1 m² = 1 mm
                )


@dataclass
class VirtualESP32:
    fog: str
    device: str
    zone: Zone
    brokers: list[tuple[str, int]]   # le sien d'abord, puis ceux des fogs voisins
    period_s: float
    has_valve: bool = False   # un seul boîtier par zone pilote la vanne
    # pannes injectables (voir faults.py)
    stuck_value: float | None = None
    soil_offset: float = 0.0
    air_offset: float = 0.0
    offline: bool = False
    connected_to: str | None = None
    battery_v: float = 4.10
    boot_s: float = field(default_factory=time.time)
    cycles: int = 0

    # ---- payloads (pure functions, easy to test) ----
    def telemetry(self) -> dict:
        temp, rh = self.zone.air()
        soil = (self.stuck_value if self.stuck_value is not None
                else self.zone.moisture + self.soil_offset + random.gauss(0, 0.3))
        return {
            "ts": self.zone.clock.iso(),                 # temps SIMULÉ
            "device": self.device,
            "soil_moisture": round(soil, 1),
            "air_temp": round(temp + self.air_offset + random.gauss(0, 0.2), 1),
            "air_humidity": round(rh + random.gauss(0, 1.0), 1),
            "flow_lpm": round(self.zone.current_flow(), 1),
            "water_l_total": round(self.zone.water_l_total, 1),
        }

    def heartbeat(self) -> dict:
        return {
            "ts": self.zone.clock.iso(),
            "fw_version": FW_VERSION,
            "battery_v": round(self.battery_v, 3),
            "rssi": random.randint(-85, -55),
            "uptime_s": int(time.time() - self.boot_s),
            "cycles": self.cycles,
            "broker": self.connected_to,
        }

    def on_command(self, payload: dict) -> dict | None:
        """Execute a water command; returns the acknowledgement to publish (or None)."""
        if not self.has_valve or payload.get("action") != "water":
            return None
        if not self.zone.accept_command(payload.get("id")):
            return None  # doublon (QoS 1) déjà exécuté
        opened, until = self.zone.water(float(payload.get("seconds", 0)))
        print(f"[{self.device}] vanne {self.zone.id} ouverte {payload.get('seconds')} s")
        iso = lambda t: t.isoformat(timespec="seconds").replace("+00:00", "Z")
        return {"id": payload.get("id"), "device": self.device, "state": "open",
                "opened": iso(opened), "until": iso(until),
                "water_l_total": round(self.zone.water_l_total, 1)}

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
            if self.has_valve:
                c.subscribe(cmd_topic, qos=1)

        def on_message(c, u, msg):
            try:
                ack = self.on_command(json.loads(msg.payload))
            except json.JSONDecodeError:
                print(f"[{self.device}] commande illisible : {msg.payload!r}")
                return
            if ack:
                c.publish(topics.valve(self.fog, self.zone.id), json.dumps(ack), qos=1)

        client.on_connect = on_connect
        client.on_message = on_message
        for host, port in self.brokers:      # son fog d'abord, puis les voisins
            try:
                client.connect(host, port)
                break
            except OSError:
                continue
        else:
            print(f"[{self.device}] aucun broker joignable ({', '.join(h for h, _ in self.brokers)})")
            self.connected_to = None
            return
        if host != self.connected_to:
            if self.connected_to is not None or host != self.brokers[0][0]:
                print(f"[{self.device}] connecté au broker {host}")
            self.connected_to = host
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
            if not self.offline:          # panne : l'ESP32 ne se réveille plus
                self.wake_once()
            stop.wait(self.period_s)  # deep sleep


def build_fleet(
    cfg: dict,
    fog: str,
    period_s: float,
    clock: SimClock,
    weather: list[dict],
    max_devices: int | None = None,
):
    port = cfg["mqtt"]["port"]
    brokers = [(cfg["mqtt"]["host"], port)] + [(h, port) for h in cfg["mqtt"].get("fallback_brokers", [])]
    sim_zones = [z for z in cfg["zones"] if z.get("simulated", True)]   # zones réelles (Wokwi) exclues
    zones = {
        z["id"]: Zone(
            z["id"],
            soil=bucket_from_config(cfg),
            clock=clock,
            weather=weather,
            area_m2=z.get("area_m2", 200.0),
            flow_lpm=z.get("flow_lpm", 20.0),
        )
        for z in sim_zones
    }
    devices = [
        VirtualESP32(fog, dev, zones[z["id"]], brokers, period_s, has_valve=(i == 0))
        for z in sim_zones
        for i, dev in enumerate(z["devices"])
    ]
    return zones, devices[:max_devices] if max_devices else devices
