"""Reprise de la parcelle voisine : un irrigation-engine de plus, pour le fog en panne, sur NOTRE broker.

Les ESP32 du voisin basculent d'eux-mêmes sur notre broker (config mqtt.fallback_brokers) et continuent
de publier sur <voisin>/zone/... : ce moteur les écoute et les pilote, avec la configuration du voisin
(culture, sol, zones) et son état restauré depuis la dernière sauvegarde (budget, litres déjà utilisés).
C'est exactement le code d'irrigation-engine : pas de « mode dégradé » au rabais.
"""

import importlib.util
import sys
import threading
import time
from pathlib import Path

from agrifog_common import load_config


def engine_module():
    """Le module irrigation-engine : copié dans l'image Docker, ou pris dans le dépôt en local."""
    try:
        from irrigation_engine import engine           # image Docker (voir Dockerfile)
        return engine
    except ImportError:
        pass
    path = Path(__file__).resolve().parents[2] / "irrigation-engine" / "app"   # services/irrigation-engine/app
    spec = importlib.util.spec_from_file_location("irrigation_engine", path / "__init__.py",
                                                  submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["irrigation_engine"] = module
    spec.loader.exec_module(module)
    from irrigation_engine import engine
    return engine


class Takeover:
    def __init__(self, target: str, host: str, port: int, backup: dict | None, mqtt_enabled: bool = True):
        eng = engine_module()
        self.target, self.started = target, time.time()
        self.engine = eng.IrrigationEngine(load_config(target), target)
        data = (backup or {}).get("data") or {}
        if data.get("used_l"):
            self.engine.used_l.update({d: float(v) for d, v in data["used_l"].items()})
        if data.get("budget"):
            self.engine.budget = data["budget"]
        self.restored_from = (backup or {}).get("unix")
        self._stop = threading.Event()
        self._thread = None
        if mqtt_enabled:
            self._thread = threading.Thread(target=eng.run_mqtt, daemon=True,
                                            args=(self.engine, host, port, self._stop))
            self._thread.start()

    def stop(self) -> dict:
        """Arrête le moteur de reprise ; renvoie de quoi rendre la parcelle (litres utilisés)."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        return {"target": self.target, "used_l": {d: round(v, 1) for d, v in self.engine.used_l.items()},
                "started": round(self.started, 1), "ended": round(time.time(), 1)}

    def snapshot(self) -> dict:
        return {"target": self.target, "since_s": round(time.time() - self.started),
                "restored_from_backup_unix": self.restored_from, **self.engine.snapshot()}
