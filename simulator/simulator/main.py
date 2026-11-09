"""ESP32 fleet simulator — stub: one virtual device publishing telemetry.

Run:  FOG_ID=fog-a python -m simulator.main
"""

import json
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from agrifog_common import fog_id, load_config, topics

PERIOD_S = 5  # accéléré pour le dev ; le vrai cycle est de X minutes


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> None:
    cfg = load_config()
    fog = fog_id()
    zone = cfg["zones"][0]["id"]

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{fog}")
    client.connect(cfg["mqtt"]["host"], cfg["mqtt"]["port"])
    client.loop_start()

    moisture = 40.0
    while True:
        moisture = max(5.0, moisture - random.uniform(0.1, 0.5))  # TODO: modèle de sol (semaine 2)
        msg = {
            "ts": now(),
            "soil_moisture": round(moisture, 1),
            "air_temp": round(random.uniform(18, 30), 1),
            "air_humidity": round(random.uniform(40, 70), 1),
            "flow_lpm": 0.0,
        }
        client.publish(topics.telemetry(fog, zone), json.dumps(msg), qos=1)
        print(topics.telemetry(fog, zone), msg)
        time.sleep(PERIOD_S)


if __name__ == "__main__":
    main()
