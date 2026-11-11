"""Experiment 1 — fixed-time watering vs AgriFog, over the whole Sfax season.

    python exp_irrigation.py          (depuis le dossier experiments/)

For each crop: AgriFog (the engine's real rules) against fixed doses every day at 7h,
and against the SMALLEST fixed dose that avoids any stress (the fair comparison).
Results: results/exp1_irrigation.csv and results/exp1_daily_<fog>.csv (for the charts).
"""

import csv
from pathlib import Path

from fieldsim import run_season, summary

OUT = Path(__file__).parent / "results"
CROPS = {"fog-a": "tomate", "fog-b": "olivier"}


def smallest_no_stress_dose(fog: str) -> tuple[float, dict]:
    for tenth in range(10, 201, 5):           # 1.0 à 20.0 mm/j par pas de 0.5
        mm = tenth / 10
        s = summary(run_season(fog, "fixed", mm))
        if s["stress_days"] == 0:
            return mm, s
    raise RuntimeError("aucune dose fixe sans stress jusqu'à 20 mm/j")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    rows = []
    for fog, crop in CROPS.items():
        agri = run_season(fog, "agrifog")
        rows.append({"fog": fog, "crop": crop, "policy": "AgriFog", **summary(agri)})
        for mm in (4, 6, 8):
            rows.append({"fog": fog, "crop": crop, "policy": f"fixe {mm} mm/j",
                         **summary(run_season(fog, "fixed", mm))})
        mm, s = smallest_no_stress_dose(fog)
        rows.append({"fog": fog, "crop": crop, "policy": f"fixe minimal sans stress ({mm:g} mm/j)", **s})

        best_fixed = run_season(fog, "fixed", mm)
        with open(OUT / f"exp1_daily_{fog}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "theta_agrifog", "liters_agrifog", "theta_fixed", "liters_fixed"])
            za, zf = agri.zones[0], best_fixed.zones[0]
            for a, b in zip(za.daily, zf.daily):
                w.writerow([a["date"], round(a["theta"], 2), round(a["liters"]), round(b["theta"], 2),
                            round(b["liters"])])

    with open(OUT / "exp1_irrigation.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print("Expérience 1 — saison complète, 3 zones de 200 m² par parcelle\n")
    print(f"{'parcelle':9} {'stratégie':34} {'eau m³':>8} {'eau mm':>7} {'drainage mm':>12} "
          f"{'jours stress':>13} {'ET relative':>12}")
    for r in rows:
        print(f"{r['crop']:9} {r['policy']:34} {r['water_m3']:8.0f} {r['water_mm']:7.0f} {r['drain_mm']:12.0f} "
              f"{r['stress_days']:13d} {r['rel_et_pct']:11.1f}%")
    print()
    for fog, crop in CROPS.items():
        agri = next(r for r in rows if r["fog"] == fog and r["policy"] == "AgriFog")
        fixed = next(r for r in rows if r["fog"] == fog and r["policy"].startswith("fixe minimal"))
        saved = 100 * (fixed["water_m3"] - agri["water_m3"]) / fixed["water_m3"]
        print(f"→ {crop} : AgriFog économise {saved:.0f} % d'eau par rapport au meilleur arrosage fixe "
              f"sans stress ({fixed['water_m3'] - agri['water_m3']:.0f} m³ sur la saison)")


if __name__ == "__main__":
    main()
