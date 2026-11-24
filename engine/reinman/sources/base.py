"""Source adapter interface.

Every adapter converts its provider's format into Rein Man's canonical dicts so the
rest of the system never depends on a single provider. Add a provider by writing
one class with these three methods and registering it in ``sources/__init__.py``.

Canonical card (one meet):
    {"meet": {meet_id, track_id, track_name, race_date, country, source, timezone?},
     "races": [{race_number, post_time(UTC ISO), surface, distance_yards, distance_desc,
                race_type, race_class, race_name, grade, purse, min_claim, max_claim,
                age_restriction, sex_restriction, breed, track_condition, weather_desc,
                temp_f, precip_pct, is_cancelled, changes:[{type,text}],
                runners:[{program_number, post_position, horse_id, horse_name,
                          jockey_id, jockey_name, trainer_id, trainer_name, sire, dam,
                          dam_sire, weight, equipment, medication, claiming, ml_odds,
                          live_odds, win_pool, place_pool, show_pool, coupled_type,
                          scratched, extra:{past_performances:[...], workouts:[...], ...}}]}]}

Canonical result (one race):
    {race_number, track_condition, surface, winning_time, fractions,
     finishers:[{program_number, horse_name, finish_position, win_payoff, place_payoff,
                 show_payoff, dead_heat}],
     payoffs:[{wager_type, wager_name, base_amount, winning_numbers, payoff_amount,
               total_pool, carryover}],
     scratches:[horse_name]}
"""
from __future__ import annotations

from typing import Protocol


class SourceAdapter(Protocol):
    name: str
    #: which handicapping inputs this source actually supplies (shown in the UI)
    capabilities: dict[str, bool]

    def meets(self, race_date: str) -> list[dict]: ...

    def card(self, meet: dict) -> dict: ...

    def results(self, meet: dict) -> list[dict]: ...


ALL_CAPABILITIES = [
    "entries", "morning_line", "live_odds", "win_place_show_pools", "scratches", "jockey_trainer",
    "equipment", "medication", "weight", "pedigree", "track_condition", "weather",
    "official_results", "exotic_payoffs", "fractional_times",
    "past_performances", "speed_figures", "pace_figures", "running_positions",
    "trouble_comments", "workouts", "beaten_lengths",
]
