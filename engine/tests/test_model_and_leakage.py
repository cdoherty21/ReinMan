import numpy as np
import pytest

import sim
from reinman import backtest, train
from reinman.features import FEATURES, HistoryBuilder, build_dataset, featurize_race, load_races
from reinman.models import Ensemble, MarketBaseline, load_model
from reinman.config import Policy


@pytest.fixture(scope="module")
def world_db():
    from reinman.db import SQLiteDatabase
    from reinman.registry import ensure_baseline
    db = SQLiteDatabase(":memory:")
    db.migrate()
    ensure_baseline(db)
    w = sim.World(seed=3)
    sim.history(w, db, "2025-01-01", days=130, races_per_track=8)
    return db


def test_features_are_point_in_time(world_db):
    races, runners, fin, _ = load_races(world_db, official_only=True)
    rid = next(r for r in races if races[r]["race_date"] == "2025-03-01")
    race = races[rid]
    h = HistoryBuilder()
    # history built only from earlier dates
    for k, r in races.items():
        if r["race_date"] < race["race_date"] and fin.get(k):
            h.add_race(r, runners[k], fin[k])
    X1, _ = featurize_race(h, race, runners[rid])
    # adding this race's own result (and same-day results) must not change its features
    for k, r in races.items():
        if r["race_date"] == race["race_date"] and fin.get(k):
            h.add_race(r, runners[k], fin[k])
    X2, _ = featurize_race(h, race, runners[rid])
    assert np.array_equal(np.nan_to_num(X1, nan=-9), np.nan_to_num(X2, nan=-9))
    assert X1.shape[1] == len(FEATURES)


def test_trained_model_beats_baseline_out_of_sample(world_db):
    samples = build_dataset(world_db)
    assert len(samples) > 2500
    tr, va, te = train.split(samples)
    assert tr[-1].race_date < va[0].race_date <= va[-1].race_date < te[0].race_date  # strictly chronological
    model, artifact = train.fit_candidate(tr, va)
    import math
    def ll(f):
        return float(np.mean([-math.log(max(f(s)[s.finish == 1].sum(), 1e-12)) for s in te]))
    fundamental = ll(lambda s: model.predict(s.X, None)[0])        # no tote odds used
    morning_line = ll(lambda s: MarketBaseline().predict(s.X, None)[0])
    closing_market = ll(lambda s: MarketBaseline().predict(s.X, train.market_odds(s))[0])
    blended = ll(lambda s: model.predict(s.X, train.market_odds(s))[0])
    uniform = ll(lambda s: np.ones(len(s.X)) / len(s.X))
    assert fundamental < morning_line < uniform      # learns real signal beyond the morning line
    assert blended < closing_market + 0.02            # blend never strays far from an efficient market
    # artifact round-trip reproduces predictions exactly
    m2 = load_model(artifact)
    s = te[0]
    p1, _ = model.predict(s.X, train.market_odds(s))
    p2, _ = m2.predict(s.X, train.market_odds(s))
    assert np.allclose(p1, p2)
    assert isinstance(m2, Ensemble) and len(artifact["lambdas"]) == 4


def test_promotion_gate_and_audit_trail(world_db, monkeypatch):
    monkeypatch.setattr(train, "POLICY", Policy(min_train_races=500, min_valid_races=100))
    out = train.train_and_maybe_promote(world_db)
    assert out["status"] in ("promoted", "rejected")
    events = world_db.query("SELECT event FROM model_events ORDER BY id")
    assert [e["event"] for e in events][-1] == out["status"]
    if out["status"] == "promoted":
        assert world_db.scalar("SELECT COUNT(*) FROM models WHERE status='champion'") == 1
        assert world_db.scalar("SELECT status FROM models WHERE model_version='v0-market-baseline'") == "retired"
    with pytest.raises(Exception):
        world_db.execute("UPDATE models SET artifact='{}'")
    with pytest.raises(Exception):
        world_db.execute("DELETE FROM model_events")


def test_walk_forward_backtest(world_db):
    m = backtest.run(world_db, min_train=1800)
    assert m["races"] > 0 and m["folds"]
    assert m["mean"]["ll_model"] < m["mean"]["ll_ml"]
    assert "top_pick_flat" in m["bets"]


def test_end_to_end_with_trained_champion(world_db, tmp_path):
    import json
    from datetime import datetime, timedelta, timezone
    from reinman import predict, registry
    from reinman.cli import do_import
    samples = build_dataset(world_db)
    tr, va, _ = train.split(samples)
    _, artifact = train.fit_candidate(tr, va)
    registry.register(world_db, "vtest-ensemble", "ensemble", artifact, {}, "test")
    registry.promote(world_db, "vtest-ensemble", {"test": True})
    # tomorrow's card arrives through the canonical file importer
    w = sim.World(seed=3)
    d = "2025-05-15"
    post = datetime.now(timezone.utc) + timedelta(hours=2)
    card, _ = w.meet(d, "AQU", 4, post, finished=False)
    f = tmp_path / "card.json"
    f.write_text(json.dumps({"source": "test_file", "cards": [card], "results": []}))
    assert do_import(world_db, str(f))["cards"] == 1
    out = predict.predict_date(world_db, d)
    assert out["model_version"] == "vtest-ensemble" and out["snapshots_written"] == 4
    preds = world_db.query("SELECT * FROM predictions WHERE race_id LIKE 'AQU-20250515-%'")
    for p in preds:
        assert p["top1"] and p["top2"] and p["top3"] and "Cold-start" not in p["take"]
        notes = json.loads(p["data_notes"])
        assert "speed_figures" in notes["unavailable"]   # honestly reported as unavailable
        pr = world_db.query("SELECT * FROM prediction_runners WHERE prediction_id=?", [p["prediction_id"]])
        assert any(f.startswith("+ ") for r in pr for f in json.loads(r["factors"]))
        assert abs(sum(r["win_prob"] for r in pr) - 1) < 1e-9
        assert all(r["place_prob"] >= r["win_prob"] and r["show_prob"] >= r["place_prob"] for r in pr)
