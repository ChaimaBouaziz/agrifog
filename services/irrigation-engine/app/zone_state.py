"""What the engine knows about each zone, rebuilt from the telemetry stream."""

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .decision import PendingCheck
from .hargreaves import et0_hargreaves

DEFAULT_ET0 = 5.0           # mm/j, tant qu'on n'a pas une journée de températures
STALE = timedelta(hours=3)  # une mesure plus vieille n'est plus prise en compte


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))  # Python 3.10 ne lit pas "Z"


@dataclass
class DailyTemps:
    by_day: dict[date, list[float]] = field(default_factory=dict)  # jour -> [min, max]

    def add(self, t: datetime, temp: float) -> None:
        mm = self.by_day.setdefault(t.date(), [temp, temp])
        mm[0], mm[1] = min(mm[0], temp), max(mm[1], temp)

    def et0(self, today: date, lat: float) -> tuple[float, str]:
        """ET0 for today: from yesterday's full day if we have it, else from today so far."""
        for d, label in ((today - timedelta(days=1), "hier"), (today, "aujourd'hui")):
            mm = self.by_day.get(d)
            if mm and mm[1] - mm[0] >= 3:  # au moins quelques heures de mesures
                return et0_hargreaves(mm[0], mm[1], lat, today.timetuple().tm_yday), label
        return DEFAULT_ET0, "défaut"


@dataclass
class ZoneState:
    id: str
    moisture_by_device: dict[str, tuple[datetime, float]] = field(default_factory=dict)
    temps: DailyTemps = field(default_factory=DailyTemps)
    water_l_total: float | None = None
    last_ts: datetime | None = None
    watering_until: datetime | None = None
    cmd: dict | None = None                 # dernier ordre envoyé, en attente d'accusé
    valve_opened: datetime | None = None    # d'après l'accusé du boîtier
    valve_until: datetime | None = None
    pending: PendingCheck | None = None     # vérification du débitmètre
    rise_check: dict | None = None          # vérification de la hausse d'humidité
    last_decision: dict | None = None

    def update(self, msg: dict) -> datetime:
        t = parse_ts(msg["ts"])
        self.last_ts = t if self.last_ts is None else max(self.last_ts, t)
        self.moisture_by_device[msg["device"]] = (t, float(msg["soil_moisture"]))
        self.temps.add(t, float(msg["air_temp"]))
        if "water_l_total" in msg:
            self.water_l_total = float(msg["water_l_total"])
        return t

    def moisture(self) -> float | None:
        """Median of the recent readings: robust to one faulty sensor."""
        if self.last_ts is None:
            return None
        fresh = [v for t, v in self.moisture_by_device.values() if self.last_ts - t <= STALE]
        return statistics.median(fresh) if fresh else None
