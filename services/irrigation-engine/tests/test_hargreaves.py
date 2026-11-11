import pytest

from app.hargreaves import et0_hargreaves, extraterrestrial_radiation_mm


def test_ra_matches_fao56_example_8():
    # FAO-56, exemple 8 : latitude 20°S, 3 septembre (jour 246) -> Ra = 32.2 MJ m-2 j-1
    assert extraterrestrial_radiation_mm(-20, 246) / 0.408 == pytest.approx(32.2, abs=0.1)


def test_sfax_summer_day_is_plausible():
    et0 = et0_hargreaves(t_min=23, t_max=33, lat_deg=34.74, day_of_year=196)  # mi-juillet
    assert 5.5 < et0 < 7.5


def test_winter_needs_less_water_than_summer():
    assert et0_hargreaves(8, 17, 34.74, 15) < et0_hargreaves(23, 33, 34.74, 196) / 2


def test_no_temperature_range_gives_zero():
    assert et0_hargreaves(20, 20, 34.74, 100) == 0
