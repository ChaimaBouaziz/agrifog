"""pump-scheduler — pompe commune aux deux fogs (exclusion mutuelle par MQTT).

    $env:FOG_ID="fog-a"; uvicorn app.main:app --port 8004     (depuis services/pump-scheduler)
    GET /health   GET /pump   (qui pompe, file d'attente, demandes du voisin, derniers arrosages)
"""

import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agrifog_common import fog_id, load_config

from .scheduler import PumpScheduler, run_mqtt

SERVICE = "pump-scheduler"

config = load_config()
_sim = config.get("simulation", {})
scheduler = PumpScheduler(fog_id(), _sim.get("speed", 360), _sim.get("wake_period_s", 10))
_stop = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = None
    if os.environ.get("PUMP_MQTT", "1") == "1":
        thread = threading.Thread(target=run_mqtt, daemon=True,
                                  args=(scheduler, config["mqtt"]["host"], config["mqtt"]["port"], _stop))
        thread.start()
    yield
    _stop.set()
    if thread:
        thread.join(timeout=3)        # libère la demande retained avant de partir


app = FastAPI(title=SERVICE, lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"service": SERVICE, "fog": fog_id(), "status": "ok"}


@app.get("/pump")
def pump() -> dict:
    return scheduler.snapshot()
