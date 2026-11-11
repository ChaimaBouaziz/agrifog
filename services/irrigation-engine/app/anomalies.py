"""Leak and anomaly detection — pure functions + a small alert book.

| Situation observée                          | Diagnostic                                  |
|---------------------------------------------|---------------------------------------------|
| vanne fermée mais débit > 0                 | fuite                                       |
| vanne ouverte mais débit nul                | pompe en panne ou réservoir vide            |
| humidité qui ne monte pas après l'arrosage  | tuyau bouché, ou capteur défectueux         |
| plus de heartbeat d'un ESP32                | nœud hors service                           |
"""

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

FLOW_EPS_LPM = 0.5                 # en dessous, on considère que rien ne coule
VALVE_GRACE = timedelta(minutes=2)  # tolérance à l'ouverture / fermeture de la vanne
OFFLINE_AFTER = timedelta(hours=3)  # ~3 réveils manqués
RISE_CHECK_AFTER = timedelta(hours=2)
MIN_RISE_RATIO = 0.3               # on attend au moins 30 % de la hausse théorique


def valve_should_be_open(now: datetime, opened: datetime | None, until: datetime | None) -> bool:
    return opened is not None and until is not None and opened <= now < until


def detect_leak(flow_lpm: float, now: datetime, opened: datetime | None, until: datetime | None) -> bool:
    if flow_lpm <= FLOW_EPS_LPM:
        return False
    if opened is None or until is None:
        return True
    return not (opened - VALVE_GRACE <= now < until + VALVE_GRACE)


def detect_pump_failure(flow_lpm: float, now: datetime, opened: datetime | None,
                        until: datetime | None) -> bool:
    return (valve_should_be_open(now, opened, until) and now >= opened + VALVE_GRACE
            and until - now > VALVE_GRACE and flow_lpm <= FLOW_EPS_LPM)


def offline_devices(last_seen: dict[str, datetime], now: datetime) -> list[str]:
    return sorted(d for d, t in last_seen.items() if now - t > OFFLINE_AFTER)


def check_rise(before: dict[str, float], after: dict[str, float], expected_pct: float):
    """Compare moisture before/after a watering.

    Returns ("blocked", msg) if the whole zone did not react, ("sensor", device, msg)
    for each sensor that did not react while the others did, or [] if all is fine.
    """
    rises = {d: after[d] - before[d] for d in before if d in after}
    if not rises or expected_pct <= 0:
        return []
    need = MIN_RISE_RATIO * expected_pct
    median = statistics.median(rises.values())
    if median < need:
        return [("blocked", None, f"l'humidité ne monte pas (+{median:.1f} % pour +{expected_pct:.1f} % "
                                  f"attendus) : tuyau bouché ou capteurs défectueux ?")]
    return [("sensor", d, f"le capteur {d} ne réagit pas à l'arrosage (+{r:.1f} %, "
                          f"les autres +{median:.1f} %) : capteur défectueux ?")
            for d, r in sorted(rises.items()) if r < need]


@dataclass
class AlertBook:
    """Raise each alert once, and say when it is resolved."""

    active: dict[tuple, dict] = field(default_factory=dict)

    def raise_(self, key: tuple, payload: dict) -> dict | None:
        if key in self.active:
            return None
        self.active[key] = payload
        return {**payload, "resolved": False}

    def resolve(self, key: tuple, ts: str) -> dict | None:
        old = self.active.pop(key, None)
        return None if old is None else {**old, "ts": ts, "resolved": True}
