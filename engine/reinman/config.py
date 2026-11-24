"""Tunable decision thresholds. Every value is recorded with the model version that
used it (in the model artifact's metrics) so results remain attributable."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Policy:
    # Win bets
    win_min_prob: float = 0.06
    win_max_odds: float = 30.0
    win_min_edge_live: float = 0.10      # EV per $1 required with live tote odds
    win_min_edge_ml: float = 0.20        # stricter when only the morning line is available
    # Place / show (only when per-horse pool amounts are available)
    ps_min_edge: float = 0.08
    # Exotics (EV is an estimate: market-implied payouts with takeout)
    exacta_min_edge: float = 0.25
    trifecta_min_edge: float = 0.35
    superfecta_min_edge: float = 0.50
    exotic_min_combo_prob: dict | None = None
    max_combos: dict | None = None
    # Multi-race
    multi_min_edge: float = 0.30
    multi_leg_coverage: float = 0.55     # cumulative win probability covered per leg
    multi_max_per_leg: int = 4
    multi_max_cost: float = 48.0
    # Confidence
    high_conf_score: float = 0.62
    med_conf_score: float = 0.42
    # Dark horse
    dark_min_odds: float = 8.0
    dark_min_ratio: float = 1.5          # model prob / market prob
    dark_min_prob: float = 0.06
    # Minimum graded races before a trained model may replace the market baseline
    min_train_races: int = 1500
    min_valid_races: int = 300

    def __post_init__(self):
        if self.exotic_min_combo_prob is None:
            object.__setattr__(self, "exotic_min_combo_prob",
                               {"EXACTA": 0.03, "TRIFECTA": 0.008, "SUPERFECTA": 0.002})
        if self.max_combos is None:
            object.__setattr__(self, "max_combos", {"EXACTA": 4, "TRIFECTA": 6, "SUPERFECTA": 10})

    def to_dict(self):
        return asdict(self)


POLICY = Policy()
