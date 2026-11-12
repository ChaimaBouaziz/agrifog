"""Build the LOCAL history of one fog: what its sensors recorded, hour by hour.

On a real farm this comes from the fog's database (InfluxDB). Here we replay part of the
season with the soil model and varied irrigation habits, so that each zone has its own story.
The file stays on the fog: in federated learning only the model weights leave it.

    python -m simulator.history --fog fog-a --days 14 --start 2025-06-01
    -> data/history_fog-a.csv
"""

import argparse
import csv
import random
from datetime import date, datetime, timedelta

from agrifog_common import load_config
from agrifog_common.config import REPO_ROOT

from .soil import bucket_from_config, et0_rate_mm_h
from .weather import air_conditions, default_weather_path, load_weather

COLUMNS = ["ts", "zone", "moisture", "air_temp", "air_humidity", "t_min", "t_max",
           "rain_mm", "irrig_mm"]


def zone_history(cfg: dict, zone: dict, weather: list[dict], start: date, days: int,
                 rng: random.Random, noise: float = 0.2) -> list[dict]:
    """Hourly records of one zone. Each zone has its own watering habits and starting moisture."""
    bucket = bucket_from_config(cfg)
    p = bucket.params
    bucket.theta = rng.uniform(p.wp_pct + 2, p.fc_pct)
    area = zone.get("area_m2", 200.0)
    flow = zone.get("flow_lpm", 20.0)
    habit_hour = rng.choice([5, 6, 7, 18, 19, 20])
    by_date = {r["date"]: r for r in weather}
    rows = []
    for d in range(days):
        day_date = start + timedelta(days=d)
        day = by_date.get(day_date.isoformat(), weather[d % len(weather)])
        # arrosage du jour : parfois rien, parfois beaucoup, à une heure habituelle (ou pas)
        dose_mm = rng.choice([0, 0, rng.uniform(3, 8), rng.uniform(8, 16)])
        water_hour = habit_hour if rng.random() < 0.7 else rng.randrange(24)
        for h in range(24):
            irrig = 0.0
            if h == water_hour and dose_mm > 0:
                irrig = min(dose_mm, flow * 60 / area)  # 1 h de vanne au maximum par heure
            rain = day["rain_mm"] / 24
            temp, rh = air_conditions(day, h + 0.5)
            bucket.step(1.0, et0_rate_mm_h(day["et0_fao"], h + 0.5), rain_mm=rain, irrig_mm=irrig)
            rows.append({
                "ts": datetime(day_date.year, day_date.month, day_date.day, h).isoformat() + "Z",
                "zone": zone["id"],
                "moisture": round(bucket.theta + rng.gauss(0, noise), 2),  # médiane de 3 capteurs bruités
                "air_temp": round(temp + rng.gauss(0, 0.3), 1),
                "air_humidity": round(rh + rng.gauss(0, 1.5), 1),
                "t_min": day["t_min"], "t_max": day["t_max"],
                "rain_mm": round(rain, 3), "irrig_mm": round(irrig, 2),
            })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fog", default="fog-a")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--start", default="2025-06-01")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--noise", type=float, default=0.2, help="bruit du capteur (% vol)")
    args = ap.parse_args()

    cfg = load_config(args.fog)
    weather = load_weather(default_weather_path())
    rng = random.Random(args.seed if args.seed is not None else sum(map(ord, args.fog)))  # reproductible
    rows = []
    for zone in (z for z in cfg["zones"] if z.get("simulated", True)):
        rows += zone_history(cfg, zone, weather, date.fromisoformat(args.start), args.days, rng, args.noise)

    out = REPO_ROOT / "data" / f"history_{args.fog}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"{args.fog} : {len(rows)} mesures horaires ({args.days} jours × {len(cfg['zones'])} zones) -> {out}")


if __name__ == "__main__":
    main()
