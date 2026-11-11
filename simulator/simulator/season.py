"""Replay a whole season offline (no MQTT) with a simple irrigation policy.

This is the BASELINE of the week-7 experiment ("arrosage à heure fixe vs AgriFog").

    python -m simulator.season                         # fog-a, 5 mm chaque jour à 7h
    python -m simulator.season --fog fog-b --mm 3 --hour 19
    python -m simulator.season --mm 0                  # sans irrigation
"""

import argparse
import csv
from dataclasses import dataclass

from agrifog_common import load_config
from agrifog_common.config import REPO_ROOT

from .soil import SoilBucket, bucket_from_config, et0_rate_mm_h
from .weather import default_weather_path, load_weather


@dataclass
class SeasonResult:
    days: int
    irrig_mm: float
    irrig_l: float
    rain_mm: float
    etc_mm: float
    drain_mm: float
    stress_h: float
    stress_days: int
    daily: list[dict]


def run_fixed_schedule(bucket: SoilBucket, weather: list[dict], mm_per_day: float,
                       hour: int, area_m2: float, dt_h: float = 0.25) -> SeasonResult:
    """Water `mm_per_day` at `hour` every day, whatever the soil says."""
    daily, stress_days = [], 0
    steps_per_day = int(24 / dt_h)
    for day in weather:
        stress_before = bucket.stress_h
        for i in range(steps_per_day):
            h = i * dt_h
            irrig = mm_per_day if h == hour else 0.0
            bucket.step(dt_h, et0_rate_mm_h(day["et0_fao"], h),
                        rain_mm=day["rain_mm"] / 24 * dt_h, irrig_mm=irrig)
        stressed = bucket.stress_h > stress_before
        stress_days += stressed
        daily.append({"date": day["date"], "theta_pct": round(bucket.theta, 2),
                      "stress": int(stressed)})
    return SeasonResult(
        days=len(weather), irrig_mm=bucket.irrig_mm, irrig_l=bucket.irrig_mm * area_m2,
        rain_mm=bucket.rain_mm, etc_mm=bucket.etc_mm, drain_mm=bucket.drain_mm,
        stress_h=bucket.stress_h, stress_days=stress_days, daily=daily,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fog", default="fog-a")
    ap.add_argument("--mm", type=float, default=5.0, help="mm apportés chaque jour")
    ap.add_argument("--hour", type=int, default=7, help="heure de l'arrosage")
    args = ap.parse_args()

    cfg = load_config(args.fog)
    weather = load_weather(default_weather_path())
    area = cfg["zones"][0].get("area_m2", 200.0)
    bucket = bucket_from_config(cfg)
    res = run_fixed_schedule(bucket, weather, args.mm, args.hour, area)

    p = bucket.params
    print(f"{args.fog} ({cfg['crop']['name']}) — {res.days} jours, {args.mm:g} mm/jour à {args.hour}h, zone {area:g} m²")
    print(f"  seuil de stress      : {p.stress_threshold_pct:.1f} % (CC {p.fc_pct} %, PF {p.wp_pct} %)")
    print(f"  eau d'irrigation     : {res.irrig_mm:7.0f} mm  = {res.irrig_l / 1000:6.1f} m³ par zone")
    print(f"  pluie                : {res.rain_mm:7.0f} mm")
    print(f"  ETc (consommée)      : {res.etc_mm:7.0f} mm")
    print(f"  drainage (gaspillée) : {res.drain_mm:7.0f} mm  ({100 * res.drain_mm / max(res.irrig_mm, 1):.0f} % de l'irrigation)")
    print(f"  stress hydrique      : {res.stress_days} jours ({res.stress_h:.0f} h)")

    out = REPO_ROOT / "experiments" / f"season_{args.fog}_fixed_{args.mm:g}mm_{args.hour}h.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "theta_pct", "stress"])
        w.writeheader()
        w.writerows(res.daily)
    print(f"  détail jour par jour : {out}")


if __name__ == "__main__":
    main()
