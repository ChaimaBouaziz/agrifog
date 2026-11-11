import pytest

from app.allocation import (CropParams, ZoneNeedInput, allocate, evening_need_l, fog_need_l,
                            isolated_share, priority, spare_to_release, zone_need_mm)

TOMATO = CropParams(kc=1.05, threshold_pct=23, fc_pct=30, wp_pct=12, root_mm=600, sensitivity=1.0)
OLIVE = CropParams(kc=0.65, threshold_pct=11, fc_pct=18, wp_pct=7, root_mm=1000, sensitivity=0.5)


def test_need_is_what_keeps_the_zone_above_the_threshold_today():
    # tomate : seuil 23 % + 1 % de marge ; racines 600 mm ; ETc = 1.05 x 6 = 6.3 mm = 1.05 %
    assert zone_need_mm(24.0, TOMATO, et0_day=6, rain_mm=0) == pytest.approx(6.3)     # compenser l'ETc
    assert zone_need_mm(22.0, TOMATO, et0_day=6, rain_mm=0) == pytest.approx(12 + 6.3)  # + remonter de 2 %
    assert zone_need_mm(29.0, TOMATO, et0_day=6, rain_mm=0) == 0                        # assez d'eau en réserve
    assert zone_need_mm(24.0, TOMATO, et0_day=6, rain_mm=10) == 0                       # la pluie suffit


def test_fog_need_in_liters_capped_by_the_valve():
    zones = [ZoneNeedInput(24, 200)] * 3
    assert fog_need_l(zones, TOMATO, 6, 0) == pytest.approx(3 * 6.3 * 200)
    very_dry = [ZoneNeedInput(13, 200)]
    assert fog_need_l(very_dry, TOMATO, 6, 0) == 20 * 60 * 6           # 7200 L max par vanne et par jour


def test_stressed_tomato_has_higher_priority_than_comfortable_olive():
    assert priority([ZoneNeedInput(18, 200)], TOMATO) > priority([ZoneNeedInput(15, 200)], OLIVE)
    assert priority([ZoneNeedInput(25, 200)], TOMATO) == 1.0


def test_everyone_served_when_quota_is_enough():
    a = allocate(10000, {"fog-a": {"need_l": 4000, "priority": 1}, "fog-b": {"need_l": 2000, "priority": 0.5}})
    assert a == {"fog-a": 6000.0, "fog-b": 4000.0}   # besoin + 2000 L de marge chacun


def test_shortage_nobody_gets_less_than_its_equal_share():
    a = allocate(5000, {"fog-a": {"need_l": 6000, "priority": 2.0}, "fog-b": {"need_l": 3000, "priority": 0.5}})
    assert a == {"fog-a": 2500.0, "fog-b": 2500.0}   # les deux ont besoin de plus que leur moitié


def test_unneeded_share_goes_to_the_neighbour():
    a = allocate(5000, {"fog-a": {"need_l": 6000, "priority": 1.0}, "fog-b": {"need_l": 1500, "priority": 0.5}})
    assert a == {"fog-a": 3500.0, "fog-b": 1500.0}   # l'olivier n'a besoin que de 1500 L : 1000 L pour la tomate


def test_priority_splits_the_leftovers_between_several_needy_fogs():
    req = {"fog-a": {"need_l": 4000, "priority": 2.0}, "fog-b": {"need_l": 4000, "priority": 0.5},
           "fog-c": {"need_l": 500, "priority": 1.0}}
    a = allocate(6000, req)                            # part égale 2000 ; fog-c rend 1500 L
    assert a["fog-c"] == 500 and a["fog-a"] > a["fog-b"] > 2000
    assert sum(a.values()) == pytest.approx(6000)


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
    wet = [ZoneNeedInput(29, 200)] * 3              # sol bien humide à 10h
    assert evening_need_l(wet, TOMATO, et0_day=6, et_fraction_until_evening=0.8) == 0
    dry = [ZoneNeedInput(24, 200)] * 3               # à 18h il sera sous 24 % : il faudra arroser
    evening = evening_need_l(dry, TOMATO, et0_day=6, et_fraction_until_evening=0.8)
    assert evening > 0
    assert spare_to_release(allocated_l=3000, used_l=0, evening_l=0) == 3000
    assert spare_to_release(allocated_l=3000, used_l=2900, evening_l=evening) == 0
