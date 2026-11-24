"""Finish-order probabilities from win probabilities.

Plain Harville (Plackett–Luce with the win probabilities) is known to overstate the
chances of favorites finishing 2nd/3rd. Rein Man uses the discounted model of
Lo, Bacon-Shone & Busche (1995): the strength of runner i for k-th place is p_i^λ_k.
λ values default to the published estimates and are re-fitted on our own graded
exacta/trifecta outcomes by ``fit_lambdas`` once enough races exist.
"""
from __future__ import annotations

import hashlib
import itertools
import math

import numpy as np
from scipy.optimize import minimize

DEFAULT_LAMBDAS = (1.0, 0.81, 0.65, 0.55)


def _seed(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


def _strengths(p: np.ndarray, lam: float) -> np.ndarray:
    return np.power(np.clip(p, 1e-12, 1.0), lam)


def ordered_prob(p: np.ndarray, order: tuple[int, ...], lambdas=DEFAULT_LAMBDAS) -> float:
    """P(runners finish in exactly ``order`` for the first len(order) places)."""
    avail = np.ones(len(p), dtype=bool)
    prob = 1.0
    for k, i in enumerate(order):
        s = _strengths(p, lambdas[min(k, len(lambdas) - 1)]) * avail
        prob *= s[i] / s.sum()
        avail[i] = False
    return prob


def position_matrix(p: np.ndarray, depth: int = 3, lambdas=DEFAULT_LAMBDAS) -> np.ndarray:
    """Exact P(runner i finishes in place k) for k < depth, by enumeration."""
    n = len(p)
    depth = min(depth, n)
    P = np.zeros((n, depth))
    for order in itertools.permutations(range(n), depth):
        q = ordered_prob(p, order, lambdas)
        for k, i in enumerate(order):
            P[i, k] += q
    return P


def exotic_table(p: np.ndarray, positions: int, lambdas=DEFAULT_LAMBDAS) -> dict[tuple, float]:
    """All ordered combinations for exacta(2)/trifecta(3)/superfecta(4) with probabilities."""
    n = len(p)
    out = {}
    if positions > n:
        return out
    S = [_strengths(p, lambdas[min(k, len(lambdas) - 1)]) for k in range(positions)]
    for order in itertools.permutations(range(n), positions):
        prob, used = 1.0, 0.0
        removed = []
        for k, i in enumerate(order):
            denom = S[k].sum() - sum(S[k][j] for j in removed)
            prob *= S[k][i] / denom
            removed.append(i)
        out[order] = prob
    return out


def simulate(p: np.ndarray, n_sims: int = 20000, key: str = "", lambdas=DEFAULT_LAMBDAS) -> np.ndarray:
    """Monte Carlo finishing orders: array [n_sims, n] of runner indices by position."""
    rng = np.random.default_rng(_seed(key))
    n = len(p)
    avail = np.ones((n_sims, n), dtype=bool)
    orders = np.zeros((n_sims, n), dtype=int)
    for k in range(n):
        s = _strengths(p, lambdas[min(k, len(lambdas) - 1)])[None, :] * avail
        cs = np.cumsum(s, axis=1)
        u = rng.random(n_sims) * cs[:, -1]
        pick = (cs < u[:, None]).sum(axis=1)
        pick = np.minimum(pick, n - 1)
        orders[:, k] = pick
        avail[np.arange(n_sims), pick] = False
    return orders


def summarize(p: np.ndarray, key: str, lambdas=DEFAULT_LAMBDAS) -> dict:
    """Win/place/show and expected finish for every runner."""
    n = len(p)
    if n <= 12:
        P = position_matrix(p, 3, lambdas)
    else:
        sims = simulate(p, 40000, key, lambdas)
        P = np.zeros((n, 3))
        for k in range(min(3, n)):
            P[:, k] = np.bincount(sims[:, k], minlength=n) / len(sims)
    sims = simulate(p, 10000, key + ":ef", lambdas)
    exp_finish = np.zeros(n)
    for pos in range(n):
        exp_finish += (pos + 1) * np.bincount(sims[:, pos], minlength=n) / len(sims)
    win = P[:, 0]
    place = P[:, :2].sum(axis=1)
    show = P[:, :3].sum(axis=1) if P.shape[1] >= 3 else place
    return {"win": win, "place": place, "show": show, "exp_finish": exp_finish}


def fit_lambdas(samples: list[tuple[np.ndarray, list[int]]]) -> tuple[float, ...]:
    """Fit (λ2, λ3) by maximum likelihood on observed 2nd/3rd places given win probs."""
    def nll(x):
        lam = (1.0, x[0], x[1], x[1] * 0.85)
        tot = 0.0
        for p, order in samples:
            if len(order) < 3:
                continue
            q = ordered_prob(p, tuple(order[:3]), lam) / max(p[order[0]], 1e-12)
            tot -= math.log(max(q, 1e-15))
        return tot
    if len(samples) < 200:
        return DEFAULT_LAMBDAS
    r = minimize(nll, [0.81, 0.65], bounds=[(0.3, 1.2), (0.2, 1.2)], method="L-BFGS-B")
    return (1.0, float(r.x[0]), float(r.x[1]), float(r.x[1] * 0.85))


def entropy(p: np.ndarray) -> float:
    q = p[p > 0]
    return float(-(q * np.log(q)).sum() / math.log(max(len(p), 2)))
