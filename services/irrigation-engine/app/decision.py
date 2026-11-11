"""Irrigation decision for one zone — pure functions, no MQTT, easy to test.

Every decision comes with a human-readable reason (shown on the dashboard / Telegram).

Rules, in order:
  1. a watering is in progress, or just finished (sensors not updated yet)  -> wait
  2. soil far below the stress threshold                                     -> water NOW (urgence)
  3. outside the cool hours (5-8h, 18-21h)                                   -> wait
  4. will the soil stay above the threshold until the next cool window?
     (Hargreaves ET0 x Kc over the hours to come)                            -> yes: wait
  5. enough rain expected                                                    -> wait (report)
  6. otherwise water, refilling up to just below field capacity (no drainage)

Times are the SIMULATED times found in the telemetry `ts` (hours = local solar time).
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

COOL_WINDOWS = [(5, 8), (18, 21)]   # heures fraîches : moins d'évaporation
URGENT_BELOW_PCT = 2.0              # urgence si humidité < seuil - 2 %
REFILL_MARGIN_PCT = 1.0             # on remplit jusqu'à CC - 1 % pour ne pas drainer
SAFETY_MARGIN_PCT = 0.5             # marge sur la prévision
MIN_DOSE_MM = 1.0                   # en dessous, ça ne vaut pas la peine d'ouvrir la vanne
SETTLE = timedelta(hours=1)         # attendre les nouvelles mesures après un arrosage
URGENT_MAX_H = 1.0                  # en urgence, on arrose au plus 1 h puis on réévalue


@dataclass
class Crop:
    name: str
    kc: float
    threshold_pct: float
    fc_pct: float
    root_mm: float


@dataclass
class ZoneSpec:
    id: str
    area_m2: float
    flow_lpm: float


@dataclass
class Decision:
    action: str          # "water" | "wait"
    reason: str
    kind: str = ""       # catégorie stable (pour n'afficher que les changements)
    seconds: int = 0
    liters: float = 0.0
    dose_mm: float = 0.0


def hour_of(t: datetime) -> float:
    return t.hour + t.minute / 60


def in_cool_window(h: float) -> bool:
    return any(a <= h < b for a, b in COOL_WINDOWS)


def window_hours_left(h: float) -> float:
    """Hours left in the current cool window (0 if outside)."""
    for a, b in COOL_WINDOWS:
        if a <= h < b:
            return b - h
    return 0.0


def hours_to_next_window(h: float) -> float:
    """Hours from h to the START of the next cool window (not the current one)."""
    starts = sorted(a for a, _ in COOL_WINDOWS)
    for s in starts:
        if s > h:
            return s - h
    return 24 - h + starts[0]


def et_fraction(h_from: float, hours: float) -> float:
    """Share of the daily ET that happens between h_from and h_from+hours.

    Same daylight sine shape as the simulator (06h-18h, integral = 1).
    """
    def cumulative(h: float) -> float:  # part de l'ETj écoulée entre 0h et h (0..1)
        h = h % 24
        if h <= 6:
            return 0.0
        if h >= 18:
            return 1.0
        return (1 - math.cos(math.pi * (h - 6) / 12)) / 2

    days, rest = divmod(hours, 24)
    end = h_from + rest
    frac = cumulative(end) - cumulative(h_from) if end < 24 else (1 - cumulative(h_from)) + cumulative(end - 24)
    return days + frac


def decide(moisture: float, now: datetime, et0_day: float, crop: Crop, zone: ZoneSpec,
           rain_expected_mm: float = 0.0, watering_until: datetime | None = None) -> Decision:
    h = hour_of(now)
    target = crop.fc_pct - REFILL_MARGIN_PCT

    if watering_until is not None and now < watering_until + SETTLE:
        if now < watering_until:
            return Decision("wait", "arrosage en cours", "in_progress")
        return Decision("wait", "arrosage terminé, on attend les nouvelles mesures", "settle")

    urgent = moisture < crop.threshold_pct - URGENT_BELOW_PCT
    if not urgent:
        if not in_cool_window(h):
            nxt = hours_to_next_window(h)
            return Decision("wait", f"hors heures fraîches (prochaine fenêtre dans {nxt:.1f} h), "
                                    f"humidité {moisture:.1f} %", "off_hours")
        nxt = hours_to_next_window(h)
        loss_mm = crop.kc * et0_day * et_fraction(h, nxt)
        predicted = moisture - loss_mm / crop.root_mm * 100
        if predicted >= crop.threshold_pct + SAFETY_MARGIN_PCT:
            return Decision("wait", f"pas besoin : {moisture:.1f} % maintenant, {predicted:.1f} % prévu "
                                    f"dans {nxt:.0f} h (seuil {crop.threshold_pct} %, ETc {loss_mm:.1f} mm)", "no_need")

    dose_mm = max(0.0, (target - moisture) / 100 * crop.root_mm)
    if dose_mm < MIN_DOSE_MM:
        return Decision("wait", f"dose trop faible ({dose_mm:.1f} mm)", "small_dose")
    if not urgent and rain_expected_mm >= 0.5 * dose_mm:
        return Decision("wait", f"arrosage reporté : {rain_expected_mm:.1f} mm de pluie prévus "
                                f"(besoin {dose_mm:.1f} mm)", "rain")

    # on n'arrose que pendant le temps qui reste dans la fenêtre (ou 1 h en urgence) :
    # le complément éventuel viendra à la fenêtre suivante
    max_h = URGENT_MAX_H if urgent else window_hours_left(h)
    max_mm = zone.flow_lpm * 60 * max_h / zone.area_m2
    capped = dose_mm > max_mm
    dose_mm = min(dose_mm, max_mm)
    liters = dose_mm * zone.area_m2
    seconds = int(round(liters / zone.flow_lpm * 60))
    why = "URGENCE" if urgent else "fenêtre fraîche"
    return Decision("water",
                    f"{why} : humidité {moisture:.1f} % (seuil {crop.threshold_pct} %), "
                    f"apport {dose_mm:.1f} mm = {liters:.0f} L en {seconds // 60} min"
                    + (" (limité à la fenêtre, complément plus tard)" if capped else
                       f" pour remonter à {target:.0f} %"),
                    "urgent" if urgent else "water",
                    seconds=seconds, liters=liters, dose_mm=dose_mm)


# ---------------------------------------------------------------- flowmeter check

@dataclass
class PendingCheck:
    cmd_id: str
    start_total_l: float
    expected_l: float
    check_after: datetime


def check_delivery(p: PendingCheck, now: datetime, total_l: float) -> tuple[str, str] | None:
    """None while it's too early; ("ok"|"fail", message) once the watering should be over."""
    if now < p.check_after:
        return None
    delivered = total_l - p.start_total_l
    if delivered >= 0.7 * p.expected_l:
        return "ok", f"{delivered:.0f} L mesurés pour {p.expected_l:.0f} L attendus"
    return "fail", (f"débit insuffisant : {delivered:.0f} L mesurés pour {p.expected_l:.0f} L "
                    f"attendus (pompe en panne ou réservoir vide ?)")
