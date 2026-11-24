"""TEST-ONLY synthetic race generator.

Produces canonical cards/results from a known ground-truth process so tests can
verify that the pipeline (ingest -> features -> model -> grading) behaves
correctly. Nothing here is real racing data and it is never used in production.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np

TRACKS = ["AQU", "GP", "SA"]


class World:
    def __init__(self, seed=1, n_horses=900, n_people=40, n_sires=30):
        self.rng = np.random.default_rng(seed)
        r = self.rng
        self.ability = r.normal(0, 1, n_horses)
        self.turf_pref = r.normal(0, 0.6, n_horses)
        self.sire = r.integers(0, n_sires, n_horses)
        self.sire_turf = r.normal(0, 0.5, n_sires)
        self.jockey = r.normal(0, 0.5, n_people)
        self.trainer = r.normal(0, 0.5, n_people)
        self.horse_trainer = r.integers(0, n_people, n_horses)
        self.n_people = n_people

    def race(self, d: str, track: str, num: int, post: datetime, finished: bool):
        r = self.rng
        n = int(r.integers(6, 11))
        horses = r.choice(len(self.ability), n, replace=False)
        surface = "T" if r.random() < 0.3 else "D"
        jockeys = r.integers(0, self.n_people, n)
        u = (self.ability[horses] + 0.6 * self.jockey[jockeys] + 0.6 * self.trainer[self.horse_trainer[horses]]
             + (self.turf_pref[horses] + self.sire_turf[self.sire[horses]] if surface == "T" else 0))
        u = 1.3 * u
        true_p = np.exp(u - u.max())
        true_p /= true_p.sum()
        noisy = np.exp(np.log(true_p) * 0.6 + r.normal(0, 0.5, n))
        ml_p = noisy / noisy.sum() * 1.20
        close_noisy = np.exp(np.log(true_p) * 0.85 + r.normal(0, 0.25, n))
        close_p = close_noisy / close_noisy.sum() * 1.18
        ml = np.round(np.maximum(1 / ml_p - 1, 0.4) * 2) / 2
        close = np.round(np.maximum(1 / close_p - 1, 0.1) * 10) / 10
        runners = []
        for i, h in enumerate(horses):
            runners.append({
                "program_number": str(i + 1), "post_position": i + 1, "horse_id": f"reg:H{h}",
                "horse_name": f"SIM HORSE {h}", "jockey_id": f"j:{jockeys[i]}", "jockey_name": f"Jockey {jockeys[i]}",
                "trainer_id": f"t:{self.horse_trainer[h]}", "trainer_name": f"Trainer {self.horse_trainer[h]}",
                "sire": f"SIRE {self.sire[h]}", "dam": None, "dam_sire": None, "weight": 120.0,
                "equipment": None, "medication": "L", "claiming": None,
                "ml_odds": float(ml[i]), "live_odds": float(close[i]) if finished else None,
                "coupled_type": None, "scratched": False, "extra": None,
            })
        race = {"race_number": num, "post_time": post.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "surface": surface,
                "distance_yards": 1320 if r.random() < 0.6 else 1870, "distance_desc": None, "race_type": "CLM",
                "race_class": "Claiming", "race_name": None, "grade": None, "purse": 30000, "min_claim": None,
                "max_claim": 20000, "age_restriction": None, "sex_restriction": None, "breed": "TB",
                "pools": [{"wager_type": w} for w in ("EXACTA", "TRIFECTA", "SUPERFECTA")],
                "track_condition": "Fast", "weather_desc": None, "temp_f": 70.0, "precip_pct": 0.0,
                "is_cancelled": False, "changes": [], "runners": runners}
        result = None
        if finished:
            # Plackett–Luce draw from the true probabilities
            g = np.log(true_p) + r.gumbel(size=n)
            order = list(np.argsort(-g))
            fins = []
            for pos, i in enumerate(order):
                fins.append({"program_number": str(i + 1), "horse_name": f"SIM HORSE {horses[i]}",
                             "finish_position": pos + 1,
                             "win_payoff": round(2 * (close[i] + 1), 2) if pos == 0 else None,
                             "place_payoff": round(2 + close[i] * 0.8, 2) if pos <= 1 else None,
                             "show_payoff": round(2 + close[i] * 0.5, 2) if pos <= 2 else None,
                             "dead_heat": False})
            cp = 1 / (close + 1)
            cp /= cp.sum()
            e = order[:2]
            ex_pay = 2 * 0.79 / (cp[e[0]] * cp[e[1]] / (1 - cp[e[0]]))
            result = {"race_number": num, "track_condition": "Fast", "surface": surface, "winning_time": 70.1,
                      "fractions": [], "finishers": fins, "scratches": [],
                      "payoffs": [{"wager_type": "EXACTA", "wager_name": "$2 Exacta", "base_amount": 2.0,
                                   "winning_numbers": f"{e[0]+1}-{e[1]+1}", "payoff_amount": round(ex_pay, 2),
                                   "total_pool": None, "carryover": None}]}
        return race, result

    def meet(self, d: str, track: str, n_races: int, first_post: datetime, finished: bool, spacing_min=30):
        meet = {"meet_id": f"{track}-{d}", "track_id": track, "track_name": track, "race_date": d,
                "country": "USA", "source": "test_sim"}
        races, results = [], []
        for k in range(n_races):
            race, res = self.race(d, track, k + 1, first_post + timedelta(minutes=spacing_min * k), finished)
            races.append(race)
            if res:
                results.append(res)
        return {"meet": meet, "races": races}, results


def history(world: World, db, start: str, days: int, races_per_track=8, tracks=TRACKS):
    from reinman import ingest
    d0 = date.fromisoformat(start)
    for k in range(days):
        d = (d0 + timedelta(days=k)).isoformat()
        for t in tracks:
            post = datetime.fromisoformat(d).replace(hour=17, tzinfo=timezone.utc)
            card, results = world.meet(d, t, races_per_track, post, finished=True)
            ingest.store_card(db, card, "test_sim")
            ingest.store_results(db, card["meet"], results, "test_sim")
