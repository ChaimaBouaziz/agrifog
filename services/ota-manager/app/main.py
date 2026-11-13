"""ota-manager — mises à jour du firmware des ESP32 du fog (semaine 4 : v1).

    $env:FOG_ID="fog-b"; uvicorn app.main:app --port 8003      (depuis services/ota-manager)

    GET  /health
    GET  /releases                    catalogue local (versions, SHA-256, taille)
    POST /releases                    {"github_tag": "fw-v0.3.0"}  ou  {"version","url","sha256"}
    GET  /firmware/{version}.bin      le binaire, servi aux ESP32 du fog (miroir local)
    POST /deploy                      {"version": "0.3.0", "devices": ["esp-b-01"]}  (ou "all")
    GET  /devices                     version réellement installée + dernier statut OTA par ESP32

Plus simple depuis PowerShell : python -m app.cli … (voir app/cli.py).
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agrifog_common import fog_id, load_config, topics
from agrifog_common.config import REPO_ROOT

from .catalog import Catalog, ChecksumError
from .manager import OtaManager

SERVICE = "ota-manager"

config = load_config()
FOG = fog_id()
catalog = Catalog(Path(os.environ.get("OTA_DATA_DIR", REPO_ROOT / "data" / "ota")))
_client: mqtt.Client | None = None


def _publish(topic: str, payload: dict) -> None:
    if _client is not None:
        _client.publish(topic, json.dumps(payload), qos=1)


manager = OtaManager(config, FOG, catalog, os.environ.get("OTA_BASE_URL", "http://127.0.0.1:8003"), _publish)


def run_mqtt(host: str, port: int, stop: threading.Event) -> None:
    global _client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"ota-manager-{FOG}")

    def on_connect(c, u, f, rc, p):
        print(f"[mqtt] connecté ({rc}) à {host}:{port}")
        c.subscribe(topics.heartbeat(FOG, "+"), qos=1)
        c.subscribe(topics.ota_status(FOG, "+"), qos=1)

    def on_message(c, u, m):
        parts = m.topic.split("/")          # fog/device/<dev>/heartbeat  |  fog/device/<dev>/ota/status
        try:
            msg = json.loads(m.payload)
        except ValueError:
            return
        if parts[-1] == "heartbeat":
            manager.on_heartbeat(parts[2], msg)
        elif parts[-2:] == ["ota", "status"]:
            manager.on_status(parts[2], msg)
            print(f"[ota] {parts[2]} : {msg.get('state')} {msg.get('version')} {msg.get('detail', '')}".rstrip())

    client.on_connect, client.on_message = on_connect, on_message
    client.reconnect_delay_set(1, 10)
    client.connect_async(host, port)
    client.loop_start()
    _client = client
    stop.wait()
    client.loop_stop()


_stop = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("OTA_MQTT", "1") == "1":
        host = os.environ.get("MQTT_HOST") or config["mqtt"]["host"]
        threading.Thread(target=run_mqtt, args=(host, config["mqtt"]["port"], _stop), daemon=True).start()
    yield
    _stop.set()


app = FastAPI(title=SERVICE, lifespan=lifespan)


class NewRelease(BaseModel):
    github_tag: str | None = None
    version: str | None = None
    url: str | None = None
    sha256: str | None = None


class Deploy(BaseModel):
    version: str
    devices: list[str] | str


@app.get("/health")
def health() -> dict:
    return {"service": SERVICE, "fog": FOG, "status": "ok"}


@app.get("/releases")
def releases() -> list[dict]:
    return [r.__dict__ for r in catalog.releases()]


@app.post("/releases")
def add_release(body: NewRelease) -> dict:
    try:
        if body.github_tag:
            rel = catalog.add_github(body.github_tag)
        elif body.version and body.url and body.sha256:
            rel = catalog.add(body.version, body.url, body.sha256)
        else:
            raise HTTPException(400, "donner github_tag, ou version + url + sha256")
    except ChecksumError as e:
        raise HTTPException(422, str(e)) from e
    except (OSError, ValueError, KeyError) as e:
        raise HTTPException(502, f"téléchargement impossible : {e}") from e
    return rel.__dict__


@app.get("/firmware/{version}.bin")
def firmware(version: str):
    path = catalog.path(version)
    if not path.exists():
        raise HTTPException(404, f"version {version} absente du catalogue")
    return FileResponse(path, media_type="application/octet-stream", filename=f"agrifog-{version}.bin")


@app.post("/deploy")
def deploy(body: Deploy) -> dict:
    try:
        orders = manager.deploy(body.version, body.devices)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"sent": len(orders), "orders": orders}


@app.get("/devices")
def devices() -> dict:
    return manager.snapshot()
