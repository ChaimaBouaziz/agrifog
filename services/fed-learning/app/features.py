"""From a fog's local history to (X, y) samples — same recipe on every fog.

Task: predict the soil moisture in HORIZON_H hours, given the situation now.
The crop/soil parameters are inputs too, so ONE global model can serve tomato and olive.

Normalisation uses FIXED physical scales (not statistics of the data): every fog
scales its inputs the same way without having to share anything about its data.
"""

import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from agrifog_common.ml import FEATURES, HORIZON_H, SCALE, TARGET_SCALE, feature_vector  # noqa: F401


def et0_hargreaves(t_min: float, t_max: float, lat_deg: float, doy: int) -> float:
    """Same formula as the irrigation engine (FAO-56 eq. 52)."""
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi / 365 * doy)
    delta = 0.409 * math.sin(2 * math.pi / 365 * doy - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    ra = 0.408 * (24 * 60 / math.pi) * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))
    t_mean = (t_min + t_max) / 2
    return max(0.0, 0.0023 * ra * (t_mean + 17.8) * math.sqrt(max(0.0, t_max - t_min)))


@dataclass
class CropInfo:
    kc: float
    fc: float
    wp: float
    root: float
    lat: float

    @classmethod
    def from_config(cls, cfg: dict) -> "CropInfo":
        return cls(cfg["crop"]["kc"], cfg["soil"]["field_capacity_pct"], cfg["soil"]["wilting_point_pct"],
                   cfg["crop"]["root_depth_mm"], cfg["location"]["latitude"])


def load_history(path: Path) -> dict[str, list[dict]]:
    """{zone: hourly rows sorted by time}"""
    zones: dict[str, list[dict]] = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            zones[r["zone"]].append(r)
    for rows in zones.values():
        rows.sort(key=lambda r: r["ts"])
    return dict(zones)


def zone_samples(rows: list[dict], crop: CropInfo) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for i in range(len(rows) - HORIZON_H):
        r, future = rows[i], rows[i + HORIZON_H]
        t = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
        et0 = et0_hargreaves(float(r["t_min"]), float(r["t_max"]), crop.lat, t.timetuple().tm_yday)
        window = rows[i + 1:i + 1 + HORIZON_H]
        irrig = sum(float(w["irrig_mm"]) for w in window)
        rain = sum(float(w["rain_mm"]) for w in window)
        X.append(feature_vector(float(r["moisture"]), float(r["air_temp"]), float(r["air_humidity"]),
                                t, et0, crop.kc, crop.fc, crop.wp, crop.root, irrig, rain))
        y.append((float(future["moisture"]) - float(r["moisture"])) / TARGET_SCALE)
    return np.array(X), np.array(y)


def split_by_time(X: np.ndarray, y: np.ndarray, train_frac: float = 0.5):
    """First days to train, last days to test (no peeking into the future)."""
    n = int(len(X) * train_frac)
    return (X[:n], y[:n]), (X[n:], y[n:])
