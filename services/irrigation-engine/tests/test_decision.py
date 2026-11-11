from datetime import datetime, timedelta, timezone

import pytest

from app.decision import (Crop, PendingCheck, ZoneSpec, check_delivery, decide, et_fraction,
                          hours_to_next_window, in_cool_window)

TOMATO = Crop("tomate", kc=1.05, threshold_pct=23, fc_pct=30, root_mm=600)
ZONE = ZoneSpec("z1", area_m2=200, flow_lpm=20)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2025, 7, 1, hour, minute, tzinfo=timezone.utc)


def test_cool_windows():
    assert in_cool_window(6) and in_cool_window(19)
    assert not in_cool_window(12) and not in_cool_window(23)
    assert hours_to_next_window(6) == 12        # 6h -> 18h
    assert hours_to_next_window(19) == 10       # 19h -> 5h


def test_et_fraction():
    assert et_fraction(6, 12) == pytest.approx(1.0)     # toute la journée
    assert et_fraction(18, 11) == pytest.approx(0.0)    # la nuit
    assert et_fraction(6, 6) == pytest.approx(0.5)      # le matin = la moitié


def test_wait_outside_cool_hours():
    d = decide(24.0, at(13), 6.0, TOMATO, ZONE)
    assert d.action == "wait" and "hors heures fraîches" in d.reason


def test_morning_anticipation_waters_before_the_hot_day():
    # 24.5 % le matin, mais 6.3 mm d'ETc dans la journée -> 23.45 % le soir : trop juste
    d = decide(24.5, at(6), 6.0, TOMATO, ZONE)
    assert d.action == "water"
    # besoin 27 mm (remonter à CC - 1 %), mais 2 h de fenêtre à 20 L/min = 12 mm max
    assert d.dose_mm == pytest.approx(min((29 - 24.5) / 100 * 600, 20 * 60 * 2 / 200))
    assert d.seconds == 2 * 3600


def test_morning_no_need_when_the_day_is_covered():
    d = decide(25.0, at(6), 6.0, TOMATO, ZONE)   # 23.95 % prévu le soir : encore OK
    assert d.action == "wait"


def test_morning_no_need_when_soil_is_wet():
    d = decide(28.5, at(6), 6.0, TOMATO, ZONE)
    assert d.action == "wait" and "pas besoin" in d.reason


def test_evening_no_need_because_night_has_no_et():
    d = decide(24.0, at(19), 6.0, TOMATO, ZONE)
    assert d.action == "wait"


def test_emergency_waters_even_at_noon():
    d = decide(20.0, at(13), 6.0, TOMATO, ZONE)
    assert d.action == "water" and "URGENCE" in d.reason


def test_rain_postpones_watering():
    d = decide(24.5, at(6), 6.0, TOMATO, ZONE, rain_expected_mm=15)
    assert d.action == "wait" and "pluie" in d.reason


def test_no_double_watering():
    d = decide(22.0, at(6, 30), 6.0, TOMATO, ZONE, watering_until=at(6, 50))
    assert d.action == "wait" and "en cours" in d.reason
    d = decide(22.0, at(7, 10), 6.0, TOMATO, ZONE, watering_until=at(6, 50))
    assert d.action == "wait" and "attend" in d.reason


def test_flowmeter_check():
    p = PendingCheck("c1", start_total_l=1000, expected_l=480, check_after=at(7))
    assert check_delivery(p, at(6), 1200) is None
    assert check_delivery(p, at(7), 1480)[0] == "ok"
    assert check_delivery(p, at(7), 1050)[0] == "fail"


def test_watering_is_limited_to_the_cool_window():
    d = decide(22.0, at(7, 30), 6.0, TOMATO, ZONE)     # il reste 30 min de fenêtre
    assert d.action == "water"
    assert d.seconds == 30 * 60
    assert "limité" in d.reason


def test_emergency_watering_is_limited_to_one_hour():
    d = decide(15.0, at(13), 6.0, TOMATO, ZONE)
    assert d.seconds == 3600
