"""Turn the fog's MQTT traffic into Prometheus metrics (pure logic, no network: easy to test).

The exporter only LISTENS: it never changes the behaviour of the other services.
Every metric carries a `fog` label so that the same dashboards work on fog-a and fog-b.

Times: `ts` in the messages is SIMULATED time; Prometheus works in real time. "Last seen"
metrics therefore use the real reception time (`now`), which is what an operator cares about.
"""

from __future__ import annotations

import time
from typing import Callable

from prometheus_client import CollectorRegistry, Counter, Gauge

from agrifog_common import topics

Clock = Callable[[], float]


class FogMetrics:
    def __init__(self, cfg: dict, fog: str, registry: CollectorRegistry | None = None,
                 clock: Clock = time.time):
        self.fog, self.cfg, self.clock = fog, cfg, clock
        self.neighbour = cfg.get("neighbor", "fog-b" if fog == "fog-a" else "fog-a")
        self.registry = registry or CollectorRegistry()
        r = self.registry
        z, d = ["fog", "zone"], ["fog", "zone", "device"]

        # --- field (telemetry) -----------------------------------------------------
        self.soil = Gauge("agrifog_soil_moisture_pct", "Humidité du sol mesurée (% vol)", d, registry=r)
        self.air_temp = Gauge("agrifog_air_temp_celsius", "Température de l'air (°C)", d, registry=r)
        self.air_hum = Gauge("agrifog_air_humidity_pct", "Humidité de l'air (%)", d, registry=r)
        self.flow = Gauge("agrifog_flow_lpm", "Débit mesuré par le débitmètre (L/min)", d, registry=r)
        self.water = Gauge("agrifog_water_total_liters", "Compteur cumulé du débitmètre (L)", d, registry=r)
        self.threshold = Gauge("agrifog_moisture_threshold_pct", "Seuil de stress de la culture (%)", z, registry=r)
        self.field_capacity = Gauge("agrifog_field_capacity_pct", "Capacité au champ du sol (%)", z, registry=r)

        # --- decisions and commands (irrigation-engine) ------------------------------
        self.decisions = Counter("agrifog_decisions_total", "Décisions publiées", z + ["action", "kind"], registry=r)
        self.last_moisture = Gauge("agrifog_decision_moisture_pct", "Humidité retenue par la dernière décision", z, registry=r)
        self.ai_6h = Gauge("agrifog_ai_moisture_6h_pct", "Prédiction de l'IA fédérée : humidité dans 6 h", z, registry=r)
        self.et0 = Gauge("agrifog_et0_mm_per_day", "ET0 utilisée par la dernière décision (mm/j)", z, registry=r)
        self.commands = Counter("agrifog_water_commands_total", "Ordres d'arrosage envoyés", z, registry=r)
        self.cmd_seconds = Counter("agrifog_water_commanded_seconds_total", "Durée d'arrosage commandée (s simulées)", z, registry=r)
        self.valve_acks = Counter("agrifog_valve_acks_total", "Accusés d'ouverture de vanne", z, registry=r)

        # --- alerts (irrigation-engine) ----------------------------------------------
        self.alert_active = Gauge("agrifog_alert_active", "Alerte en cours (1) ou résolue (0)",
                                  ["fog", "type", "zone", "device"], registry=r)
        self.alerts = Counter("agrifog_alerts_total", "Alertes levées", ["fog", "type"], registry=r)

        # --- devices (heartbeat) -----------------------------------------------------
        dv = ["fog", "device"]
        self.battery = Gauge("agrifog_device_battery_volts", "Tension de la batterie (V)", dv, registry=r)
        self.rssi = Gauge("agrifog_device_rssi_dbm", "Force du signal WiFi (dBm)", dv, registry=r)
        self.uptime = Gauge("agrifog_device_uptime_seconds", "Temps de fonctionnement annoncé (s)", dv, registry=r)
        self.last_seen = Gauge("agrifog_device_last_seen_timestamp_seconds", "Dernier message reçu (temps réel)", dv, registry=r)
        self.fw = Gauge("agrifog_device_firmware_info", "Version du firmware (valeur 1)", dv + ["version"], registry=r)

        # --- quota (quota-negotiator, irrigation-engine) -----------------------------
        self.q_alloc = Gauge("agrifog_quota_allocated_liters", "Litres attribués aujourd'hui", ["fog"], registry=r)
        self.q_used = Gauge("agrifog_quota_used_liters", "Litres utilisés aujourd'hui", ["fog"], registry=r)
        self.q_given = Gauge("agrifog_quota_given_liters", "Litres cédés au voisin aujourd'hui", ["fog"], registry=r)
        self.q_received = Gauge("agrifog_quota_received_liters", "Litres reçus du voisin aujourd'hui", ["fog"], registry=r)
        self.q_negotiated = Gauge("agrifog_quota_negotiated", "1 = répartition négociée, 0 = mode isolé", ["fog"], registry=r)
        self.q_daily = Gauge("agrifog_quota_daily_liters", "Quota journalier du domaine (L)", ["fog"], registry=r)
        self.q_view = Gauge("agrifog_quota_allocation_liters",
                            "Répartition publiée par chaque fog : les deux doivent être identiques",
                            ["computed_by", "fog"], registry=r)
        self.q_request = Gauge("agrifog_quota_request_liters", "Besoin annoncé par chaque fog", ["fog"], registry=r)

        # --- cooperation between fogs ------------------------------------------------
        self.bridge_up = Gauge("agrifog_bridge_up", "Pont MQTT connecté (1) ou non (0)", ["fog", "connection"], registry=r)
        self.peer_last_seen = Gauge("agrifog_fog_last_seen_timestamp_seconds",
                                    "Dernier signe de vie de chaque fog (temps réel)", ["fog"], registry=r)
        self.peer_up = Gauge("agrifog_fog_alive", "État annoncé par chaque fog (1 vivant)", ["fog"], registry=r)
        self.fl_round = Gauge("agrifog_fl_round", "Dernier tour d'apprentissage fédéré envoyé", ["fog"], registry=r)
        self.fl_mae = Gauge("agrifog_fl_mae_pct", "Erreur moyenne locale du modèle fédéré (% vol)", ["fog"], registry=r)

        self.messages = Counter("agrifog_mqtt_messages_total", "Messages MQTT reçus par l'exporter", ["fog", "kind"], registry=r)

        self.zone_of_device: dict[str, str] = {}
        for zone in cfg.get("zones", []):
            self.threshold.labels(fog, zone["id"]).set(cfg["crop"]["moisture_threshold_pct"])
            self.field_capacity.labels(fog, zone["id"]).set(cfg["soil"]["field_capacity_pct"])
            for dev in zone.get("devices", []):
                self.zone_of_device[dev] = zone["id"]
        self.q_daily.labels(fog).set(cfg.get("domain", {}).get("daily_quota_l", 0))

    # ------------------------------------------------------------------ routing
    def subscriptions(self) -> list[str]:
        f = self.fog
        return [topics.telemetry(f, "+"), topics.decision(f, "+"), topics.cmd(f, "+"), topics.valve(f, "+"),
                topics.heartbeat(f, "+"), topics.alert(f), topics.quota_budget(f), topics.quota_usage(f),
                "domaine/quota/allocation/+", "domaine/quota/request/+", "domaine/fog/+/status",
                "domaine/fl/update/+", "$SYS/broker/connection/+/state"]

    def handle(self, topic: str, payload) -> str | None:
        """Update the metrics for one message. Returns the kind of message (or None if ignored)."""
        p, f, now = topic.split("/"), self.fog, self.clock()
        kind = None
        if topic.startswith("$SYS/broker/connection/"):
            kind = self._bridge(p[3], payload)
        elif p[0] == f and len(p) == 4 and p[1] == "zone":
            kind = {"telemetry": self._telemetry, "decision": self._decision, "cmd": self._cmd,
                    "valve": self._valve}.get(p[3], lambda *a: None)(p[2], payload, now)
        elif p[0] == f and len(p) == 4 and p[1] == "device" and p[3] == "heartbeat":
            kind = self._heartbeat(p[2], payload, now)
        elif topic == topics.alert(f):
            kind = self._alert(payload)
        elif topic == topics.quota_budget(f):
            kind = self._budget(payload)
        elif topic == topics.quota_usage(f):
            self.q_used.labels(f).set(float(payload["used_l"]))
            kind = "quota_usage"
        elif p[0] == "domaine":
            kind = self._shared(p, payload, now)
        if kind:
            self.messages.labels(f, kind).inc()
        return kind

    # ----------------------------------------------------------------- handlers
    def _zone(self, zone: str, device: str | None) -> str:
        return zone or self.zone_of_device.get(device or "", "?")

    def _telemetry(self, zone: str, m: dict, now: float) -> str:
        dev = m.get("device", "?")
        lbl = (self.fog, zone, dev)
        self.soil.labels(*lbl).set(float(m["soil_moisture"]))
        if m.get("air_temp") is not None:
            self.air_temp.labels(*lbl).set(float(m["air_temp"]))
        if m.get("air_humidity") is not None:
            self.air_hum.labels(*lbl).set(float(m["air_humidity"]))
        self.flow.labels(*lbl).set(float(m.get("flow_lpm") or 0))
        self.water.labels(*lbl).set(float(m.get("water_l_total") or 0))
        self.last_seen.labels(self.fog, dev).set(now)
        return "telemetry"

    def _decision(self, zone: str, m: dict, now: float) -> str:
        self.decisions.labels(self.fog, zone, m.get("action", "?"), m.get("kind") or "?").inc()
        if m.get("moisture") is not None:
            self.last_moisture.labels(self.fog, zone).set(float(m["moisture"]))
        if m.get("et0") is not None:
            self.et0.labels(self.fog, zone).set(float(m["et0"]))
        if m.get("ai_moisture_6h") is not None:
            self.ai_6h.labels(self.fog, zone).set(float(m["ai_moisture_6h"]))
        return "decision"

    def _cmd(self, zone: str, m: dict, now: float) -> str | None:
        if m.get("action") != "water":
            return None
        self.commands.labels(self.fog, zone).inc()
        self.cmd_seconds.labels(self.fog, zone).inc(float(m.get("seconds") or 0))
        return "cmd"

    def _valve(self, zone: str, m: dict, now: float) -> str:
        self.valve_acks.labels(self.fog, zone).inc()
        return "valve"

    def _heartbeat(self, dev: str, m: dict, now: float) -> str:
        lbl = (self.fog, dev)
        for gauge, key in ((self.battery, "battery_v"), (self.rssi, "rssi"), (self.uptime, "uptime_s")):
            if m.get(key) is not None:
                gauge.labels(*lbl).set(float(m[key]))
        if m.get("fw_version"):
            self.fw.labels(*lbl, str(m["fw_version"])).set(1)
        self.last_seen.labels(*lbl).set(now)
        return "heartbeat"

    def _alert(self, m: dict) -> str:
        t, dev = m.get("type", "?"), m.get("device") or ""
        zone = m.get("zone") or self.zone_of_device.get(dev, "")
        if m.get("resolved"):
            self.alert_active.labels(self.fog, t, zone, dev).set(0)
        else:
            self.alert_active.labels(self.fog, t, zone, dev).set(1)
            self.alerts.labels(self.fog, t).inc()
        return "alert"

    def _budget(self, m: dict) -> str:
        f = self.fog
        self.q_alloc.labels(f).set(float(m.get("allocated_l") or 0))
        self.q_given.labels(f).set(float(m.get("given_l") or 0))
        self.q_received.labels(f).set(float(m.get("received_l") or 0))
        self.q_negotiated.labels(f).set(1 if str(m.get("mode", "")).startswith("négoci") else 0)
        return "quota_budget"

    def _bridge(self, connection: str, payload) -> str:
        state = payload if isinstance(payload, (int, float)) else str(payload).strip()
        self.bridge_up.labels(self.fog, connection.split(".", 1)[-1]).set(1 if str(state) == "1" else 0)
        return "bridge_state"

    def _shared(self, p: list[str], m: dict, now: float) -> str | None:
        # domaine/quota/allocation/<fog>, domaine/quota/request/<fog>, domaine/fog/<fog>/status, domaine/fl/update/<fog>
        if p[1] == "quota" and p[2] == "allocation":
            for fog, liters in (m.get("allocation") or {}).items():
                self.q_view.labels(p[3], fog).set(float(liters))
            self.peer_last_seen.labels(p[3]).set(now)
            return "quota_allocation"
        if p[1] == "quota" and p[2] == "request":
            self.q_request.labels(p[3]).set(float(m.get("need_l") or 0))
            self.peer_last_seen.labels(p[3]).set(now)
            return "quota_request"
        if p[1] == "fog" and len(p) == 4 and p[3] == "status":
            alive = bool(m.get("alive"))
            self.peer_up.labels(p[2]).set(1 if alive else 0)
            if alive:   # heure d'envoi réelle : un ancien message "retained" ne fait pas croire le voisin vivant
                self.peer_last_seen.labels(p[2]).set(min(now, float(m.get("unix", now))))
            return "fog_status"
        if p[1] == "fl" and p[2] == "update":
            self.fl_round.labels(p[3]).set(float(m.get("round") or 0))
            if m.get("mae") is not None:
                self.fl_mae.labels(p[3]).set(float(m["mae"]))
            self.peer_last_seen.labels(p[3]).set(now)
            return "fl_update"
        return None
