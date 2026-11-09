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
        return yaml.safe_load(f)
