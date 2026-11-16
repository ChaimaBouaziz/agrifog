"""Outils communs aux expériences de la plateforme (pompe commune, fog jumeau, OTA).

Comme fieldsim.py : on charge le VRAI code des services (pas une copie), sous un nom de paquet
unique (chaque service s'appelle « app », on ne peut pas tous les importer tels quels).
"""

import csv
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = Path(__file__).parent / "results"
SPEED = 360              # 1 s réelle = 6 min simulées (config/<fog>.yaml)


def service(name: str, folder: str):
    """Le paquet app/ d'un service, importé sous le nom `name` (ex. service("ps", "pump-scheduler"))."""
    if name in sys.modules:
        return sys.modules[name]
    path = ROOT / "services" / folder / "app"
    spec = importlib.util.spec_from_file_location(name, path / "__init__.py",
                                                  submodule_search_locations=[str(path)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def sim_min(real_s: float) -> float:
    """Secondes réelles -> minutes simulées."""
    return real_s * SPEED / 60


def write_csv(name: str, rows: list[dict]) -> Path:
    RES.mkdir(exist_ok=True)
    path = RES / name
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t
