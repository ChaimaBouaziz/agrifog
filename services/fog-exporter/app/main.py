"""fog-exporter: MQTT -> Prometheus (http://<fog>:9108/metrics) + fog heartbeat on domaine/fog/<fog>/status.

    FOG_ID=fog-b python -m app.main            # depuis services/fog-exporter
"""

from __future__ import annotations

import json
import os
import threading
import time

import paho.mqtt.client as mqtt
import psutil
from prometheus_client import start_http_server

from agrifog_common import topics
from agrifog_common.config import fog_id, load_config

from .metrics import FogMetrics

VERSION = "0.3.0"
STATUS_PERIOD_S = 10


def status_payload(fog: str, alive: bool = True) -> dict:
    p = {"fog": fog, "alive": alive, "version": VERSION, "unix": round(time.time(), 1)}
    if alive:
        p["cpu"] = psutil.cpu_percent(interval=None)
        p["ram"] = psutil.virtual_memory().percent
    return p


def run(metrics: FogMetrics, host: str, port: int, stop: threading.Event | None = None) -> None:
    fog = metrics.fog
    stop = stop or threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"fog-exporter-{fog}")
    # si l'exporter (donc le fog) disparaît, le broker annonce sa mort au voisin
    client.will_set(topics.fog_status(fog), json.dumps(status_payload(fog, alive=False)), qos=1, retain=True)

    def on_connect(c, u, f, rc, p):
        print(f"[mqtt] connecté ({rc}) à {host}:{port}")
        for t in metrics.subscriptions():
            c.subscribe(t, qos=1)

    def on_message(c, u, m):
        try:
            raw = m.payload.decode("utf-8", "replace")
            payload = raw if m.topic.startswith("$SYS/") else json.loads(raw)
            metrics.handle(m.topic, payload)
        except (ValueError, KeyError, TypeError) as e:
            print(f"[mqtt] message ignoré sur {m.topic} : {e}")

    client.on_connect, client.on_message = on_connect, on_message
    while not stop.is_set():
        try:
            client.connect(host, port)
            break
        except OSError as e:
            print(f"[mqtt] broker {host}:{port} injoignable ({e}), nouvel essai dans 5 s")
            time.sleep(5)
    client.loop_start()
    while True:   # signe de vie du fog toutes les 10 s (traverse le pont : le voisin le voit)
        client.publish(topics.fog_status(fog), json.dumps(status_payload(fog)), qos=1, retain=True)
        if stop.wait(STATUS_PERIOD_S):
            break
    client.publish(topics.fog_status(fog), json.dumps(status_payload(fog, alive=False)), qos=1, retain=True)
    client.loop_stop()


def main() -> None:
    cfg, fog = load_config(), fog_id()
    port = int(os.environ.get("EXPORTER_PORT", "9108"))
    metrics = FogMetrics(cfg, fog)
    start_http_server(port, registry=metrics.registry)
    print(f"fog-exporter {fog} : métriques sur http://0.0.0.0:{port}/metrics")
    try:
        # MQTT_HOST d'abord : dans Docker le broker s'appelle "mosquitto", pas 127.0.0.1
        host = os.environ.get("MQTT_HOST") or cfg["mqtt"]["host"]
        run(metrics, host, int(cfg["mqtt"].get("port", 1883)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
