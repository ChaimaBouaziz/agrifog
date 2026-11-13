"""Load the fog configuration (config/<FOG_ID>.yaml)."""

import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def fog_id() -> str:
    return os.environ.get("FOG_ID", "fog-a")


def load_config(fog: str | None = None) -> dict:
    fog = fog or fog_id()
    path = Path(os.environ.get("AGRIFOG_CONFIG_DIR", REPO_ROOT / "config")) / f"{fog}.yaml"
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Dans Docker, le broker s'appelle "mosquitto" : on surcharge par variable d'env.
    if os.environ.get("MQTT_HOST"):
        cfg["mqtt"]["host"] = os.environ["MQTT_HOST"]
    # Brokers de secours des ESP32 (les fogs voisins). Vide = aucun (ex. : cluster de test isolé).
    if "MQTT_FALLBACK" in os.environ:
        cfg["mqtt"]["fallback_brokers"] = [h for h in os.environ["MQTT_FALLBACK"].split(",") if h.strip()]
    return cfg
