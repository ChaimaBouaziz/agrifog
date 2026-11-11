"""Experiment 2 — shared water quota: equal split 50/50 vs negotiation between the fogs.

    python exp_quota.py               (depuis le dossier experiments/)

Same season, same quota, both fogs run the AgriFog engine; only the way the quota is shared changes.
Results: results/exp2_quota.csv
"""

import csv
from pathlib import Path

from fieldsim import run_pair, summary

OUT = Path(__file__).parent / "results"
QUOTAS = (4000, 5000, 6000, 7000)
MODES = {"half": "50/50 (sans coopération)", "negotiated": "négociation AgriFog"}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    rows = []
    for q in QUOTAS:
        for mode, label in MODES.items():
            fogs = run_pair(q, mode)
            for fog, crop in (("fog-a", "tomate"), ("fog-b", "olivier")):
                rows.append({"quota_l_day": q, "mode": label, "crop": crop, **summary(fogs[fog])})
    with open(OUT / "exp2_quota.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print("Expérience 2 — même quota partagé par la tomate (fog-a) et l'olivier (fog-b)\n")
    print(f"{'quota L/j':>9}  {'partage':26} {'tomate ET':>10} {'stress':>7}   {'olivier ET':>10} {'stress':>7}   {'eau utilisée':>13}")
    for q in QUOTAS:
        for label in MODES.values():
            t = next(r for r in rows if r["quota_l_day"] == q and r["mode"] == label and r["crop"] == "tomate")
            o = next(r for r in rows if r["quota_l_day"] == q and r["mode"] == label and r["crop"] == "olivier")
            used = t["water_m3"] + o["water_m3"]
            print(f"{q:9d}  {label:26} {t['rel_et_pct']:9.1f}% {t['stress_days']:6d}j   "
                  f"{o['rel_et_pct']:9.1f}% {o['stress_days']:6d}j   {used:6.0f} / {q * 183 / 1000:.0f} m³")
    print("\nET relative = transpiration réelle / transpiration sans stress (indicateur du rendement).")


if __name__ == "__main__":
    main()
