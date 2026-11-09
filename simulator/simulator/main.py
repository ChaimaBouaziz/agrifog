"""Run the virtual ESP32 fleet of one fog.

    $env:FOG_ID="fog-a"; python -m simulator.main               # 10 devices, réveil toutes les 10 s
    python -m simulator.main --period 5 --devices 3
"""

import argparse
import threading
import time

from agrifog_common import fog_id, load_config

from .fleet import build_fleet

SOIL_STEP_S = 1.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", type=float, default=10, help="secondes de deep sleep (accéléré)")
    ap.add_argument("--devices", type=int, default=None, help="limiter le nombre d'ESP32")
    args = ap.parse_args()

    cfg, fog = load_config(), fog_id()
    zones, devices = build_fleet(cfg, fog, args.period, args.devices)
    print(f"{fog} : {len(devices)} ESP32 virtuels sur {len(zones)} zones, "
          f"broker {cfg['mqtt']['host']}:{cfg['mqtt']['port']}")

    stop = threading.Event()
    threads = [threading.Thread(target=d.run, args=(stop,), daemon=True) for d in devices]
    for t in threads:
        t.start()

    try:
        while True:
            for z in zones.values():
                z.step(SOIL_STEP_S)
            time.sleep(SOIL_STEP_S)
    except KeyboardInterrupt:
        print("arrêt de la flotte…")
        stop.set()
        for t in threads:
            t.join(timeout=5)


if __name__ == "__main__":
    main()