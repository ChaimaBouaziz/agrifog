"""Soil water balance of one zone (FAO-56 "bucket" model).

The root zone is a bucket of depth root_mm. Its water content theta (% vol) goes
    up with rain and irrigation, down with crop evapotranspiration (ETc = Ks * Kc * ET0).
Above field capacity the excess drains away (water lost). Below the readily
available water (RAW) the plant is stressed: Ks < 1, it transpires less, yield suffers.

    TAW = (FC - WP) * root_mm          total available water (mm)
    RAW = p * TAW                      readily available water (mm)
    depletion Dr = (FC - theta) * root_mm
    Ks = 1 if Dr <= RAW else (TAW - Dr) / (TAW - RAW)
"""

import math
from dataclasses import dataclass, field


def et0_rate_mm_h(et0_day_mm: float, hour: float) -> float:
    """Spread the daily ET0 over daylight (06h-18h) with a sine shape.

    The integral over 24 h equals et0_day_mm.
    """
    if not 6.0 <= hour <= 18.0:
        return 0.0
    return et0_day_mm * math.sin(math.pi * (hour - 6.0) / 12.0) * math.pi / 24.0


@dataclass
class SoilParams:
    fc_pct: float          # capacité au champ (% vol)
    wp_pct: float          # point de flétrissement (% vol)
    root_mm: float         # profondeur racinaire (mm)
    p: float               # fraction facilement utilisable (FAO-56, tableau 22)

    @property
    def taw_mm(self) -> float:
        return (self.fc_pct - self.wp_pct) / 100.0 * self.root_mm

    @property
    def raw_mm(self) -> float:
        return self.p * self.taw_mm

    @property
    def stress_threshold_pct(self) -> float:
        """Humidité en dessous de laquelle la plante commence à souffrir."""
        return self.fc_pct - self.raw_mm / self.root_mm * 100.0


@dataclass
class SoilBucket:
    params: SoilParams
    kc: float
    theta: float = field(default=None)  # % vol ; capacité au champ par défaut
    # compteurs cumulés (mm), utiles pour les expériences
    etc_mm: float = 0.0
    rain_mm: float = 0.0
    irrig_mm: float = 0.0
    drain_mm: float = 0.0
    stress_h: float = 0.0

    def __post_init__(self):
        if self.theta is None:
            self.theta = self.params.fc_pct

    def ks(self) -> float:
        p = self.params
        depletion = (p.fc_pct - self.theta) / 100.0 * p.root_mm
        if depletion <= p.raw_mm:
            return 1.0
        return max(0.0, (p.taw_mm - depletion) / (p.taw_mm - p.raw_mm))

    def step(self, dt_h: float, et0_mm_h: float, rain_mm: float = 0.0, irrig_mm: float = 0.0) -> None:
        p = self.params
        ks = self.ks()
        etc = ks * self.kc * et0_mm_h * dt_h
        self.theta += (rain_mm + irrig_mm - etc) / p.root_mm * 100.0

        drain = 0.0
        if self.theta > p.fc_pct:
            drain = (self.theta - p.fc_pct) / 100.0 * p.root_mm
            self.theta = p.fc_pct
        self.theta = max(self.theta, p.wp_pct)

        self.etc_mm += etc
        self.rain_mm += rain_mm
        self.irrig_mm += irrig_mm
        self.drain_mm += drain
        if ks < 1.0:
            self.stress_h += dt_h


def bucket_from_config(cfg: dict) -> SoilBucket:
    soil, crop = cfg["soil"], cfg["crop"]
    params = SoilParams(
        fc_pct=soil["field_capacity_pct"],
        wp_pct=soil["wilting_point_pct"],
        root_mm=crop["root_depth_mm"],
        p=crop["p"],
    )
    return SoilBucket(params, kc=crop["kc"])
