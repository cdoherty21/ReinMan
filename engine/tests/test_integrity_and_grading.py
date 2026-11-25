import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from reinman import grading, ingest, performance, predict
from reinman.util import to_iso


def _card(post, runners=4, track="AQU", d="2026-11-24", num=1):
    return {"meet": {"meet_id": f"{track}-{d}", "track_id": track, "track_name": track, "race_date": d},
            "races": [{"race_number": num, "post_time": post, "surface": "D", "distance_yards": 1320,
                       "track_condition": "Fast", "purse": 20000, "is_cancelled": False, "changes": [],
                       "runners": [{"program_number": str(i + 1), "post_position": i + 1, "horse_id": f"h{num}{i}",
                                    "horse_name": f"Horse {num}-{i+1}", "jockey_id": f"j{i}", "trainer_id": f"t{i}",
                                    "ml_odds": [1.5, 3.0, 5.0, 8.0, 10.0, 12.0][i], "live_odds": None,
                                    "scratched": False} for i in range(runners)]}]}


def _future(seconds):
    return to_iso(datetime.now(timezone.utc) + timedelta(seconds=seconds))


def test_predictions_are_append_only_and_pre_post(db):
    ingest.store_card(db, _card(_future(3600)), "t")
    out = predict.predict_date(db, "2026-11-24")
    assert out["snapshots_written"] == 1
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE predictions SET top1='9'")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM predictions")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM prediction_runners")
    # unchanged inputs do not create duplicate snapshots
    assert predict.predict_date(db, "2026-11-24")["snapshots_written"] == 0
    # a prediction for a race already off is rejected by the database itself
    ingest.store_card(db, _card(_future(-60), num=2), "t")
    with pytest.raises(sqlite3.DatabaseError):
        db.insert("predictions", {"race_id": "AQU-20261124-2", "model_version": "x", "reason": "t", "top1": "1",
                                  "most_likely": "1", "confidence": "Low", "confidence_score": 0, "decision": "pass",
                                  "take": "x", "inputs_hash": "x"})


def test_every_race_ranks_three_and_scratch_triggers_update(db):
    ingest.store_card(db, _card(_future(3600), runners=5), "t")
    predict.predict_date(db, "2026-11-24")
    p = db.one("SELECT * FROM predictions")
    assert p["top1"] and p["top2"] and p["top3"] and p["take"] and p["confidence"] in ("High", "Medium", "Low")
    pr = db.query("SELECT * FROM prediction_runners WHERE prediction_id=?", [p["prediction_id"]])
    assert sum(r["win_prob"] for r in pr) == pytest.approx(1.0)
    card = _card(_future(3600), runners=5)
    card["races"][0]["runners"][0]["scratched"] = True
    changed = ingest.store_card(db, card, "t")
    assert changed["AQU-20261124-1"] == ["scratch"]
    predict.predict_date(db, "2026-11-24", changed)
    latest = db.one("SELECT * FROM predictions ORDER BY prediction_id DESC LIMIT 1")
    assert latest["reason"] == "scratch"
    progs = {r["program_number"] for r in db.query("SELECT program_number FROM prediction_runners WHERE prediction_id=?",
                                                    [latest["prediction_id"]])}
    assert "1" not in progs
    assert db.scalar("SELECT COUNT(*) FROM predictions") == 2  # history preserved


def _result(fins, payoffs=(), scratches=()):
    return {"race_number": 1, "track_condition": "Fast", "finishers": fins, "payoffs": list(payoffs),
            "scratches": list(scratches)}


def test_lock_grade_and_stats(db):
    ingest.store_card(db, _card(_future(2)), "t")
    predict.predict_date(db, "2026-11-24")
    pid = db.scalar("SELECT prediction_id FROM predictions")
    top1 = db.scalar("SELECT top1 FROM predictions")
    # add a hypothetical wager on the locked snapshot to exercise grading
    db.insert("wagers", {"prediction_id": pid, "race_id": "AQU-20261124-1", "legs": ["AQU-20261124-1"],
                         "wager_type": "WIN", "structure": "straight", "selection": top1, "combos": 1,
                         "base_amount": 2.0, "cost": 2.0, "est_prob": 0.4})
    db.insert("wagers", {"prediction_id": pid, "race_id": "AQU-20261124-1", "legs": ["AQU-20261124-1"],
                         "wager_type": "EXACTA", "structure": "straight", "selection": f"{top1}-2 2-{top1}",
                         "combos": 2, "base_amount": 1.0, "cost": 2.0, "est_prob": 0.2})
    assert predict.lock_due(db) == 0
    time.sleep(2.2)
    assert predict.lock_due(db) == 1
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM locks")
    fins = [{"program_number": top1, "horse_name": "W", "finish_position": 1, "win_payoff": 5.0, "place_payoff": 3.0,
             "show_payoff": 2.5},
            {"program_number": "2" if top1 != "2" else "3", "horse_name": "P", "finish_position": 2,
             "place_payoff": 4.0, "show_payoff": 3.0}]
    second = fins[1]["program_number"]
    pay = [{"wager_type": "EXACTA", "base_amount": 2.0, "winning_numbers": f"{top1}-{second}", "payoff_amount": 18.0}]
    ingest.store_results(db, {"track_id": "AQU", "race_date": "2026-11-24"}, [_result(fins, pay)], "t")
    out = grading.grade_all(db)
    assert out == {"predictions_graded": 1, "wagers_graded": 2}
    g = db.one("SELECT * FROM grades")
    assert g["top1_finish"] == 1 and g["top1_win_payoff"] == 5.0 and g["winner_rank"] == 1
    wg = {r["wager_id"]: r for r in db.query("SELECT * FROM wager_grades")}
    win, ex = sorted(wg)
    assert wg[win]["status"] == "won" and wg[win]["payout"] == 5.0
    expected_ex = 9.0 if second == "2" else 0.0
    assert wg[ex]["payout"] == expected_ex
    assert grading.grade_all(db) == {"predictions_graded": 0, "wagers_graded": 0}  # idempotent
    stats = performance.compute(db)
    assert stats["picks"]["graded"] == 1 and stats["picks"]["wins"] == 1
    assert stats["picks"]["flat_win_net"] == 3.0
    assert stats["wagers"]["wagered"] == 4.0
    performance.refresh_stats(db)
    assert json.loads(db.scalar("SELECT json FROM stats_cache WHERE key='overall'"))["scope"] == "all"


class _R:
    def __init__(self, pos, scratched=(), payoffs=(), fin=(), official=True, cancelled=False, winners=None):
        self.pos, self.scratched, self.payoffs, self.fin = pos, set(scratched), list(payoffs), list(fin)
        self.official, self.cancelled = official, cancelled
        self.winners = winners if winners is not None else {e for e, p in pos.items() if p == 1}
        self.race = {"post_time": "2026-01-01T00:00:00.000Z"}

    def payoff_field(self, e, f):
        for x in self.fin:
            if x["program_number"] == e:
                return x.get(f)
        return None


NOW = datetime(2026, 1, 5, tzinfo=timezone.utc)


def _w(wt, sel, base, cost, legs=("r1",), structure="straight"):
    return {"wager_type": wt, "selection": sel, "base_amount": base, "cost": cost, "legs": list(legs),
            "structure": structure}


def test_grading_rules():
    r = _R({"3": 1, "5": 2, "1": 3}, fin=[{"program_number": "3", "win_payoff": 6.4, "place_payoff": 4.0,
                                           "show_payoff": 3.0}, {"program_number": "5", "place_payoff": 5.0,
                                                                  "show_payoff": 3.2}],
           payoffs=[{"wager_type": "EXACTA", "winning_numbers": "3-5", "payoff_amount": 40.0, "base_amount": 2.0},
                    {"wager_type": "TRIFECTA", "winning_numbers": "3-5-1", "payoff_amount": 90.0, "base_amount": 0.5}],
           scratched={"7"})
    g = grading.grade_wager(_w("WIN", "3", 2, 2), {"r1": r}, NOW)
    assert g["status"] == "won" and g["payout"] == 6.4
    assert grading.grade_wager(_w("PLACE", "5", 2, 2), {"r1": r}, NOW)["payout"] == 5.0
    assert grading.grade_wager(_w("WIN", "5", 2, 2), {"r1": r}, NOW)["status"] == "lost"
    assert grading.grade_wager(_w("WIN", "7", 2, 2), {"r1": r}, NOW)["status"] == "refunded"
    # coupled entry 3A counts as betting interest 3
    assert grading.grade_wager(_w("WIN", "3A", 2, 2), {"r1": r}, NOW)["status"] == "won"
    g = grading.grade_wager(_w("EXACTA", "3-5 5-3", 1, 2), {"r1": r}, NOW)
    assert g["payout"] == 20.0 and g["status"] == "won"
    g = grading.grade_wager(_w("TRIFECTA", "3-5-1 3-7-1", 0.5, 1.0), {"r1": r}, NOW)
    assert g["payout"] == 90.5 and g["refund"] == 0.5
    # cancelled race refunds
    rc = _R({}, cancelled=True)
    assert grading.grade_wager(_w("EXACTA", "1-2", 1, 1), {"r1": rc}, NOW)["status"] == "refunded"
    # not official yet -> no grade
    assert grading.grade_wager(_w("WIN", "1", 2, 2), {"r1": _R({}, official=False)}, NOW) is None


def test_dead_heat_and_multi_race():
    r = _R({"2": 1, "4": 1, "6": 3},
           payoffs=[{"wager_type": "EXACTA", "winning_numbers": "2-4", "payoff_amount": 30.0, "base_amount": 2.0},
                    {"wager_type": "EXACTA", "winning_numbers": "4-2", "payoff_amount": 28.0, "base_amount": 2.0}])
    assert grading.grade_wager(_w("EXACTA", "4-2", 2, 2), {"r1": r}, NOW)["payout"] == 28.0
    r1 = _R({"1": 1})
    r2 = _R({"5": 1}, payoffs=[{"wager_type": "DAILY_DOUBLE", "winning_numbers": "1/5", "payoff_amount": 22.0,
                                "base_amount": 2.0}])
    w = _w("DAILY_DOUBLE", "1,2/5,6", 1.0, 4.0, legs=("a", "b"), structure="ticket")
    g = grading.grade_wager(w, {"a": r1, "b": r2}, NOW)
    assert g["status"] == "won" and g["payout"] == 11.0
    # hit but no payoff published -> waits, then 'unpriced' after the grace period
    r2b = _R({"5": 1})
    assert grading.grade_wager(w, {"a": r1, "b": r2b}, datetime(2026, 1, 1, 1, tzinfo=timezone.utc)) is None
    assert grading.grade_wager(w, {"a": r1, "b": r2b}, NOW)["status"] == "unpriced"


def test_drawdown_and_streak():
    seq = [("a", -2, True), ("b", -2, True), ("c", 10, False), ("d", -2, True), ("e", -2, True), ("f", -2, True)]
    mdd, streak = performance._drawdown_and_streak(seq)
    assert mdd == 6 and streak == 3


def test_stale_tote_odds_give_a_watch_not_a_bet(db):
    card = _card(_future(3600), runners=4, track="PRX")
    for rn, (ml, live) in zip(card["races"][0]["runners"], [(9.0, 9.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]):
        rn.update(ml_odds=ml, live_odds=live)
    ingest.store_card(db, card, "puntersedge")
    db.execute("UPDATE races SET source='puntersedge'")
    # The tote read is 20 minutes old: too stale to bet into.
    stale = to_iso(datetime.now(timezone.utc) - timedelta(minutes=20))
    db.execute("UPDATE runners SET updated_at=?", [stale])
    db.execute("UPDATE odds_snapshots SET captured_at=?", [stale])
    predict.predict_date(db, "2026-11-24")
    row = db.one("SELECT prediction_id, decision, data_notes FROM predictions ORDER BY prediction_id DESC LIMIT 1")
    notes = json.loads(row["data_notes"])
    assert notes["odds_basis"] == "morning_line" and notes["odds_as_of"] == stale
    assert not db.query("SELECT * FROM wagers WHERE prediction_id=? AND wager_type='WIN'", [row["prediction_id"]])
    for w in notes["watch"]:
        assert w["min_odds"] > 0 and w["horse_name"].startswith("Horse")
