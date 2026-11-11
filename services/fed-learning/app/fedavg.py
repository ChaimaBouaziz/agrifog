"""FedAvg (McMahan et al., 2017) — the aggregation used by Flower's default strategy.

Hierarchical, two levels:
    1. inside a fog : average of its zones (each zone = one client)
    2. between fogs : average of the fog models, each weighted by its number of samples
Only weights and sample counts travel; the raw data never leaves the zone.
"""

import numpy as np

from .model import Weights, train


def fedavg(updates: list[tuple[Weights, int]]) -> tuple[Weights, int]:
    """Weighted mean of models. Returns (weights, total number of samples)."""
    total = sum(n for _, n in updates)
    if total == 0:
        raise ValueError("aucune donnée")
    layers = len(updates[0][0])
    avg = [sum(w[i] * (n / total) for w, n in updates) for i in range(layers)]
    return avg, total


def fog_round(global_w: Weights, zone_data: dict[str, tuple[np.ndarray, np.ndarray]],
              epochs: int, seed: int = 0) -> tuple[Weights, int]:
    """Level 1: each zone trains from the global model on its own data, the fog averages them."""
    updates = []
    for i, (zone, (X, y)) in enumerate(sorted(zone_data.items())):
        if len(X):
            updates.append((train(global_w, X, y, epochs=epochs, seed=seed + i), len(X)))
    return fedavg(updates)
