"""test-console — une page web pour provoquer chaque situation et vérifier que le système réagit.

    $env:FOG_ID="fog-a"; uvicorn app.main:app --port 8090        (depuis services/test-console)
    puis ouvrir http://localhost:8090

    GET  /                      la console
    GET  /api/scenarios         les scénarios et leur dernier résultat
    POST /api/scenarios/{id}    lancer un scénario
    GET  /api/stream            les messages MQTT en direct (server-sent events)
"""

import asyncio
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from agrifog_common import fog_id, load_config

from .scenarios import Ctx, Runner

SERVICE = "test-console"
FOG = fog_id()
CFG = load_config()
NEIGHBOR = CFG["neighbor"]
PEER_TWIN = os.environ.get("TWIN_PEER_URL", f"http://{NEIGHBOR}:8080").rstrip("/")
OTA_URL = os.environ.get("OTA_URL", "http://127.0.0.1:8003").rstrip("/")
STATIC = Path(__file__).parent / "static"

_http = httpx.Client(timeout=8)
_client: mqtt.Client | None = None
_listeners: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None
_stop = threading.Event()


def _call(method: str, url: str, body: dict | None) -> dict:
    try:
        r = _http.request(method, url, json=body)
    except httpx.TransportError as e:
        raise RuntimeError(f"{url} injoignable ({type(e).__name__}) : le service est-il lancé ?") from e
    if r.status_code >= 400:
        raise RuntimeError(f"{url} a refusé ({r.status_code}) : {r.text[:200]}")
    return r.json() if r.content else {}


def _execute(action: tuple) -> str:
    kind = action[0]
    if kind == "mqtt":
        _, topic, payload, retain = action
        if _client is None:
            raise RuntimeError("pas de connexion au broker")
        _client.publish(topic, json.dumps(payload), qos=1, retain=retain)
        return f"MQTT → {topic} {json.dumps(payload, ensure_ascii=False)}"
    _, method, url, body = action
    answer = _call(method, url, body)
    return f"HTTP {method} {url} → {json.dumps(answer, ensure_ascii=False)[:160]}"


def _ctx() -> Ctx:
    return Ctx(FOG, NEIGHBOR, CFG, PEER_TWIN, OTA_URL, http=_call)


runner = Runner(_ctx, _execute)


def _broadcast(item: dict) -> None:
    if _loop is None:
        return
    for q in list(_listeners):
        _loop.call_soon_threadsafe(_put, q, item)


def _put(q: asyncio.Queue, item: dict) -> None:
    if not q.full():            # navigateur trop lent : on saute des messages plutôt que de bloquer
        q.put_nowait(item)


def _run_mqtt() -> None:
    global _client
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"test-console-{FOG}-{int(time.time())}")

    def on_connect(cl, u, f, rc, p):
        print(f"[mqtt] connecté ({rc}) : écoute de {FOG}/# et domaine/#")
        cl.subscribe([(f"{FOG}/#", 0), ("domaine/#", 0)])

    def on_message(cl, u, m):
        try:
            payload = json.loads(m.payload)
        except ValueError:
            payload = {"raw": m.payload.decode(errors="replace")[:200]}
        if isinstance(payload, dict):
            runner.on_message(m.topic, payload, retained=m.retain)
        _broadcast({"t": time.strftime("%H:%M:%S"), "topic": m.topic, "retained": m.retain,
                    "payload": payload})

    c.on_connect, c.on_message = on_connect, on_message
    mq = CFG["mqtt"]
    while not _stop.is_set():
        try:
            c.connect(mq["host"], mq["port"])
            break
        except OSError as e:
            print(f"[mqtt] broker {mq['host']}:{mq['port']} injoignable ({e}), nouvel essai dans 5 s")
            _stop.wait(5)
    _client = c
    c.loop_start()
    while not _stop.wait(0.5):
        runner.tick()
    c.disconnect()
    c.loop_stop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    if os.environ.get("CONSOLE_MQTT", "1") == "1":
        threading.Thread(target=_run_mqtt, daemon=True).start()
    yield
    _stop.set()


app = FastAPI(title=SERVICE, lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict:
    return {"service": SERVICE, "fog": FOG, "status": "ok"}


@app.get("/api/info")
def info() -> dict:
    return {"fog": FOG, "neighbor": NEIGHBOR, "crop": CFG.get("crop", {}).get("name"),
            "peer_twin": PEER_TWIN, "ota": OTA_URL, "mqtt": _client is not None and _client.is_connected()}


@app.get("/api/scenarios")
def scenarios() -> list[dict]:
    return runner.snapshot()


@app.post("/api/scenarios/{sid}")
def launch(sid: str) -> dict:
    if sid not in runner.scenarios:
        raise HTTPException(404, f"scénario inconnu : {sid}")
    current = runner.runs.get(sid)
    if current and current.status == "en cours":
        raise HTTPException(409, "ce scénario est déjà en cours")
    run = runner.start(sid)
    return {"id": sid, "status": run.status, "detail": run.detail}


@app.get("/api/stream")
async def stream():
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _listeners.add(q)

    async def events():
        try:
            while True:
                item = await q.get()
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            _listeners.discard(q)

    return StreamingResponse(events(), media_type="text/event-stream")
