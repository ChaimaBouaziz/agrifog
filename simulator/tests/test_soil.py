import pytest

from agrifog_common import load_config
from simulator.soil import SoilBucket, SoilParams, bucket_from_config, et0_rate_mm_h

PARAMS = SoilParams(fc_pct=30, wp_pct=12, root_mm=600, p=0.4)  # TAW 108 mm, RAW 43.2 mm


def test_daily_et0_integrates_to_daily_value():
    dt = 0.01
    total = sum(et0_rate_mm_h(6.0, i * dt) * dt for i in range(int(24 / dt)))
    assert total == pytest.approx(6.0, rel=1e-3)
    assert et0_rate_mm_h(6.0, 2) == 0  # pas d'évaporation la nuit


def test_taw_raw_and_threshold():
    assert PARAMS.taw_mm == pytest.approx(108)
    assert PARAMS.raw_mm == pytest.approx(43.2)
    assert PARAMS.stress_threshold_pct == pytest.approx(22.8)


def test_soil_dries_with_et():
    b = SoilBucket(PARAMS, kc=1.0)
    b.step(10, et0_mm_h=0.6)               # 6 mm évapotranspirés
    assert b.theta == pytest.approx(30 - 1.0)  # 6 mm / 600 mm = 1 %


def test_excess_water_drains_above_field_capacity():
    b = SoilBucket(PARAMS, kc=1.0)
    b.step(1, et0_mm_h=0, irrig_mm=12)
    assert b.theta == 30
    assert b.drain_mm == pytest.approx(12)


def test_stress_reduces_et_and_is_counted():
    b = SoilBucket(PARAMS, kc=1.0, theta=18)  # sous le seuil de 22.8 %
    assert b.ks() < 1
    b.step(1, et0_mm_h=1.0)
    assert b.etc_mm < 1.0
    assert b.stress_h == 1


def test_water_balance_is_conserved():
    b = SoilBucket(PARAMS, kc=1.05, theta=25)
    start_mm = b.theta / 100 * PARAMS.root_mm
    for h in range(48):
        b.step(1, et0_mm_h=et0_rate_mm_h(7, h % 24), rain_mm=0.2, irrig_mm=5 if h == 7 else 0)
    end_mm = b.theta / 100 * PARAMS.root_mm
    assert end_mm - start_mm == pytest.approx(b.rain_mm + b.irrig_mm - b.etc_mm - b.drain_mm)


def test_both_crops_have_consistent_threshold():
    for fog in ("fog-a", "fog-b"):
        cfg = load_config(fog)
        b = bucket_from_config(cfg)
        p = b.params
        assert p.wp_pct < p.stress_threshold_pct < p.fc_pct
        assert cfg["crop"]["moisture_threshold_pct"] == pytest.approx(p.stress_threshold_pct, abs=0.5)
