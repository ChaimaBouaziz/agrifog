"""irrigation-engine — HTTP API around the MQTT decision engine.

    $env:FOG_ID="fog-a"; uvicorn app.main:app --port 8001     (depuis services/irrigation-engine)
    GET /health   GET /zones
"""

import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agrifog_common import fog_id, load_config

from .engine import IrrigationEngine, run_mqtt

SERVICE = "irrigation-engine"

config = load_config()
engine = IrrigationEngine(config, fog_id())
_stop = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("ENGINE_MQTT", "1") == "1":
        threading.Thread(target=run_mqtt, args=(engine, config["mqtt"]["host"], config["mqtt"]["port"], _stop),
                         daemon=True).start()
    yield
    _stop.set()


app = FastAPI(title=SERVICE, lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"service": SERVICE, "fog": fog_id(), "status": "ok"}


@app.get("/zones")
def zones() -> dict:
    """Current moisture, flowmeter counter and last decision (with its reason) per zone."""
    return engine.snapshot()
