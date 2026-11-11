"""Run the virtual ESP32 fleet of one fog, on real Sfax weather and an accelerated clock.

    $env:FOG_ID="fog-a"; python -m simulator.main
    python -m simulator.main --speed 1800 --period 2 --moisture 22

Besides the fleet, the simulator:
  - publishes a 24 h rain forecast (retained) on {fog}/weather/forecast every simulated hour;
  - listens to {fog}/sim/fault to inject faults (see faults.py).
"""

import argparse
import json
import threading
import time
from datetime import datetime

import paho.mqtt.client as mqtt

from agrifog_common import fog_id, load_config, topics

from .clock import SimClock
from .faults import ForecastOverride, apply_fault
from .fleet import build_fleet
from .weather import default_weather_path, load_weather

SOIL_STEP_S = 1.0     # pas réel du modèle de sol
STATUS_EVERY_S = 30   # résumé à l'écran


def start_control(cfg: dict, fog: str, zones: dict, devices: dict, clock: SimClock,
                  forecast: ForecastOverride) -> mqtt.Client:
    """MQTT client of the simulator itself: fault injection in, forecast out."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"simulator-{fog}")

    def on_connect(c, u, f, rc, p):
        c.subscribe(topics.sim_fault(fog), qos=1)

    def on_message(c, u, msg):
        try:
            print(f"[panne] {apply_fault(json.loads(msg.payload), zones, devices, forecast, clock.now())}")
        except (ValueError, json.JSONDecodeError) as e:
            print(f"[panne] commande refusée : {e}")

    client.on_connect, client.on_message = on_connect, on_message
    try:
        client.connect(cfg["mqtt"]["host"], cfg["mqtt"]["port"])
        client.loop_start()
    except OSError as e:
        print(f"[simulateur] broker injoignable, pas d'injection de pannes ni de prévision : {e}")
    return client


def publish_forecast(client: mqtt.Client, fog: str, zones: dict, clock: SimClock,
                     forecast: ForecastOverride) -> None:
    now = clock.now()
    z = next(iter(zones.values()))  # même météo pour toute la parcelle
    if forecast.active(now):
        mm, source = forecast.mm, "injectée"
    else:
        mm, source = z.rain_next_hours(24), "simulation"
    msg = {"ts": clock.iso(), "rain_next_24h_mm": mm, "source": source}
    client.publish(topics.weather_forecast(fog), json.dumps(msg), qos=1, retain=True)


def main() -> None:
    cfg, fog = load_config(), fog_id()
    sim = cfg.get("simulation", {})

    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=sim.get("speed", 360),
                    help="1 s réelle = speed s simulées (360 -> 1 jour en 4 min)")
    ap.add_argument("--period", type=float, default=sim.get("wake_period_s", 10),
                    help="secondes RÉELLES de deep sleep entre deux réveils")
    ap.add_argument("--devices", type=int, default=None, help="limiter le nombre d'ESP32")
    ap.add_argument("--moisture", type=float, default=None,
                    help="humidité de départ du sol en %% (défaut : capacité au champ)")
    ap.add_argument("--sync", action="store_true",
                    help="horloge commune aux fogs (minuit UTC réel = départ) : obligatoire pour négocier le quota")
    args = ap.parse_args()

    weather_path = default_weather_path()
    weather = load_weather(weather_path)
    start = datetime.fromisoformat(weather[0]["date"]).replace(hour=0)
    real0 = (time.time() // 86400) * 86400 if args.sync else None
    clock = SimClock(start, args.speed, real0=real0)

    zones, devices = build_fleet(cfg, fog, args.period, clock, weather, args.devices)
    if args.moisture is not None:
        for z in zones.values():
            z.soil.theta = args.moisture
    print(f"{fog} : {len(devices)} ESP32 virtuels sur {len(zones)} zones, "
          f"brokers {', '.join(h for h, _ in devices[0].brokers)}")
    print(f"météo {weather_path.name}, départ {start:%Y-%m-%d}, vitesse x{args.speed:g} "
          f"(1 jour simulé = {86400 / args.speed / 60:.1f} min réelles)")
    print(f"horloge {'synchronisée' if args.sync else 'locale'} : maintenant {clock.now():%Y-%m-%d %H:%M} (simulé)")
    print(f"pannes : publier sur {topics.sim_fault(fog)} (voir simulator/faults.py)")

    forecast = ForecastOverride()
    control = start_control(cfg, fog, zones, {d.device: d for d in devices}, clock, forecast)

    stop = threading.Event()
    threads = [threading.Thread(target=d.run, args=(stop,), daemon=True) for d in devices]
    for t in threads:
        t.start()

    last_status, last_forecast_hour = 0.0, None
    try:
        while True:
            for z in zones.values():
                z.step()
            now = clock.now()
            if now.replace(minute=0, second=0, microsecond=0) != last_forecast_hour:
                last_forecast_hour = now.replace(minute=0, second=0, microsecond=0)
                publish_forecast(control, fog, zones, clock, forecast)
            if time.time() - last_status >= STATUS_EVERY_S:
                last_status = time.time()
                summary = "  ".join(f"{z.id}={z.moisture:.1f}%" for z in zones.values())
                print(f"[{now:%m-%d %H:%M}] humidité sol : {summary}")
            time.sleep(SOIL_STEP_S)
    except KeyboardInterrupt:
        print("arrêt de la flotte…")
        stop.set()
        control.loop_stop()
        for t in threads:
            t.join(timeout=5)


if __name__ == "__main__":
    main()
