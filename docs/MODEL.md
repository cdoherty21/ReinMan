# The Prediction Engine

## Approaches evaluated

| Approach | Use in Rein Man | Why |
|---|---|---|
| Logistic regression | **Yes** — as a conditional (multinomial) logit across the field, fitted on the top-3 finish order (exploded logit / Plackett–Luce) | The classic, well-calibrated racing model (Bolton & Chapman 1986). Interpretable coefficients drive Rein Man's written evidence. |
| Gradient boosting (LightGBM) | **Yes** — raw + race-relative features, probabilities via fitted softmax temperature | Captures interactions (e.g. off-track × pedigree, layoff × trainer) the linear model cannot. |
| XGBoost | Not separately | Same model family as LightGBM; LightGBM is lighter on CPU in Actions. Swappable in `GBMScorer`. |
| Ensemble | **Yes** — second-stage conditional logit over both fundamental models + log market probability | Benter (1994): combining a fundamental model with the public's odds beats either alone. |
| Bayesian estimates | **Yes** — empirical-Bayes shrinkage of every rate (horse, jockey, trainer, combo, sire, post) toward its prior | Small-sample rates (2-for-3) are otherwise wildly overstated. |
| Monte Carlo | **Yes** — seeded simulation of finishing orders for expected finish, large fields, pool-based place/show EV | Reproducible (seed = race id). |

## Outputs per horse
Win / place / show probability, expected finish, fair odds (1/p − 1), market probability,
win EV at the odds used, uncertainty (component disagreement), and evidence lines.
Per race: projected pace (only when running-position data exists — otherwise shown as
unavailable), confidence (High/Medium/Low from top-pick strength, separation, data
completeness and model agreement), play/pass.

## Finish-order model
Discounted Harville (Lo, Bacon-Shone & Busche 1995): strength for k-th place is p^λk with
λ = (1, 0.81, 0.65, 0.55) by default; λ2, λ3 are re-fitted on our own results at training.

## Leakage, overfitting and hindsight controls
* Features for date D only use results before D (`HistoryBuilder.asof`); enforced by a test
  that adds a race's own result and asserts its features don't change.
* Splits are chronological and snapped to date boundaries (train → valid → test).
* Fundamental model never sees tote odds; the blend learns how much to trust the market
  on the validation set only.
* Backtests decide bets with pre-race information (morning line); a separate line reports
  closing-odds bets and labels them as unattainable.
* L2 regularization, LightGBM early stopping, min-leaf sizes, and shrinkage.

## Promotion gate (continuous learning)
A candidate replaces the champion only if, on the same untouched test races, mean
log-loss improves with a paired-bootstrap 95% CI entirely below zero **and** calibration
error (ECE) is not worse by more than 0.01. All registrations, promotions, rejections
and drift warnings go to the append-only `model_events` table.

Minimum data before any trained model is considered: 1,500 training + 2×300 validation/test
races (`config.Policy`). Before that, `v0-market-baseline` is champion and every Take says so.

## Wagers (Phase 6)
* **Win:** EV = p·(odds+1) − 1, bet only on fresh tote odds (read within 5 minutes) with ≥10% edge.
  Value against a morning line (≥20% edge) is not a bet: it is listed as *Watch at the tote* with
  the lowest tote price that would clear the 10% rule, rounded up to the next standard price
  (`wagers.win_watch`). Watch items are kept in the pick's `data_notes`, not in `wagers`, so
  they never count in recommended-bet results. Each pick also records when the tote was last read.
* **Place/Show:** only with per-horse pool amounts; pari-mutuel payout formula with takeout,
  $2.10 minimum, averaged over simulated finishing orders.
* **Exacta/Trifecta/Superfecta:** combination probabilities from the finish-order model;
  payouts estimated from market-implied combination probability and takeout. **Only with
  live tote odds** — in testing, morning-line-priced exotics were badly over-optimistic.
* **Daily Double / Pick 3–6:** legs covered to ~55% model probability (≤4 per leg, plus one
  strong overlay), only when the pool is offered and all legs have live odds; cost cap $48.
* Dead heats, refunds, scratches and official payouts are handled at grading.

## Benchmarks tracked
Morning-line favorite, public (tote) favorite, market-implied log-loss, uniform baseline;
walk-forward backtests report the same per month.

## What has and hasn't been validated
The pipeline is verified on a **synthetic test world** (tests only): the model recovers real
signal beyond the morning line out-of-sample, leakage checks pass, and the gate rejects
non-significant improvements. **No claim of profitability on real races is made.** That
requires live, graded results over a meaningful sample — which the Performance page tracks.

## Progressive learning

Daily training attempts use the available official races. The first candidate is a
strongly regularized conditional-logit model, eligible with at least 200 training,
50 validation and 50 untouched test races across at least 10 race dates. Date-based
splits can require more races. At 1,500 training races, the LightGBM component is
added. Both stages must clear the existing paired log-loss and calibration promotion
gate. Historical market input uses morning lines to avoid depending on closing odds
that may have been recorded after post time.

Uploaded program and result-chart records supplement tracked horse history, including
prior finishes, recency, distance, track, condition and speed/pace where available.
Records are deduplicated by horse and race date; missing positions or context remain
unknown. Later cards cannot add past-performance records to earlier training races.
Sparse card refreshes preserve supplemental runner data for the same horse.
