"""fog-twin — API REST entre fogs : sauvegarde mutuelle et reprise de la parcelle voisine.

    $env:FOG_ID="fog-a"; uvicorn app.main:app --host 0.0.0.0 --port 8080     (depuis services/fog-twin)

    GET  /health            état du jumeau (normal, partition, isole, reprise…)
    GET  /twin              détail : âge du statut du voisin, témoin cloud, sauvegardes, reprise en cours
    POST /backup            le voisin nous envoie sa sauvegarde : {"fog", "unix", "data"}
    GET  /backup/{fog}      restitue la dernière sauvegarde d'un fog
    POST /takeover          {"from": "<voisin>", "action": "request" | "release"}
"""

import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agrifog_common import fog_id, load_config

from .twin import FogTwin

SERVICE = "fog-twin"

config = load_config()
twin = FogTwin(config, fog_id(), mqtt_enabled=os.environ.get("TWIN_MQTT", "1") == "1")
_stop = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = None
    if twin.mqtt_enabled:
        thread = threading.Thread(target=twin.run, args=(_stop,), daemon=True)
        thread.start()
    yield
    _stop.set()
    if thread:
        thread.join(timeout=5)


app = FastAPI(title=SERVICE, lifespan=lifespan)


class Backup(BaseModel):
    fog: str
    unix: float
    data: dict


class TakeoverCall(BaseModel):
    origin: str = Field(alias="from")
    action: str


@app.get("/health")
def health() -> dict:
    return {"service": SERVICE, "fog": fog_id(), "status": "ok", "state": twin.state,
            "takeover": twin.takeover.target if twin.takeover else None}


@app.get("/twin")
def status() -> dict:
    return twin.snapshot()


@app.post("/backup")
def receive_backup(body: Backup) -> dict:
    if body.fog != twin.neighbor:
        raise HTTPException(400, f"{body.fog} n'est pas notre voisin ({twin.neighbor})")
    twin.store.save(body.fog, body.model_dump())
    return {"stored": body.fog, "unix": body.unix}


@app.get("/backup/{fog}")
def get_backup(fog: str) -> dict:
    backup = twin.store.get(fog)
    if backup is None:
        raise HTTPException(404, f"aucune sauvegarde de {fog}")
    return backup


@app.post("/takeover")
def takeover(body: TakeoverCall) -> dict:
    try:
        return twin.on_takeover_call(body.origin, body.action)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
