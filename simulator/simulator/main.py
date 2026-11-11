"""Run the virtual ESP32 fleet of one fog, on real Sfax weather and an accelerated clock.

    $env:FOG_ID="fog-a"; python -m simulator.main
    python -m simulator.main --speed 720 --period 5 --devices 3
"""

import argparse
import threading
import time
from datetime import datetime

from agrifog_common import fog_id, load_config

from .clock import SimClock
from .fleet import build_fleet
from .weather import default_weather_path, load_weather

SOIL_STEP_S = 1.0     # pas réel du modèle de sol
STATUS_EVERY_S = 30   # résumé à l'écran


def main() -> None:
    cfg, fog = load_config(), fog_id()
    sim = cfg.get("simulation", {})

    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=sim.get("speed", 360),
                    help="1 s réelle = speed s simulées (360 -> 1 jour en 4 min)")
    ap.add_argument("--period", type=float, default=sim.get("wake_period_s", 10),
                    help="secondes RÉELLES de deep sleep entre deux réveils")
    ap.add_argument("--devices", type=int, default=None, help="limiter le nombre d'ESP32")
    args = ap.parse_args()

    weather_path = default_weather_path()
    weather = load_weather(weather_path)
    start = datetime.fromisoformat(weather[0]["date"]).replace(hour=0)
    clock = SimClock(start, args.speed)

    zones, devices = build_fleet(cfg, fog, args.period, clock, weather, args.devices)
    print(f"{fog} : {len(devices)} ESP32 virtuels sur {len(zones)} zones, "
          f"broker {cfg['mqtt']['host']}:{cfg['mqtt']['port']}")
    print(f"météo {weather_path.name}, départ {start:%Y-%m-%d}, vitesse x{args.speed:g} "
          f"(1 jour simulé = {86400 / args.speed / 60:.1f} min réelles)")

    stop = threading.Event()
    threads = [threading.Thread(target=d.run, args=(stop,), daemon=True) for d in devices]
    for t in threads:
        t.start()

    last_status = 0.0
    try:
        while True:
            for z in zones.values():
                z.step()
            if time.time() - last_status >= STATUS_EVERY_S:
                last_status = time.time()
                summary = "  ".join(f"{z.id}={z.moisture:.1f}%" for z in zones.values())
                print(f"[{clock.now():%m-%d %H:%M}] humidité sol : {summary}")
            time.sleep(SOIL_STEP_S)
    except KeyboardInterrupt:
        print("arrêt de la flotte…")
        stop.set()
        for t in threads:
            t.join(timeout=5)


if __name__ == "__main__":
    main()
