"""D1 write budget: unchanged data costs no writes, meaningful changes are kept, and the
quota levels gate what may write. See docs/D1_BUDGET.md."""
import copy
from datetime import datetime, timedelta, timezone

import pytest

from reinman import cli, ingest, predict, quota
from reinman.util import to_iso


def _future(minutes):
    return to_iso(datetime.now(timezone.utc) + timedelta(minutes=minutes))


def _card(post, odds=(2.0, 3.0, 5.0, 8.0), track="AQU", d="2026-11-24", pools=False):
    runners = []
    for i, o in enumerate(odds):
        rn = {"program_number": str(i + 1), "post_position": i + 1, "horse_id": f"h{i}",
              "horse_name": f"Horse {i + 1}", "jockey_name": f"Rider, J{i}", "trainer_name": f"Coach, T{i}",
              "ml_odds": [1.5, 3.0, 5.0, 8.0][i], "live_odds": o, "scratched": False}
        if pools:
            rn.update(win_pool=1000.0 * (i + 1), place_pool=500.0, show_pool=300.0)
        runners.append(rn)
    return {"meet": {"meet_id": f"{track}-{d}", "track_id": track, "track_name": track, "race_date": d},
            "races": [{"race_number": 1, "post_time": post, "surface": "D", "distance_yards": 1320,
                       "track_condition": "Fast", "purse": 20000, "is_cancelled": False, "changes": [],
                       "runners": runners}]}


def _writes(db, fn):
    before = db.rows_written
    fn()
    return db.rows_written - before


def test_identical_card_writes_nothing(db):
    card = _card(_future(120))
    assert _writes(db, lambda: ingest.store_card(db, card, "puntersedge")) > 0
    assert _writes(db, lambda: ingest.store_card(db, copy.deepcopy(card), "puntersedge")) == 0
    assert db.scalar("SELECT COUNT(*) FROM odds_snapshots") == 4


def test_blank_source_fields_do_not_erase_stored_ones(db):
    card = _card(_future(120))
    ingest.store_card(db, card, "puntersedge")
    db.execute("UPDATE races SET temp_f=51, precip_pct=10")
    assert _writes(db, lambda: ingest.store_card(db, copy.deepcopy(card), "puntersedge")) == 0
    assert db.scalar("SELECT temp_f FROM races") == 51


def test_small_far_move_is_used_but_not_stored(db):
    post = _future(180)
    ingest.store_card(db, _card(post), "puntersedge")
    fresh = {}
    written = _writes(db, lambda: ingest.store_card(db, _card(post, odds=(2.1, 3.0, 5.0, 8.0)),
                                                   "puntersedge", fresh))
    assert written == 0
    assert db.scalar("SELECT live_odds FROM runners WHERE program_number='1'") == 2.0
    assert fresh["AQU-20261124-1"]["odds"]["1"] == 2.1  # this run's predictions see the real read


def test_big_far_move_is_stored(db):
    post = _future(180)
    ingest.store_card(db, _card(post), "puntersedge")
    ingest.store_card(db, _card(post, odds=(4.0, 3.0, 5.0, 8.0)), "puntersedge")
    assert db.scalar("SELECT live_odds FROM runners WHERE program_number='1'") == 4.0
    assert db.scalar("SELECT COUNT(*) FROM odds_snapshots WHERE program_number='1'") == 2


def test_near_post_any_change_snapshots_whole_race(db):
    post = _future(10)
    ingest.store_card(db, _card(post), "puntersedge")
    ingest.store_card(db, _card(post, odds=(2.1, 3.0, 5.0, 8.0)), "puntersedge")
    assert db.scalar("SELECT live_odds FROM runners WHERE program_number='1'") == 2.1
    assert db.scalar("SELECT COUNT(*) FROM odds_snapshots") == 8  # a consistent line at each step
    ingest.store_card(db, _card(post, odds=(2.1, 3.0, 5.0, 8.0)), "puntersedge")
    assert db.scalar("SELECT COUNT(*) FROM odds_snapshots") == 8  # no change, no snapshot


def test_unchanged_prediction_is_not_stored_again(db):
    post = _future(120)
    fresh = {}
    ingest.store_card(db, _card(post), "puntersedge", fresh)
    assert predict.predict_date(db, "2026-11-24", fresh=fresh)["snapshots_written"] == 1
    fresh = {}
    ingest.store_card(db, _card(post, odds=(2.0, 3.0, 5.0, 8.05)), "puntersedge", fresh)
    out = predict.predict_date(db, "2026-11-24", fresh=fresh)
    assert out["races_analyzed"] == 1 and out["snapshots_written"] == 0
    fresh = {}
    ingest.store_card(db, _card(post, odds=(9.0, 3.0, 1.2, 8.0)), "puntersedge", fresh)
    assert predict.predict_date(db, "2026-11-24", fresh=fresh)["snapshots_written"] == 1


def test_fresh_read_is_required_for_live_odds(db):
    ingest.store_card(db, _card(_future(120)), "puntersedge")
    out = predict.predict_date(db, "2026-11-24")  # no read in this run: morning line only
    assert out["snapshots_written"] == 1
    notes = db.scalar("SELECT json_extract(data_notes,'$.odds_basis') FROM predictions")
    assert notes == "morning_line"


def test_material_change_rules():
    a = {"race": {"post_time": _future(60)}, "inputs_hash": "x", "wagers": [],
         "selection": {"top1": "1", "top2": "2", "top3": "3", "most_likely": "1", "decision": "pass", "confidence": "Low"},
         "data_notes": {"odds_basis": "live", "watch": []},
         "runners": [{"program_number": "1", "win_prob": 0.40}, {"program_number": "2", "win_prob": 0.60}]}
    last = {"row": {"inputs_hash": "x", "top1": "1", "top2": "2", "top3": "3", "most_likely": "1", "decision": "pass",
                    "confidence": "Low", "data_notes": '{"odds_basis": "live", "watch": []}',
                    "created_at": to_iso(datetime.now(timezone.utc))},
            "probs": {"1": 0.405, "2": 0.595}, "wagers": set()}
    assert predict.material_change(a, None) == "first"
    assert predict.material_change(a, last) is None
    assert predict.material_change(a, {**last, "probs": {"1": 0.42, "2": 0.58}}) == "probabilities"
    assert predict.material_change(a, {**last, "row": {**last["row"], "inputs_hash": "y"}}) == "inputs"
    assert predict.material_change(a, {**last, "row": {**last["row"], "top1": "2"}}) == "selection"
    near = {**a, "race": {"post_time": _future(8)}}
    old = {**last, "row": {**last["row"], "created_at": to_iso(datetime.now(timezone.utc) - timedelta(minutes=12))}}
    assert predict.material_change(near, old) == "pre_post_refresh"


@pytest.mark.parametrize("used,level", [(0, "normal"), (49_999, "normal"), (50_000, "watch"), (65_000, "defer"),
                                        (75_000, "essential"), (85_000, "pause_bulk"), (90_000, "emergency")])
def test_quota_levels(db, monkeypatch, used, level):
    monkeypatch.setenv("REINMAN_QUOTA_USED", str(used))
    assert quota.status(db)["level"] == level


def test_quota_gates(db, monkeypatch):
    monkeypatch.setenv("REINMAN_QUOTA_USED", "66000")
    assert quota.may(db, "standard") and not quota.may(db, "background") and quota.may(db, "bulk")
    monkeypatch.setenv("REINMAN_QUOTA_USED", "86000")
    assert not quota.may(db, "bulk") and quota.guard_bulk(db, "backfill")["deferred"] == "backfill"
    monkeypatch.setenv("REINMAN_QUOTA_USED", "91000")
    assert not quota.may(db, "standard") and quota.may(db, "essential") and quota.may(db, "critical")
    assert quota.predict_scope(db)["within_min"] == 30
    assert quota.odds_far_move(db) > 100  # far-from-post odds are not stored in an emergency


def test_emergency_keeps_near_post_odds(db, monkeypatch):
    monkeypatch.setenv("REINMAN_QUOTA_USED", "95000")
    post = _future(10)
    ingest.store_card(db, _card(post), "puntersedge")
    ingest.store_card(db, _card(post, odds=(2.5, 3.0, 5.0, 8.0)), "puntersedge",
                      far_move=quota.odds_far_move(db))
    assert db.scalar("SELECT live_odds FROM runners WHERE program_number='1'") == 2.5


def test_job_is_one_row_and_counts_its_writes(db):
    cli.job(db, "probe", lambda: db.execute("INSERT INTO stats_cache (key, json) VALUES ('k', '{}')") or {"ok": 1})
    rows = db.query("SELECT job, status, rows_written FROM job_runs WHERE job='probe'")
    assert rows == [{"job": "probe", "status": "ok", "rows_written": 1}]
    cli.job(db, "quiet", lambda: {"nothing": True}, quiet=True)
    assert db.scalar("SELECT COUNT(*) FROM job_runs WHERE job='quiet'") == 0


def test_stats_refresh_skips_unchanged(db):
    from reinman import performance
    performance.refresh_stats(db)
    assert _writes(db, lambda: performance.refresh_stats(db)) <= 1


def test_bulk_import_stops_at_budget_and_resumes(db, monkeypatch, tmp_path):
    import json as _json
    cards = [_card(_future(300 + i), track=f"T{i}") for i in range(6)]
    path = tmp_path / "cards.json"
    path.write_text(_json.dumps({"source": "archive", "cards": cards}))
    monkeypatch.setattr(quota, "SMALL_JOB", 10)
    monkeypatch.setenv("REINMAN_QUOTA_USED", "70000")  # defer level: bulk work yields to racing
    first = cli.do_import(db, str(path))
    assert first.get("deferred") and first["cards"] < 6
    monkeypatch.setenv("REINMAN_QUOTA_USED", "0")  # after the 00:00 UTC reset
    second = cli.do_import(db, str(path))
    assert "deferred" not in second
    assert db.scalar("SELECT COUNT(DISTINCT track_id) FROM races") == 6
    assert db.scalar("SELECT COUNT(*) FROM runners") == 24  # nothing duplicated


def test_performance_page_keeps_refreshing_under_budget_pressure(db, monkeypatch):
    # The stats cache is what the Performance page shows; it costs a handful of rows and
    # must keep up with grading at every level short of an emergency.
    from reinman import performance
    monkeypatch.setenv("REINMAN_QUOTA_USED", "86000")  # pause_bulk
    monkeypatch.setattr(cli, "today", lambda: "2026-11-24")
    monkeypatch.setattr(cli, "automatic_sources", lambda db: [])
    db.execute("INSERT INTO stats_cache (key, json, computed_at) VALUES ('overall', '{}', '2026-01-01T00:00:00Z')")
    db.execute("INSERT INTO grades (race_id, prediction_id, status, graded_at) VALUES ('X-1', 1, 'graded', '2026-11-24T20:00:00Z')")
    out = cli.monitor(db)
    assert "deferred" not in out.get("stats", {}), out.get("stats")
    assert db.scalar("SELECT computed_at FROM stats_cache WHERE key='overall'") > "2026-11-24"


def test_watch_and_defer_reduce_nonessential_history(db, monkeypatch):
    monkeypatch.setenv("REINMAN_QUOTA_USED", "50000")
    assert quota.predict_scope(db)["within_min"] == 180
    assert quota.odds_far_move(db) == 0.15
    monkeypatch.setenv("REINMAN_QUOTA_USED", "65000")
    assert quota.predict_scope(db)["within_min"] == 120
    assert quota.odds_far_move(db) == 0.20
    monkeypatch.setenv("REINMAN_QUOTA_USED", "86000")
    quota.start_run(db)
    assert not quota.keep_going(db)
    assert quota.may(db, "critical")


def test_watch_preserves_first_snapshots_and_defers_far_revisions(db, monkeypatch):
    monkeypatch.setenv("REINMAN_QUOTA_USED", "50000")
    fresh = {}
    post = _future(240)
    ingest.store_card(db, _card(post), "puntersedge", fresh)
    scope = quota.predict_scope(db)
    assert predict.predict_date(db, "2026-11-24", fresh=fresh, scope=scope)["snapshots_written"] == 1
    fresh = {}
    ingest.store_card(db, _card(post, odds=(9.0, 3.0, 1.2, 8.0)), "puntersedge", fresh)
    out = predict.predict_date(db, "2026-11-24", fresh=fresh, scope=scope)
    assert out["held_for_quota"] == 1 and out["snapshots_written"] == 0
    monkeypatch.setenv("REINMAN_QUOTA_USED", "0")
    assert predict.predict_date(db, "2026-11-24", fresh=fresh, scope=quota.predict_scope(db))["snapshots_written"] == 1
