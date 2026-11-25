import numpy as np
import pytest

from reinman.probability import exotic_table, ordered_prob, position_matrix, simulate, summarize
from reinman.wagers import multi_race_ticket, race_wagers, win_ev


def test_position_matrix_is_doubly_consistent():
    p = np.array([0.4, 0.25, 0.15, 0.1, 0.06, 0.04])
    P = position_matrix(p, 3)
    assert np.allclose(P.sum(axis=0), 1.0)
    assert np.allclose(P[:, 0], p)          # win column equals the win probabilities
    assert np.all(P.sum(axis=1) <= 1 + 1e-12)


def test_harville_special_case():
    p = np.array([0.5, 0.3, 0.2])
    q = ordered_prob(p, (0, 1), lambdas=(1, 1, 1, 1))
    assert q == pytest.approx(0.5 * 0.3 / 0.5)


def test_exotic_tables_sum_to_one():
    p = np.array([0.3, 0.25, 0.2, 0.15, 0.1])
    for k in (2, 3, 4):
        assert sum(exotic_table(p, k).values()) == pytest.approx(1.0)


def test_simulation_matches_exact():
    p = np.array([0.35, 0.25, 0.2, 0.12, 0.08])
    sims = simulate(p, 60000, "t")
    P = position_matrix(p, 3)
    for k in range(3):
        emp = np.bincount(sims[:, k], minlength=5) / len(sims)
        assert np.allclose(emp, P[:, k], atol=0.01)
    s = summarize(p, "x")
    assert s["exp_finish"].argmin() == 0 and np.all(s["show"] >= s["place"]) and np.all(s["place"] >= s["win"])


def test_simulation_is_reproducible():
    p = np.array([0.5, 0.3, 0.2])
    assert np.array_equal(simulate(p, 500, "k"), simulate(p, 500, "k"))


def test_win_ev_and_no_forced_bets():
    assert win_ev(0.25, 4.0) == pytest.approx(0.25)
    p = np.array([0.4, 0.3, 0.2, 0.1])
    fair = 1 / p - 1
    worse = (1 / (p * 1.2)) - 1   # every price below fair: no value anywhere
    w = race_wagers(p, ["1", "2", "3", "4"], worse, ["live"] * 4, None, "AQU", "r", None)
    assert w == []
    better = fair * 1.6
    w = race_wagers(p, ["1", "2", "3", "4"], better, ["live"] * 4, None, "AQU", "r", {"WIN", "PLACE", "SHOW"})
    assert any(x["wager_type"] == "WIN" for x in w)
    assert all(x["wager_type"] in ("WIN", "PLACE", "SHOW") for x in w)


def test_multi_race_ticket_requires_edge():
    p = np.array([0.5, 0.3, 0.2])
    same = {"race_id": "a", "progs": ["1", "2", "3"], "p": p, "q": p}
    assert multi_race_ticket([same, dict(same, race_id="b")], "DAILY_DOUBLE", "AQU") is None
    q = np.array([0.2, 0.3, 0.5])
    legs = [{"race_id": "a", "progs": ["1", "2", "3"], "p": p, "q": q},
            {"race_id": "b", "progs": ["1", "2", "3"], "p": p, "q": q}]
    t = multi_race_ticket(legs, "DAILY_DOUBLE", "AQU")
    assert t and t["legs"] == ["a", "b"] and t["ev"] > 0 and "/" in t["selection"]


def test_exotics_need_live_tote_odds():
    p = np.array([0.45, 0.25, 0.15, 0.1, 0.05])
    q = np.array([0.15, 0.35, 0.2, 0.15, 0.15])   # market badly underrates #1 and #2-ordering
    generous = 1 / q - 1
    ml_only = race_wagers(p, list("12345"), generous, ["morning_line"] * 5, None, "AQU", "r", None)
    assert not any(w["wager_type"] in ("EXACTA", "TRIFECTA", "SUPERFECTA") for w in ml_only)
    live = race_wagers(p, list("12345"), generous, ["live"] * 5, None, "AQU", "r", None)
    assert any(w["wager_type"] == "EXACTA" for w in live)


def test_win_bets_need_fresh_tote_odds_and_morning_line_value_is_a_watch():
    from reinman.util import min_tote_odds
    from reinman.wagers import win_watch
    p = np.array([0.40, 0.30, 0.20, 0.10])
    ml = np.array([3.0, 2.0, 4.0, 9.0])                 # #1 at 3-1 with a 40% chance: big edge vs ML
    progs = ["1", "2", "3", "4"]
    assert not race_wagers(p, progs, ml, ["morning_line"] * 4, None, "AQU", "r", None)
    watch = win_watch(p, progs, ml, ["morning_line"] * 4)
    assert watch[0]["selection"] == "1"
    # 10% edge at 40%: break point 1.75-1, shown as the next tote price up (9-5).
    assert watch[0]["min_odds"] == min_tote_odds(1.1 / 0.40 - 1) == 1.8
    assert "9-5 or better" in watch[0]["rationale"]
    live = race_wagers(p, progs, ml, ["live"] * 4, None, "AQU", "r", None)
    assert live[0]["wager_type"] == "WIN" and live[0]["selection"] == "1"
    assert not win_watch(p, progs, ml, ["live"] * 4)
    assert [min_tote_odds(x) for x in (0.95, 2.1, 4.01, 9.2, 12.3)] == [1.0, 2.5, 4.5, 10.0, 13.0]


def test_take_points_to_the_tote_when_only_the_morning_line_is_known():
    from reinman.selections import select
    p = np.array([0.40, 0.30, 0.20, 0.10])
    ml = np.array([3.0, 2.0, 4.0, 9.0])
    watch = [{"selection": "1", "horse_name": "First", "min_odds": 1.8}]
    s = select("r", list("1234"), ["First", "Second", "Third", "Fourth"], p, {}, ml, ["morning_line"] * 4,
               [[], [], [], []], [], 0.5, "market_baseline", watch=watch)
    assert s["decision"] == "pass"
    assert "Watch the tote: #1 First at 9-5 or better" in s["take"]
