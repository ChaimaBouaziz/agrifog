"""Charts for the report, from the CSV files of the experiments.

    python exp_irrigation.py && python exp_quota.py && python charts.py
    -> results/fig1_eau_saison.png, fig2_humidite_tomate.png, fig3_quota.png
Colors: validated palette (blue = AgriFog / useful water, orange = drainage, gray = baseline).
"""

import csv
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

RES = Path(__file__).parent / "results"
BLUE, ORANGE = "#2a78d6", "#eb6834"
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
BASELINE = "#898781"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
    "text.color": INK, "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlecolor": INK,
})


def read(name: str) -> list[dict]:
    with open(RES / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fig_water() -> None:
    rows = read("exp1_irrigation.csv")
    fig, ax = plt.subplots(figsize=(8, 3.6))
    labels, useful, drained, totals = [], [], [], []
    for fog, crop in (("fog-a", "Tomate"), ("fog-b", "Olivier")):
        fixed = next(r for r in rows if r["fog"] == fog and r["policy"].startswith("fixe minimal"))
        agri = next(r for r in rows if r["fog"] == fog and r["policy"] == "AgriFog")
        dose = fixed["policy"].split("(")[1].rstrip(")")
        for r, lab in ((fixed, f"{crop} — fixe {dose}"), (agri, f"{crop} — AgriFog")):
            w, d = float(r["water_mm"]), float(r["drain_mm"])
            labels.append(lab)
            useful.append(w - d)
            drained.append(d)
            totals.append(w)
    y = list(range(len(labels)))[::-1]
    gap = 6  # séparation visuelle entre segments (mm), couleur de la surface
    ax.barh(y, useful, height=0.55, color=BLUE, label="eau restée dans le sol / consommée")
    ax.barh(y, drained, left=[u + gap for u in useful], height=0.55, color=ORANGE,
            label="eau perdue par drainage")
    for yi, tot in zip(y, totals):
        ax.annotate(f"{tot:.0f} mm", (tot, yi), xytext=(6, 0), textcoords="offset points",
                    va="center", color=INK, fontsize=10)
    for i in (1, 3):  # économie, après le total de la barre AgriFog
        saved = 100 * (totals[i - 1] - totals[i]) / totals[i - 1]
        ax.annotate(f"−{saved:.0f} % d'eau", (totals[i], y[i]), xytext=(62, 0), textcoords="offset points",
                    va="center", color="#006300", fontweight="bold")
    ax.set_yticks(y, labels, color=INK2)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(totals) * 1.32)
    ax.set_xlabel("eau d'irrigation sur la saison (mm = L/m²), avril–septembre")
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    savings = [100 * (totals[i - 1] - totals[i]) / totals[i - 1] for i in (1, 3)]
    ax.set_title(f"Zéro stress avec {min(savings):.0f} à {max(savings):.0f} % d'eau en moins "
                 f"qu'un arrosage fixe")
    ax.legend(loc="lower right", frameon=False, fontsize=9, labelcolor=INK2, bbox_to_anchor=(1.0, 0.12))
    fig.tight_layout()
    fig.savefig(RES / "fig1_eau_saison.png", dpi=200)
    plt.close(fig)


def fig_moisture() -> None:
    rows = read("exp1_daily_fog-a.csv")
    days = [date.fromisoformat(r["date"]) for r in rows]
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.axhspan(0, 23, color="#f7e3dc", zorder=0)
    ax.axhline(23, color=MUTED, linewidth=1, linestyle=(0, (4, 3)))
    ax.text(days[-1], 22.7, "seuil de stress de la tomate (23 %)", ha="right", va="top", color=INK2, fontsize=9)
    ax.axhline(30, color=MUTED, linewidth=1, linestyle=(0, (1, 2)))
    ax.text(days[40], 30.15, "capacité au champ (30 %) : au-delà, l'eau draine", ha="left", va="bottom",
            color=INK2, fontsize=9)
    ax.plot(days, [float(r["theta_fixed"]) for r in rows], color=BASELINE, linewidth=2)
    ax.plot(days, [float(r["theta_agrifog"]) for r in rows], color=BLUE, linewidth=1.6)
    from datetime import timedelta
    end = days[-1] + timedelta(days=3)
    ax.text(end, float(rows[-1]["theta_fixed"]), "arrosage fixe\n7 mm/j", va="center", color=INK2, fontsize=9)
    ax.text(end, float(rows[-1]["theta_agrifog"]), "AgriFog", va="center", color=INK, fontsize=9,
            fontweight="bold")
    ax.set_xlim(days[0] - timedelta(days=4), days[-1] + timedelta(days=24))
    ax.set_ylim(20, 31.5)
    ax.set_ylabel("humidité du sol (% vol), fin de journée")
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.set_title("Tomate : AgriFog reste au-dessus du seuil sans remplir le sol")
    fig.tight_layout()
    fig.savefig(RES / "fig2_humidite_tomate.png", dpi=200)
    plt.close(fig)


def fig_quota() -> None:
    rows = read("exp2_quota.csv")
    quotas = sorted({int(r["quota_l_day"]) for r in rows})
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.6), sharey=True)
    for ax, crop in zip(axes, ("tomate", "olivier")):
        series = {}
        for mode, color, label in (("50/50 (sans coopération)", BASELINE, "partage 50/50"),
                                   ("négociation AgriFog", BLUE, "négociation")):
            vals = [float(next(r for r in rows if int(r["quota_l_day"]) == q and r["mode"] == mode
                               and r["crop"] == crop)["rel_et_pct"]) for q in quotas]
            series[label] = vals
            ax.plot(quotas, vals, color=color, linewidth=2, marker="o", markersize=5,
                    markeredgecolor=SURFACE, markeredgewidth=1.5, label=label)
        ends = {k: v[-1] for k, v in series.items()}
        if abs(ends["partage 50/50"] - ends["négociation"]) < 2:      # courbes confondues : une étiquette
            ax.text(quotas[-1] + 120, ends["négociation"] - 3, f"identiques\n{ends['négociation']:.0f} %",
                    va="center", color=INK2, fontsize=8.5)
        else:
            for label, v in ends.items():
                ax.text(quotas[-1] + 120, v, f"{label}\n{v:.0f} %", va="center", color=INK2, fontsize=8.5)
        ax.set_title(crop.capitalize(), fontsize=11)
        ax.set_xticks(quotas, [f"{q / 1000:g}" for q in quotas])
        ax.set_xlim(quotas[0] - 300, quotas[-1] + 1400)
        ax.set_xlabel("quota partagé (m³/jour)")
        ax.yaxis.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    axes[0].set_ylim(60, 102)
    axes[0].set_ylabel("transpiration réelle / sans stress (%)\n≈ rendement relatif")
    axes[0].legend(loc="lower right", frameon=False, fontsize=9, labelcolor=INK2)
    fig.suptitle("Même eau, mieux partagée : la négociation aide la tomate sans pénaliser l'olivier",
                 x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.tight_layout()
    fig.savefig(RES / "fig3_quota.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    fig_water()
    fig_moisture()
    fig_quota()
    print(f"graphiques enregistrés dans {RES}")