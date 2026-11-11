"""What the fed-learning service and the irrigation engine share about the moisture model:
the input recipe and the prediction. Training lives in services/fed-learning.

Model: 12 normalised inputs -> 16 tanh -> 1 output = (moisture in 6 h - moisture now) / 10.
"""

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np

HORIZON_H = 6
FEATURES = ["moisture", "air_temp", "air_humidity", "hour_sin", "hour_cos", "et0_day",
            "kc", "fc", "wp", "root", "irrig_next", "rain_next"]
# échelles physiques fixes : tous les fogs normalisent pareil sans rien partager de leurs données
SCALE = np.array([40, 40, 100, 1, 1, 8, 1.2, 40, 20, 1000, 20, 20], dtype=float)
TARGET_SCALE = 10.0


def feature_vector(moisture: float, air_temp: float, air_humidity: float, t: datetime, et0_day: float,
                   kc: float, fc: float, wp: float, root: float, irrig_next: float = 0.0,
                   rain_next: float = 0.0) -> np.ndarray:
    h = t.hour + t.minute / 60
    return np.array([moisture, air_temp, air_humidity, math.sin(2 * math.pi * h / 24),
                     math.cos(2 * math.pi * h / 24), et0_day, kc, fc, wp, root,
                     irrig_next, rain_next], dtype=float) / SCALE


def predict(weights: list[np.ndarray], X: np.ndarray) -> np.ndarray:
    """Normalised output; multiply by TARGET_SCALE to get % vol."""
    h = np.tanh(X @ weights[0] + weights[1])
    return (h @ weights[2] + weights[3]).ravel()


def save_model(path: Path, weights: list[np.ndarray], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"weights": [w.tolist() for w in weights], **meta}), encoding="utf-8")


def load_model(path: Path) -> tuple[list[np.ndarray], dict] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return [np.array(w, dtype=float) for w in data.pop("weights")], data
