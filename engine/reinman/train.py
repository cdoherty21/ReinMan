"""Continuous learning: train candidates, validate out-of-sample, promote or reject.

Chronological protocol (no shuffling, no look-ahead):
    train  : races before  T_valid
    valid  : races in [T_valid, T_test)  — early stopping, blend weights, λ fit
    test   : races in [T_test, end]      — untouched until the promotion decision
A candidate replaces the champion only if, on the *same* test races, its mean
log-loss is lower and a paired bootstrap 95% interval of the difference excludes
zero, and calibration error is not materially worse. Every decision is written to
the append-only ``model_events`` audit trail.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from .config import POLICY
from .db import Database
from .features import RaceSample, build_dataset
from .models import Ensemble, ExplodedLogit, GBMScorer
from .probability import DEFAULT_LAMBDAS, fit_lambdas
from .registry import champion, log_event, new_version, promote, register, reject

log = logging.getLogger("reinman.train")


def market_odds(s: RaceSample) -> np.ndarray | None:
    live = np.array([r["live_odds"] if r["live_odds"] is not None else np.nan for r in s.runners], float)
    if np.all(np.isfinite(live)):
        return live
    ml = np.array([r["ml_odds"] if r["ml_odds"] is not None else np.nan for r in s.runners], float)
    return ml if np.all(np.isfinite(ml)) else None


def evaluate(model, samples: list[RaceSample]) -> dict:
    lls, briers, hits, ece_acc = [], [], 0, np.zeros((10, 3))
    for s in samples:
        p, _ = model.predict(s.X, market_odds(s))
        win = (s.finish == 1).astype(float)
        if win.sum() == 0:
            continue
        lls.append(-math.log(max(float((p * win).sum() / win.sum()), 1e-12)))
        briers.append(float(((p - win / win.sum()) ** 2).sum()))
        hits += int(win[int(np.argmax(p))] == 1)
        for pi, wi in zip(p, win):
            b = min(9, int(pi * 10))
            ece_acc[b] += (pi, wi, 1)
    n = len(lls)
    ece = float(sum(abs(a[0] - a[1]) for a in ece_acc if a[2]) / max(1, ece_acc[:, 2].sum()))
    return {"races": n, "log_loss": float(np.mean(lls)) if n else None, "brier": float(np.mean(briers)) if n else None,
            "top_pick_win_pct": hits / n if n else None, "ece": ece, "per_race_ll": lls}


def paired_bootstrap(a: list[float], b: list[float], n_boot: int = 2000, seed: int = 11) -> tuple[float, float, float]:
    d = np.array(a) - np.array(b)
    rng = np.random.default_rng(seed)
    means = [float(d[rng.integers(0, len(d), len(d))].mean()) for _ in range(n_boot)]
    return float(d.mean()), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def split(samples: list[RaceSample], valid_frac=0.15, test_frac=0.15):
    dates = sorted({s.race_date for s in samples})
    if len(dates) < 10:
        raise ValueError("not enough race dates for a chronological split")
    n = len(samples)
    i_test = int(n * (1 - test_frac))
    i_valid = int(n * (1 - test_frac - valid_frac))
    # snap to date boundaries so no date straddles two sets
    d_test, d_valid = samples[i_test].race_date, samples[i_valid].race_date
    train = [s for s in samples if s.race_date < d_valid]
    valid = [s for s in samples if d_valid <= s.race_date < d_test]
    test = [s for s in samples if s.race_date >= d_test]
    return train, valid, test


def fit_candidate(train: list[RaceSample], valid: list[RaceSample]) -> tuple[Ensemble, dict]:
    Xs, fs = [s.X for s in train], [s.finish for s in train]
    Xv, fv = [s.X for s in valid], [s.finish for s in valid]
    logit = ExplodedLogit(l2=1e-3).fit(Xs, fs)
    gbm = GBMScorer().fit(Xs, fs, Xv, fv)
    ens = Ensemble(logit, gbm).fit_blend(Xv, fv, [market_odds(s) for s in valid])
    lam_samples = []
    for s in train[-3000:] + valid:
        p, _ = ens.predict(s.X, market_odds(s))
        order = [int(i) for i in np.argsort(s.finish) if np.isfinite(s.finish[i])][:3]
        if len(order) == 3 and s.finish[order[0]] == 1:
            lam_samples.append((p, order))
    lambdas = fit_lambdas(lam_samples) if lam_samples else DEFAULT_LAMBDAS
    artifact = ens.to_json()
    artifact["lambdas"] = list(lambdas)
    return ens, artifact


def train_and_maybe_promote(db: Database, end: str | None = None, force_register: bool = False) -> dict:
    samples = build_dataset(db, end=end)
    if len(samples) < POLICY.min_train_races + 2 * POLICY.min_valid_races:
        msg = {"status": "insufficient_data", "races": len(samples),
               "required": POLICY.min_train_races + 2 * POLICY.min_valid_races}
        log_event(db, None, "training_skipped", msg)
        return msg
    train, valid, test = split(samples)
    cand, artifact = fit_candidate(train, valid)
    champ_version, champ_model, _ = champion(db)
    cm = evaluate(cand, test)
    hm = evaluate(champ_model, test)
    diff, lo, hi = paired_bootstrap(cm["per_race_ll"], hm["per_race_ll"])
    window = f"train {train[0].race_date}..{train[-1].race_date}; valid {valid[0].race_date}..{valid[-1].race_date}; " \
             f"test {test[0].race_date}..{test[-1].race_date}"
    metrics = {k: v for k, v in cm.items() if k != "per_race_ll"}
    metrics.update(champion_version=champ_version,
                   champion_metrics={k: v for k, v in hm.items() if k != "per_race_ll"},
                   ll_diff=diff, ll_diff_ci=[lo, hi], train_races=len(train), valid_races=len(valid),
                   test_races=len(test), policy=POLICY.to_dict(),
                   logit_coefficients=dict(zip(artifact["logit"]["features"], artifact["logit"]["beta"])),
                   feature_importance=_importance(cand))
    version = new_version("ensemble")
    register(db, version, "ensemble", artifact, metrics, window)
    evidence = {"ll_diff": diff, "ci": [lo, hi], "ece_candidate": cm["ece"], "ece_champion": hm["ece"],
                "test_races": len(test)}
    if hi < 0 and cm["ece"] <= hm["ece"] + 0.01:
        promote(db, version, evidence)
        status = "promoted"
    else:
        reject(db, version, evidence)
        status = "rejected"
    return {"status": status, "version": version, **evidence}


def _importance(ens: Ensemble) -> dict:
    from .features import FEATURES
    if ens.gbm is None:
        return {}
    gains = ens.gbm.booster.feature_importance("gain")
    names = FEATURES + [f"rel_{f}" for f in FEATURES]
    tot = gains.sum() or 1.0
    pairs = sorted(zip(names, gains / tot), key=lambda x: -x[1])[:25]
    return {k: float(v) for k, v in pairs}

