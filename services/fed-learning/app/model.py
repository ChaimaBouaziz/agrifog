"""A small neural network in pure NumPy (12 -> 16 -> 1): light enough for any fog PC.

Its weights are a list of 4 arrays, which is all FedAvg needs.
"""

import numpy as np

from agrifog_common.ml import FEATURES, TARGET_SCALE
from agrifog_common.ml import predict as _predict

HIDDEN = 16
Weights = list[np.ndarray]


def init_weights(seed: int = 0) -> Weights:
    rng = np.random.default_rng(seed)
    n_in = len(FEATURES)
    return [rng.normal(0, 1 / np.sqrt(n_in), (n_in, HIDDEN)), np.zeros(HIDDEN),
            rng.normal(0, 1 / np.sqrt(HIDDEN), (HIDDEN, 1)), np.zeros(1)]


def predict(w: Weights, X: np.ndarray) -> np.ndarray:
    return _predict(w, X)


def train(w: Weights, X: np.ndarray, y: np.ndarray, epochs: int = 5, lr: float = 0.01,
          batch: int = 32, seed: int = 0) -> Weights:
    """Mini-batch Adam on the MSE. Returns NEW weights (the input is not modified)."""
    w = [a.copy() for a in w]
    m = [np.zeros_like(a) for a in w]
    v = [np.zeros_like(a) for a in w]
    rng, step = np.random.default_rng(seed), 0
    for _ in range(epochs):
        idx = rng.permutation(len(X))
        for start in range(0, len(X), batch):
            b = idx[start:start + batch]
            xb, yb = X[b], y[b]
            h = np.tanh(xb @ w[0] + w[1])
            err = (h @ w[2] + w[3]).ravel() - yb               # dérivée de la MSE (à 2/n près)
            g3 = np.array([err.mean()])
            g2 = h.T @ err[:, None] / len(b)
            dh = (err[:, None] @ w[2].T) * (1 - h ** 2)
            g1 = dh.mean(axis=0)
            g0 = xb.T @ dh / len(b)
            step += 1
            for i, g in enumerate((g0, g1, g2, g3)):
                m[i] = 0.9 * m[i] + 0.1 * g
                v[i] = 0.999 * v[i] + 0.001 * g ** 2
                mh, vh = m[i] / (1 - 0.9 ** step), v[i] / (1 - 0.999 ** step)
                w[i] -= lr * mh / (np.sqrt(vh) + 1e-8)
    return w


def mae_pct(w: Weights, X: np.ndarray, y: np.ndarray) -> float:
    """Mean absolute error on the moisture in 6 h, in % vol."""
    return float(np.mean(np.abs(predict(w, X) - y)) * TARGET_SCALE)


def n_bytes(w: Weights) -> int:
    return sum(a.size for a in w) * 4  # float32 sur le réseau


def to_lists(w: Weights) -> list:
    return [a.tolist() for a in w]


def from_lists(data: list) -> Weights:
    return [np.array(a, dtype=float) for a in data]
