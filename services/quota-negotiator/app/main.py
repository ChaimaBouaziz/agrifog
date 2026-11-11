"""quota-negotiator — HTTP API around the MQTT negotiation.

    $env:FOG_ID="fog-a"; uvicorn app.main:app --port 8002     (depuis services/quota-negotiator)
    GET /health   GET /quota   (7 derniers jours : demandes, répartition, cessions)
"""

import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agrifog_common import fog_id, load_config

from .negotiator import QuotaNegotiator, run_mqtt

SERVICE = "quota-negotiator"

config = load_config()
negotiator = QuotaNegotiator(config, fog_id())
_stop = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("NEGOTIATOR_MQTT", "1") == "1":
        threading.Thread(target=run_mqtt, args=(negotiator, config["mqtt"]["host"], config["mqtt"]["port"], _stop),
                         daemon=True).start()
    yield
    _stop.set()


app = FastAPI(title=SERVICE, lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"service": SERVICE, "fog": fog_id(), "status": "ok"}


@app.get("/quota")
def quota() -> dict:
    return negotiator.snapshot()
