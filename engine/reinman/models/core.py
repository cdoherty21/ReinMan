"""Reproducible statistical win-probability models.

Stage 1 ("fundamental"):  two independent models on point-in-time features
  * ``ExplodedLogit`` — conditional (multinomial) logit fitted on the top-3 order of
    finish (Plackett–Luce / "exploded" logit) with L2 regularization.
  * ``GBMScorer`` — LightGBM gradient-boosted trees on raw + race-relative features,
    turned into within-race probabilities by a fitted softmax temperature. Captures
    interactions between variables the linear model cannot.
Stage 2 ("blend"): a small conditional logit on validation data that combines the
  two fundamental log-probabilities with the log market probability (Benter, 1994).
  When no market odds exist for a race, a no-market blend is used.

Every number is produced by fitted coefficients stored in a JSON artifact, so any
prediction can be reproduced exactly from (artifact, features).
"""
from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

from ..features import FEATURES, RACE_CONSTANT

LOGIT_FEATURES = [f for f in FEATURES if f not in RACE_CONSTANT]


# ---------------------------------------------------------------- utilities
def race_softmax(u: np.ndarray) -> np.ndarray:
    z = u - np.max(u)
    e = np.exp(z)
    return e / e.sum()


def pad(races_u: list[np.ndarray], fill=-np.inf) -> tuple[np.ndarray, np.ndarray]:
    m = max(len(u) for u in races_u)
    out = np.full((len(races_u), m), fill, dtype=float)
    mask = np.zeros((len(races_u), m), dtype=bool)
    for i, u in enumerate(races_u):
        out[i, :len(u)] = u
        mask[i, :len(u)] = True
    return out, mask


@dataclass
class Standardizer:
    """Race-relative centering (NaN -> race mean -> 0) then global scaling."""
    cols: list[int]
    scale: np.ndarray | None = None

    def center(self, X: np.ndarray) -> np.ndarray:
        Z = X[:, self.cols].astype(float)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            mu = np.nanmean(Z, axis=0)
        mu = np.where(np.isfinite(mu), mu, 0.0)
        Z = Z - mu
        return np.where(np.isfinite(Z), Z, 0.0)

    def fit(self, Xs: list[np.ndarray]) -> "Standardizer":
        C = np.vstack([self.center(X) for X in Xs])
        s = C.std(axis=0)
        self.scale = np.where(s > 1e-9, s, 1.0)
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return self.center(X) / self.scale

    def to_json(self):
        return {"cols": self.cols, "scale": self.scale.tolist()}

    @classmethod
    def from_json(cls, d):
        return cls(d["cols"], np.array(d["scale"]))


# ---------------------------------------------------------------- exploded logit
def _top_k_orders(finish: np.ndarray, k: int) -> list[int]:
    """Indices of runners finishing 1..k (dead heats ordered arbitrarily but stably)."""
    idx = [i for i in np.argsort(finish, kind="stable") if np.isfinite(finish[i]) and finish[i] <= k]
    return idx[:k]


def fit_conditional_logit(Zs: list[np.ndarray], orders: list[list[int]], l2: float,
                          offsets: list[np.ndarray] | None = None) -> np.ndarray:
    """Maximize Plackett–Luce likelihood of observed partial orders. Returns beta."""
    R = len(Zs)
    p = Zs[0].shape[1]
    M = max(len(z) for z in Zs)
    Zp = np.zeros((R, M, p))
    mask = np.zeros((R, M), dtype=bool)
    off = np.zeros((R, M))
    for i, z in enumerate(Zs):
        Zp[i, :len(z)] = z
        mask[i, :len(z)] = True
        if offsets is not None:
            off[i, :len(z)] = offsets[i]
    K = max(len(o) for o in orders)
    chosen = np.full((R, K), -1, dtype=int)
    for i, o in enumerate(orders):
        chosen[i, :len(o)] = o
    n_obs = max(1, int((chosen >= 0).sum()))

    def f(beta):
        U = np.einsum("rmp,p->rm", Zp, beta) + off
        avail = mask.copy()
        ll = 0.0
        grad = np.zeros(p)
        for s in range(K):
            c = chosen[:, s]
            ok = c >= 0
            if not ok.any():
                break
            Us = np.where(avail, U, -np.inf)[ok]
            mx = Us.max(axis=1, keepdims=True)
            E = np.exp(Us - mx)
            S = E.sum(axis=1, keepdims=True)
            P = E / S
            rows = np.nonzero(ok)[0]
            ll += (U[rows, c[ok]] - (mx[:, 0] + np.log(S[:, 0]))).sum()
            grad += Zp[rows, c[ok]].sum(axis=0) - np.einsum("rm,rmp->p", P, Zp[rows])
            avail[rows, c[ok]] = False
        obj = -ll / n_obs + l2 * beta @ beta
        g = -grad / n_obs + 2 * l2 * beta
        return obj, g

    res = minimize(f, np.zeros(p), jac=True, method="L-BFGS-B", options={"maxiter": 500})
    return res.x


class ExplodedLogit:
    kind = "exploded_logit"

    def __init__(self, l2: float = 1e-3, top_k: int = 3):
        self.l2, self.top_k = l2, top_k
        self.std: Standardizer | None = None
        self.beta: np.ndarray | None = None

    def fit(self, Xs, finishes):
        self.std = Standardizer([FEATURES.index(f) for f in LOGIT_FEATURES]).fit(Xs)
        Zs = [self.std.transform(X) for X in Xs]
        orders = [_top_k_orders(fz, self.top_k) for fz in finishes]
        self.beta = fit_conditional_logit(Zs, orders, self.l2)
        return self

    def utilities(self, X):
        return self.std.transform(X) @ self.beta

    def contributions(self, X) -> np.ndarray:
        """Per-runner, per-feature utility contributions (race-relative)."""
        return self.std.transform(X) * self.beta

    def to_json(self):
        return {"l2": self.l2, "top_k": self.top_k, "std": self.std.to_json(), "beta": self.beta.tolist(),
                "features": LOGIT_FEATURES}

    @classmethod
    def from_json(cls, d):
        m = cls(d["l2"], d["top_k"])
        m.std, m.beta = Standardizer.from_json(d["std"]), np.array(d["beta"])
        return m


# ---------------------------------------------------------------- gradient boosting
class GBMScorer:
    kind = "lightgbm"

    def __init__(self, params: dict | None = None, rounds: int = 400):
        self.params = params or {
            "objective": "binary", "learning_rate": 0.03, "num_leaves": 15, "min_data_in_leaf": 40,
            "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 5.0,
            "verbose": -1, "seed": 7, "deterministic": True, "num_threads": 1,
        }
        self.rounds = rounds
        self.booster = None
        self.temp = 1.0
        self.std = Standardizer(list(range(len(FEATURES))))

    def _design(self, X):
        rel = self.std.center(X)  # race-relative copy of every feature
        return np.hstack([X, rel])

    def fit(self, Xs, finishes, Xv=None, fv=None):
        import lightgbm as lgb
        D = np.vstack([self._design(X) for X in Xs])
        y = np.concatenate([(f == 1).astype(float) for f in finishes])
        dtrain = lgb.Dataset(D, y, free_raw_data=False)
        valid = []
        if Xv:
            Dv = np.vstack([self._design(X) for X in Xv])
            yv = np.concatenate([(f == 1).astype(float) for f in fv])
            valid = [lgb.Dataset(Dv, yv, reference=dtrain)]
        cb = [lgb.early_stopping(50, verbose=False)] if valid else []
        self.booster = lgb.train(self.params, dtrain, num_boost_round=self.rounds, valid_sets=valid or None,
                                 callbacks=cb)
        # fit softmax temperature on the validation (or training) races
        Xc, fc = (Xv, fv) if Xv else (Xs, finishes)
        S = [self.raw(X) for X in Xc]
        winners = [int(np.nanargmin(np.where(np.isfinite(f), f, np.inf))) for f in fc]

        def nll(t):
            return -sum(math.log(max(race_softmax(t[0] * s)[w], 1e-12)) for s, w in zip(S, winners))
        self.temp = float(minimize(nll, [1.0], bounds=[(0.05, 5.0)], method="L-BFGS-B").x[0])
        return self

    def raw(self, X):
        return self.booster.predict(self._design(X), raw_score=True)

    def utilities(self, X):
        return self.temp * self.raw(X)

    def to_json(self):
        return {"params": self.params, "rounds": self.rounds, "temp": self.temp,
                "model": self.booster.model_to_string()}

    @classmethod
    def from_json(cls, d):
        import lightgbm as lgb
        m = cls(d["params"], d["rounds"])
        m.temp = d["temp"]
        m.booster = lgb.Booster(model_str=d["model"])
        return m


# ---------------------------------------------------------------- ensemble + blend
def market_logp(odds: np.ndarray) -> np.ndarray | None:
    """Log of overround-normalized market probabilities; None if any runner lacks odds."""
    if odds is None or len(odds) == 0 or np.any(~np.isfinite(odds)):
        return None
    p = 1.0 / (odds + 1.0)
    return np.log(p / p.sum())


class Ensemble:
    kind = "ensemble"

    def __init__(self, logit: ExplodedLogit | None, gbm: GBMScorer | None,
                 w_market=(1.0, 1.0, 0.0), w_nomarket=(1.0, 1.0)):
        self.logit, self.gbm = logit, gbm
        self.w_market = np.array(w_market, dtype=float)
        self.w_nomarket = np.array(w_nomarket, dtype=float)

    def components(self, X) -> dict[str, np.ndarray]:
        out = {}
        if self.logit is not None:
            out["logit"] = np.log(race_softmax(self.logit.utilities(X)))
        if self.gbm is not None:
            out["gbm"] = np.log(race_softmax(self.gbm.utilities(X)))
        return out

    def predict(self, X, odds: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
        comp = self.components(X)
        a = comp.get("logit", np.zeros(len(X)))
        b = comp.get("gbm", np.zeros(len(X)))
        m = market_logp(odds) if odds is not None else None
        if m is not None:
            u = self.w_market[0] * a + self.w_market[1] * b + self.w_market[2] * m
        else:
            u = self.w_nomarket[0] * a + self.w_nomarket[1] * b
        p = race_softmax(u)
        comps = {k: np.exp(v) for k, v in comp.items()}
        comps["fundamental"] = race_softmax(self.w_nomarket[0] * a + self.w_nomarket[1] * b)
        if m is not None:
            comps["market"] = np.exp(m)
        return p, comps

    def fit_blend(self, Xs, finishes, odds_list, l2: float = 1e-4):
        A = [self.components(X) for X in Xs]
        orders = [_top_k_orders(f, 1) for f in finishes]
        Zn = [np.column_stack([c.get("logit", np.zeros(len(X))), c.get("gbm", np.zeros(len(X)))])
              for c, X in zip(A, Xs)]
        self.w_nomarket = fit_conditional_logit(Zn, orders, l2)
        idx = [i for i, o in enumerate(odds_list) if o is not None and market_logp(o) is not None]
        if len(idx) >= 50:
            Zm = [np.column_stack([Zn[i], market_logp(odds_list[i])]) for i in idx]
            self.w_market = fit_conditional_logit(Zm, [orders[i] for i in idx], l2)
        else:
            self.w_market = np.array([*self.w_nomarket, 0.0])
        return self

    def to_json(self):
        return {"kind": self.kind, "features": FEATURES,
                "logit": self.logit.to_json() if self.logit else None,
                "gbm": self.gbm.to_json() if self.gbm else None,
                "w_market": self.w_market.tolist(), "w_nomarket": self.w_nomarket.tolist()}

    @classmethod
    def from_json(cls, d):
        return cls(ExplodedLogit.from_json(d["logit"]) if d.get("logit") else None,
                   GBMScorer.from_json(d["gbm"]) if d.get("gbm") else None,
                   d["w_market"], d["w_nomarket"])


class MarketBaseline:
    """Cold-start model (v0): normalized morning-line / market probabilities only.

    Used until enough graded history exists to train and validate Ensemble. It is
    an honest statistical baseline, clearly labeled in the UI.
    """
    kind = "market_baseline"

    def predict(self, X, odds: np.ndarray | None = None):
        m = market_logp(odds) if odds is not None else None
        if m is None:
            ml = X[:, FEATURES.index("ml_logp")]
            p = race_softmax(ml) if np.isfinite(ml).all() else np.full(len(X), 1 / len(X))
        else:
            p = np.exp(m)
        return p, {"market": p}

    def to_json(self):
        return {"kind": self.kind}


def load_model(artifact: str | dict):
    d = json.loads(artifact) if isinstance(artifact, str) else artifact
    if d["kind"] == "ensemble":
        if d.get("features") != FEATURES:
            raise ValueError("model artifact was trained on a different feature set")
        return Ensemble.from_json(d)
    if d["kind"] == "market_baseline":
        return MarketBaseline()
    raise ValueError(f"unknown model kind {d['kind']}")
