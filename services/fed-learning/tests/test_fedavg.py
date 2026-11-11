import numpy as np
import pytest

from app.fedavg import fedavg, fog_round
from app.model import from_lists, init_weights, mae_pct, n_bytes, predict, to_lists, train


def toy_data(n=200, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.uniform(0, 1, (n, 12))
    y = 0.3 * X[:, 0] - 0.2 * X[:, 5] + 0.1 * X[:, 10]
    return X, y


def test_fedavg_is_a_weighted_mean():
    a = [np.ones((2, 2)), np.zeros(2)]
    b = [np.full((2, 2), 4.0), np.full(2, 2.0)]
    avg, n = fedavg([(a, 100), (b, 300)])
    assert n == 400
    assert np.allclose(avg[0], 3.25) and np.allclose(avg[1], 1.5)


def test_fedavg_refuses_empty():
    with pytest.raises(ValueError):
        fedavg([(init_weights(), 0)])


def test_training_reduces_error():
    X, y = toy_data()
    w0 = init_weights(0)
    w1 = train(w0, X, y, epochs=30)
    assert mae_pct(w1, X, y) < mae_pct(w0, X, y) / 2
    assert not np.allclose(w0[0], w1[0])        # les poids d'origine ne sont pas modifiés en place


def test_fog_round_averages_its_zones():
    X, y = toy_data()
    w, n = fog_round(init_weights(0), {"z1": (X[:100], y[:100]), "z2": (X[100:], y[100:])}, epochs=2)
    assert n == 200 and len(w) == 4


def test_weights_travel_as_json_lists():
    w = init_weights(1)
    assert all(np.allclose(a, b) for a, b in zip(w, from_lists(to_lists(w))))
    assert n_bytes(w) == (12 * 16 + 16 + 16 + 1) * 4
    assert predict(w, np.zeros((3, 12))).shape == (3,)
