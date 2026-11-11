"""What the bot says and does — pure functions, no network (easy to test).

A command returns a Reply: the text to send back + the MQTT messages to publish.
"""

import time
from dataclasses import dataclass, field

from agrifog_common import topics

HELP = """🌱 AgriFog — {fog} ({crop})

Consulter
/etat — humidité, dernière décision et quota de chaque zone
/alertes — alertes en cours

Agir
/arroser z1 600 — arroser la zone z1 pendant 600 s (simulées)

Démo : injecter une panne dans le simulateur
/fuite z2 [L/min] · /pompe z1 · /capteur esp-a-02 35
/biais esp-a-05 -8 · /muet esp-a-09 · /pluie 20 · /reparer"""

ALERT_ICON = {"leak": "💧 FUITE", "pump": "⛔ POMPE", "no_rise": "🚱 ARROSAGE SANS EFFET",
              "sensor": "🔧 CAPTEUR", "offline": "📴 NŒUD MUET", "valve": "🚰 VANNE"}


@dataclass
class Reply:
    text: str
    publish: list[tuple[str, dict]] = field(default_factory=list)


@dataclass
class FarmView:
    """What the bot knows about the fog, from MQTT."""

    zones: dict[str, dict] = field(default_factory=dict)        # zone -> dernière décision
    alerts: dict[tuple, dict] = field(default_factory=dict)     # (type, zone/device) -> alerte
    budget: dict | None = None

    def on_decision(self, zone: str, msg: dict) -> None:
        self.zones[zone] = msg

    def on_alert(self, msg: dict) -> str:
        """Keep the list of active alerts up to date; returns the text to push to the farmer."""
        key = (msg.get("type"), msg.get("device") or msg.get("zone"))
        if msg.get("resolved"):
            self.alerts.pop(key, None)
            return f"✅ Résolu : {ALERT_ICON.get(msg.get('type'), msg.get('type'))} " \
                   f"{msg.get('zone') or ''} {msg.get('device') or ''}".strip()
        self.alerts[key] = msg
        return f"⚠️ {ALERT_ICON.get(msg.get('type'), msg.get('type', '').upper())}\n{msg.get('msg', '')}"

    def on_budget(self, msg: dict) -> str | None:
        first_of_day = self.budget is None or self.budget.get("day") != msg.get("day")
        self.budget = msg
        if first_of_day:
            return f"💧 Quota d'eau du {msg['day']} : {msg['allocated_l']:.0f} L ({msg.get('mode')})"
        if msg.get("received_l"):
            return f"🤝 Le voisin nous cède de l'eau : budget du jour {msg['allocated_l']:.0f} L"
        return None


def status_text(view: FarmView, fog: str) -> str:
    if not view.zones:
        return f"{fog} : pas encore de décision reçue (le moteur d'irrigation tourne-t-il ?)"
    lines = [f"📊 {fog}"]
    for zone, d in sorted(view.zones.items()):
        icon = "🚿" if d.get("action") == "water" or d.get("kind") == "in_progress" else "⏸️"
        lines.append(f"{icon} {zone} : {d.get('moisture', '?')} % — {d.get('reason', '')}")
    if view.budget:
        lines.append(f"💧 quota {view.budget['day']} : {view.budget['allocated_l']:.0f} L ({view.budget.get('mode')})")
    lines.append(f"⚠️ {len(view.alerts)} alerte(s) en cours" if view.alerts else "✅ aucune alerte")
    return "\n".join(lines)


def handle(text: str, fog: str, crop: str, view: FarmView) -> Reply:
    parts = text.strip().split()
    if not parts:
        return Reply("Tapez /aide")
    cmd, args = parts[0].lower().split("@")[0], parts[1:]
    fault = topics.sim_fault(fog)

    try:
        if cmd in ("/aide", "/help", "/start"):
            return Reply(HELP.format(fog=fog, crop=crop))
        if cmd == "/etat":
            return Reply(status_text(view, fog))
        if cmd == "/alertes":
            if not view.alerts:
                return Reply("✅ Aucune alerte en cours")
            return Reply("\n\n".join(f"⚠️ {a.get('msg')}" for a in view.alerts.values()))
        if cmd == "/arroser":
            zone, seconds = args[0], int(args[1]) if len(args) > 1 else 600
            cmd_id = f"{fog}-{zone}-telegram-{int(time.time())}"
            return Reply(f"🚿 Ordre envoyé : {zone} pendant {seconds} s",
                         [(topics.cmd(fog, zone), {"id": cmd_id, "action": "water", "seconds": seconds})])
        if cmd == "/fuite":
            lpm = float(args[1]) if len(args) > 1 else 6.0
            return Reply(f"💧 Fuite injectée sur {args[0]} ({lpm} L/min)",
                         [(fault, {"type": "leak", "zone": args[0], "lpm": lpm})])
        if cmd == "/pompe":
            return Reply(f"⛔ Pompe de {args[0]} mise en panne", [(fault, {"type": "pump_fail", "zone": args[0]})])
        if cmd == "/capteur":
            value = float(args[1]) if len(args) > 1 else 35.0
            return Reply(f"🔧 Capteur {args[0]} figé à {value} %",
                         [(fault, {"type": "sensor_stuck", "device": args[0], "value": value})])
        if cmd == "/biais":
            soil = float(args[1]) if len(args) > 1 else -8.0
            return Reply(f"🔧 Capteur {args[0]} faussé de {soil:+} %",
                         [(fault, {"type": "sensor_bias", "device": args[0], "soil": soil, "air": 0})])
        if cmd == "/muet":
            return Reply(f"📴 {args[0]} ne se réveillera plus", [(fault, {"type": "offline", "device": args[0]})])
        if cmd == "/pluie":
            mm = float(args[0]) if args else 20.0
            return Reply(f"🌧️ Prévision forcée : {mm} mm dans les 12 h",
                         [(fault, {"type": "rain_forecast", "mm": mm, "hours": 12})])
        if cmd == "/reparer":
            return Reply("🛠️ Toutes les pannes sont réparées", [(fault, {"type": "clear"})])
    except (IndexError, ValueError):
        return Reply(f"Commande incomplète. Exemple :\n{_example(cmd)}")
    return Reply("Commande inconnue. Tapez /aide")


def _example(cmd: str) -> str:
    return {"/arroser": "/arroser z1 600", "/fuite": "/fuite z2 6", "/pompe": "/pompe z1",
            "/capteur": "/capteur esp-a-02 35", "/biais": "/biais esp-a-05 -8",
            "/muet": "/muet esp-a-09"}.get(cmd, "/aide")
