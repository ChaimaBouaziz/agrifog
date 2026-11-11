"""Fault injection: break the virtual field on purpose, to test detection (and for the demo).

Send a JSON command on  {fog}/sim/fault  (or from the Telegram bot later):

    {"type": "leak",          "zone": "z2", "lpm": 6}        fuite : débit sans pompe
    {"type": "pump_fail",     "zone": "z1"}                  la vanne s'ouvre mais rien ne coule
    {"type": "sensor_stuck",  "device": "esp-a-02", "value": 35}   capteur d'humidité figé
    {"type": "sensor_bias",   "device": "esp-a-05", "soil": -8, "air": 6}   capteur faussé
    {"type": "offline",       "device": "esp-a-09"}          l'ESP32 ne se réveille plus
    {"type": "rain_forecast", "mm": 20, "hours": 12}         la météo annonce de la pluie
    {"type": "clear"}                                        tout réparer
Each fault (except rain_forecast) also accepts "on": false to repair only that one.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class ForecastOverride:
    mm: float = 0.0
    until: datetime | None = None

    def active(self, now: datetime) -> bool:
        return self.until is not None and now < self.until


def apply_fault(fault: dict, zones: dict, devices: dict, forecast: ForecastOverride,
                now: datetime) -> str:
    """Apply one fault command; returns a short description (or raises ValueError)."""
    kind = fault.get("type")
    on = fault.get("on", True)

    if kind == "clear":
        for z in zones.values():
            z.leak_lpm, z.pump_ok = 0.0, True
        for d in devices.values():
            d.stuck_value, d.soil_offset, d.air_offset, d.offline = None, 0.0, 0.0, False
        forecast.until = None
        return "toutes les pannes réparées"

    if kind in ("leak", "pump_fail"):
        z = zones.get(fault.get("zone"))
        if z is None:
            raise ValueError(f"zone inconnue : {fault.get('zone')}")
        if kind == "leak":
            z.leak_lpm = float(fault.get("lpm", 5)) if on else 0.0
            return f"fuite {z.id} : {z.leak_lpm} L/min"
        z.pump_ok = not on
        return f"pompe {z.id} : {'en panne' if on else 'réparée'}"

    if kind in ("sensor_stuck", "sensor_bias", "offline"):
        d = devices.get(fault.get("device"))
        if d is None:
            raise ValueError(f"ESP32 inconnu : {fault.get('device')}")
        if kind == "sensor_stuck":
            d.stuck_value = float(fault.get("value", d.zone.moisture)) if on else None
            return f"{d.device} : humidité figée à {d.stuck_value}"
        if kind == "sensor_bias":
            d.soil_offset = float(fault.get("soil", 0)) if on else 0.0
            d.air_offset = float(fault.get("air", 0)) if on else 0.0
            return f"{d.device} : biais sol {d.soil_offset:+} %, air {d.air_offset:+} °C"
        d.offline = on
        return f"{d.device} : {'muet' if on else 'de retour'}"

    if kind == "rain_forecast":
        forecast.mm = float(fault.get("mm", 15))
        forecast.until = now + timedelta(hours=float(fault.get("hours", 12)))
        return f"prévision forcée : {forecast.mm} mm jusqu'à {forecast.until:%m-%d %H:%M}"

    raise ValueError(f"type de panne inconnu : {kind}")
