"""Graphiques des expériences de la plateforme (semaine 7, Ines), même style que charts.py.

    python exp_pump.py && python exp_twin.py && python exp_ota.py && python charts_platform.py
    -> results/fig4_pompe.png, fig5_jumeau.png, fig6_ota.png
Couleurs : bleu = AgriFog (pump-scheduler, témoin, canary), gris = sans le mécanisme, orange = dégât.
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from charts import BASELINE, BLUE, INK, INK2, MUTED, ORANGE, RES, read  # noqa: E402  (style commun)


def fig_pump() -> None:
    rows = read("exp4_pump.csv")
    fig, ax = plt.subplots(figsize=(8, 3.8))
    labels = [r["policy"].replace("pump-scheduler", "AgriFog") for r in rows]
    overlap = [float(r["overlap_min_mean"]) for r in rows]
    colors = [BASELINE if r["settle_s"] == "" else (ORANGE if float(r["overlap_min_mean"]) > 0 else BLUE)
              for r in rows]
    y = list(range(len(rows)))[::-1]
    ax.barh(y, [max(o, 0.6) for o in overlap], height=0.55, color=colors)
    for yi, o, r in zip(y, overlap, rows):
        txt = (f"{o:.0f} min · {r['mornings_with_overlap']}/{r['mornings']} matins" if o > 0
               else "aucun chevauchement")
        ax.annotate(txt, (max(o, 0.6), yi), xytext=(6, 0), textcoords="offset points", va="center",
                    color=INK, fontsize=9)
    ax.set_yticks(y, labels, fontsize=9)
    ax.set_xlabel("deux vannes de fogs différents ouvertes en même temps (min simulées par matin)")
    ax.set_xlim(0, max(overlap) * 1.45)
    fig.suptitle("Pompe commune : aucun chevauchement tant que le pont répond en moins de 1,5 s",
                 x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    ax.grid(axis="x", color="#e1e0d9", linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(RES / "fig4_pompe.png", dpi=200)
    plt.close(fig)


def fig_twin() -> None:
    rows = read("exp5_twin_blips.csv")
    fig, ax = plt.subplots(figsize=(8, 3.6))
    for witness, color, marker, dy in (("sans témoin", BASELINE, "o", 8), ("avec témoin", BLUE, "s", -16)):
        pts = [(int(r["threshold_s"]), int(r["false_takeovers_24h"])) for r in rows if r["witness"] == witness]
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=color, linewidth=2, marker=marker, markersize=7, label=witness)
        for x, yv in pts:
            ax.annotate(str(yv), (x, yv), xytext=(0, dy), textcoords="offset points", ha="center",
                        color=INK, fontsize=9)
    blips = rows[0]["blips"]
    ax.set_xticks([10, 20, 30, 60], ["10 s\n(1 h sim)", "20 s\n(2 h sim)", "30 s\n(3 h sim)", "60 s\n(6 h sim)"])
    ax.set_xlabel("seuil de silence avant reprise = temps de détection d'une vraie panne")
    ax.set_ylabel(f"reprises à tort en 24 h\n({blips} coupures brèves du pont)")
    top = max(int(r["false_takeovers_24h"]) for r in rows)
    ax.set_ylim(-25, top * 1.18)
    ax.set_title("Fog jumeau : le témoin cloud supprime les reprises à tort (split-brain)")
    ax.legend(frameon=False, fontsize=9, labelcolor=INK2)
    ax.grid(axis="y", color="#e1e0d9", linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(RES / "fig5_jumeau.png", dpi=200)
    plt.close(fig)


def fig_ota() -> None:
    rows = read("exp6_ota.csv")
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4), gridspec_kw={"wspace": 0.45})
    for ax, firmware, title in ((axes[0], "crash", "firmware qui plante au démarrage"),
                                (axes[1], "sensor", "firmware qui ne mesure plus")):
        sub = {r["strategy"]: r for r in rows if r["firmware"] == firmware}
        vals = [float(sub["big bang"]["blind_hours_sim_mean"]), float(sub["canary"]["blind_hours_sim_mean"])]
        exposed = [sub["big bang"]["exposed_mean"], sub["canary"]["exposed_mean"]]
        ax.bar([0, 1], vals, width=0.55, color=[BASELINE, BLUE])
        for x, v, e in zip([0, 1], vals, exposed):
            ax.annotate(f"{v:.0f} h\n{float(e):.0f} ESP32 touchés", (x, v), xytext=(0, 4), textcoords="offset points",
                        ha="center", va="bottom", color=INK, fontsize=9)
        ax.set_xticks([0, 1], ["tout d'un coup", "canary + rollback"])
        ax.set_ylim(0, max(vals) * 1.35)
        ax.set_title(title, fontsize=10)
        ax.tick_params(axis="x", colors=INK2)
    axes[0].set_ylabel("heures (simulées) de parcelle\nsans mesure d'humidité")
    fig.suptitle("OTA sur 10 ESP32 : le canary limite un mauvais firmware à un seul capteur",
                 x=0.02, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.text(0.02, 0.01, "« ne mesure plus » sans canary : la parcelle reste aveugle jusqu'à ce qu'un humain s'en aperçoive "
             "(ici, la fin de l'expérience)", fontsize=8, color=MUTED)
    fig.subplots_adjust(top=0.78, bottom=0.2, left=0.12, right=0.98)
    fig.savefig(RES / "fig6_ota.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    RES.mkdir(exist_ok=True)
    fig_pump()
    fig_twin()
    fig_ota()
    print(sorted(p.name for p in RES.glob("fig[4-6]*.png")))
