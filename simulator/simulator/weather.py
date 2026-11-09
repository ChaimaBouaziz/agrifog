"""Download real daily weather for Sfax from Open-Meteo (free, no API key).

Run (from the simulator/ folder):
    python -m simulator.weather                          # saison 2025 par défaut
    python -m simulator.weather --start 2024-04-01 --end 2024-09-30

Output: data/meteo_sfax_<start>_<end>.csv
    date, t_max, t_min, t_mean, rain_mm, rh_mean, et0_fao
"""

import argparse
import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

from agrifog_common import load_config
from agrifog_common.config import REPO_ROOT

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DAILY_VARS = {
    "temperature_2m_max": "t_max",
    "temperature_2m_min": "t_min",
    "temperature_2m_mean": "t_mean",
    "precipitation_sum": "rain_mm",
    "relative_humidity_2m_mean": "rh_mean",
    "et0_fao_evapotranspiration": "et0_fao",  # référence pour valider notre Hargreaves
}


def build_url(lat: float, lon: float, start: str, end: str) -> str:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "daily": ",".join(DAILY_VARS),
        "timezone": "Africa/Tunis",
    }
    return f"{ARCHIVE_URL}?{urllib.parse.urlencode(params)}"


def parse(payload: dict) -> list[dict]:
    """Turn Open-Meteo's column format into one dict per day."""
    daily = payload["daily"]
    rows = []
    for i, day in enumerate(daily["time"]):
        row = {"date": day}
        for api_name, col in DAILY_VARS.items():
            row[col] = daily.get(api_name, [None] * len(daily["time"]))[i]
        rows.append(row)
    return rows


def save_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", *DAILY_VARS.values()])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2025-04-01")
    ap.add_argument("--end", default="2025-09-30")
    args = ap.parse_args()

    loc = load_config()["location"]
    url = build_url(loc["latitude"], loc["longitude"], args.start, args.end)
    print(f"GET {url}")
    with urllib.request.urlopen(url, timeout=30) as resp:
        rows = parse(json.load(resp))

    out = REPO_ROOT / "data" / f"meteo_sfax_{args.start}_{args.end}.csv"
    save_csv(rows, out)
    rain_days = sum(1 for r in rows if (r["rain_mm"] or 0) > 0.2)
    print(f"{len(rows)} jours enregistrés dans {out} ({rain_days} jours de pluie)")


if __name__ == "__main__":
    main()