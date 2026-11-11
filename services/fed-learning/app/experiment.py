"""Experiment for the report: local models vs federated model vs centralised (upper bound).

Uses the histories of BOTH fogs on one PC — only to measure, for the report.
In production each fog keeps its own file (see federated.py).

    python -m app.experiment                     # 200 tours × 2 époques, ~2 s

FedAvg needs MANY short rounds (here 200 × 2 epochs) to converge: each round only moves the
global model a little. Local and centralised models get the same number of epochs per sample.
"""

import argparse

import numpy as np

from agrifog_common import load_config
from agrifog_common.config import REPO_ROOT

from .features import FEATURES, CropInfo, load_history, split_by_time, zone_samples
from .fedavg import fedavg, fog_round
from .model import init_weights, mae_pct, n_bytes, train

FOGS = ("fog-a", "fog-b")
IRRIG, RAIN = FEATURES.index("irrig_next"), FEATURES.index("rain_next")


def load_all(train_frac: float):
    """{fog: {zone: ((Xtr, ytr), (Xte, yte))}}"""
    data = {}
    for fog in FOGS:
        crop = CropInfo.from_config(load_config(fog))
        hist = load_history(REPO_ROOT / "data" / f"history_{fog}.csv")
        data[fog] = {z: split_by_time(*zone_samples(rows, crop), train_frac) for z, rows in hist.items()}
    return data


def run(rounds: int, epochs: int, train_frac: float = 0.5, seed: int = 0, verbose: bool = True) -> dict:
    data = load_all(train_frac)
    clients = [(f, z) for f in FOGS for z in sorted(data[f])]
    test = {c: data[c[0]][c[1]][1] for c in clients}
    train_ = {c: data[c[0]][c[1]][0] for c in clients}

    def avg_mae(w_of, events_only=False):  # w_of(client) -> poids à évaluer sur le test de ce client
        out = {}
        for f in FOGS:
            errs = []
            for z in sorted(data[f]):
                X, y = test[(f, z)]
                if events_only:  # arrosage ou pluie dans les 6 h : les moments qui comptent
                    mask = (X[:, IRRIG] > 0) | (X[:, RAIN] > 0.02)
                    if not mask.any():
                        continue
                    X, y = X[mask], y[mask]
                errs.append(mae_pct(w_of((f, z)), X, y))
            out[f] = float(np.mean(errs))
        return out

    # 1. chaque zone seule, avec le même budget d'entraînement total
    local = {c: train(init_weights(seed), *train_[c], epochs=rounds * epochs, seed=seed) for c in clients}  # même nb d'époques
    res_local = avg_mae(lambda c: local[c])
    ev_local = avg_mae(lambda c: local[c], True)

    # 2. fédéré hiérarchique : zones -> fog -> global
    w = init_weights(seed)
    history = []
    for r in range(1, rounds + 1):
        fog_models = [fog_round(w, {z: train_[(f, z)] for z in data[f]}, epochs, seed + r) for f in FOGS]
        w, _ = fedavg(fog_models)
        history.append(avg_mae(lambda c: w))
        if verbose and (r in (1, rounds) or r % 50 == 0):
            print(f"  tour {r:2d} : MAE fédéré  fog-a {history[-1]['fog-a']:.2f} %   fog-b {history[-1]['fog-b']:.2f} %")
    res_fed = history[-1]
    ev_fed = avg_mae(lambda c: w, True)

    # 3. centralisé (toutes les données au même endroit) : borne haute, mais pas confidentiel
    Xall = np.vstack([train_[c][0] for c in clients])
    yall = np.concatenate([train_[c][1] for c in clients])
    central = train(init_weights(seed), Xall, yall, epochs=rounds * epochs, seed=seed)
    res_central = avg_mae(lambda c: central)
    ev_central = avg_mae(lambda c: central, True)

    n_samples = sum(len(train_[c][0]) for c in clients)
    fl_bytes = rounds * len(FOGS) * 2 * n_bytes(w)          # chaque fog : 1 envoi + 1 réception par tour
    raw_bytes = n_samples * 9 * 4 + n_samples * 25          # ~9 valeurs + horodatage par mesure brute
    return {"local": res_local, "federated": res_fed, "central": res_central, "history": history,
            "events": {"local": ev_local, "federated": ev_fed, "central": ev_central},
            "fl_bytes": fl_bytes, "raw_bytes": raw_bytes, "train_samples": n_samples}


def rounds_label(res: dict) -> str:
    return f"{len(res['history'])} tours"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--train-frac", type=float, default=0.3, help="part des jours pour l'entraînement")
    args = ap.parse_args()
    print(f"Apprentissage fédéré : {args.rounds} tours × {args.epochs} époques, 6 clients (3 zones × 2 fogs)")
    res = run(args.rounds, args.epochs, args.train_frac)
    print("\nErreur moyenne sur l'humidité à +6 h (jours de test, % vol) :")
    print(f"  {'':28}  fog-a (tomate)   fog-b (olivier)")
    for name, key in (("modèle local (zone seule)", "local"), ("modèle fédéré (AgriFog)", "federated"),
                      ("centralisé (non confidentiel)", "central")):
        print(f"  {name:28}  {res[key]['fog-a']:10.2f}   {res[key]['fog-b']:12.2f}")
    print("\nMême erreur, seulement quand il y a arrosage ou pluie dans les 6 h (ce qui compte pour décider) :")
    for name, key in (("modèle local (zone seule)", "local"), ("modèle fédéré (AgriFog)", "federated"),
                      ("centralisé (non confidentiel)", "central")):
        print(f"  {name:28}  {res['events'][key]['fog-a']:10.2f}   {res['events'][key]['fog-b']:12.2f}")
    for fog in FOGS:
        gain = 100 * (res["local"][fog] - res["federated"][fog]) / res["local"][fog]
        print(f"  -> {fog} : le fédéré réduit l'erreur de {gain:.0f} % par rapport au modèle local")
    per_round = n_bytes(init_weights(0))
    season_raw = 20 * 96 * 183 * 150   # 20 ESP32, 1 message / 15 min, 183 jours, ~150 octets JSON
    print(f"\nDonnées échangées : un modèle = {per_round} octets. Cet entraînement ({rounds_label(res)}) : "
          f"{res['fl_bytes'] / 1024:.0f} Ko de poids.")
    print(f"Centraliser une saison de mesures brutes des 2 fogs : ~{season_raw / 1e6:.0f} Mo "
          f"(le volume des poids, lui, ne dépend pas de la quantité de données).")


if __name__ == "__main__":
    main()
