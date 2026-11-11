import pytest

from app.allocation import (CropParams, ZoneNeedInput, allocate, evening_need_l, fog_need_l,
                            isolated_share, priority, spare_to_release, zone_need_mm)

TOMATO = CropParams(kc=1.05, threshold_pct=23, fc_pct=30, wp_pct=12, root_mm=600, sensitivity=1.0)
OLIVE = CropParams(kc=0.65, threshold_pct=11, fc_pct=18, wp_pct=7, root_mm=1000, sensitivity=0.5)


def test_need_counts_refill_etc_and_rain():
    assert zone_need_mm(29, TOMATO, et0_day=6, rain_mm=0) == pytest.approx(6.3)        # juste l'ETc
    assert zone_need_mm(26, TOMATO, et0_day=6, rain_mm=0) == pytest.approx(18 + 6.3)   # + remplir 3 %
    assert zone_need_mm(29, TOMATO, et0_day=6, rain_mm=10) == 0                         # la pluie suffit


def test_fog_need_in_liters():
    zones = [ZoneNeedInput(29, 200)] * 3
    assert fog_need_l(zones, TOMATO, 6, 0) == pytest.approx(3 * 6.3 * 200)


def test_stressed_tomato_has_higher_priority_than_comfortable_olive():
    assert priority([ZoneNeedInput(18, 200)], TOMATO) > priority([ZoneNeedInput(15, 200)], OLIVE)
    assert priority([ZoneNeedInput(25, 200)], TOMATO) == 1.0


def test_everyone_served_when_quota_is_enough():
    a = allocate(10000, {"fog-a": {"need_l": 4000, "priority": 1}, "fog-b": {"need_l": 2000, "priority": 0.5}})
    assert a == {"fog-a": 6000.0, "fog-b": 4000.0}   # besoin + 2000 L de marge chacun


def test_shortage_follows_need_and_priority():
    a = allocate(5000, {"fog-a": {"need_l": 6000, "priority": 2.0}, "fog-b": {"need_l": 3000, "priority": 0.5}})
    assert sum(a.values()) == pytest.approx(5000)
    assert a["fog-a"] > 5000 * 6000 / 9000          # la tomate stressée reçoit plus qu'au prorata


def test_a_fog_never_gets_more_than_it_needs():
    a = allocate(5000, {"fog-a": {"need_l": 1000, "priority": 3}, "fog-b": {"need_l": 8000, "priority": 0.5}})
    assert a["fog-a"] == 1000 and a["fog-b"] == pytest.approx(4000)


def test_allocation_is_identical_on_both_fogs():
    req = {"fog-b": {"need_l": 3100.4, "priority": 0.7}, "fog-a": {"need_l": 5120.9, "priority": 1.3}}
    req_other_order = dict(reversed(list(req.items())))
    assert allocate(5000, req) == allocate(5000, req_other_order)


def test_isolated_mode_takes_half():
    assert isolated_share(5000) == 2500


def test_release_spare_after_a_rainy_morning():
    zones = [ZoneNeedInput(29, 200)] * 3            # sol bien humide à 10h
    evening = evening_need_l(zones, TOMATO, et0_day=3, et_fraction_until_evening=0.8)
    assert spare_to_release(allocated_l=3000, used_l=0, evening_l=evening) > 1000
    assert spare_to_release(allocated_l=3000, used_l=2900, evening_l=evening) == 0
