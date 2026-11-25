"""Rein Man's own speed and pace figures (figures.py)."""
import math
from pathlib import Path

from reinman import charts, figures

FIXTURE = Path(__file__).parent / "fixtures" / "equibase_chart_prx_20261123.txt"


def _lines():
    by = {}
    for race in charts.parse_chart(FIXTURE.read_text()):
        for ln in race["lines"]:
            by.setdefault(ln["horse_name"], []).append(ln["data"])
    figures.annotate(by)
    return [ln for group in by.values() for ln in group]


def test_scale_anchor():
    line = {"final_time": 71.0, "distance_yards": 1320, "beaten_lengths": 0, "surface": "D"}
    assert math.isclose(figures.raw_speed(line), 80.0, abs_tol=1e-6)
    beaten = dict(line, beaten_lengths=1)
    assert 1.5 < figures.raw_speed(line) - figures.raw_speed(beaten) < 2.5  # ~2 points a length at 6f


def test_figures_follow_the_finish_and_skip_missing_inputs():
    lines = _lines()
    for race in {ln["race_number"] for ln in lines}:
        figs = [ln["rm_speed"] for ln in sorted((x for x in lines if x["race_number"] == race), key=lambda x: x["finish"])
                if "rm_speed" in ln]
        assert figs == sorted(figs, reverse=True)
    assert figures.raw_speed({"distance_yards": 1320, "beaten_lengths": 0}) is None


def test_variant_needs_a_full_card():
    lines = _lines()
    assert len({ln["race_number"] for ln in lines}) == 4
    assert set(figures.variants(lines)) == {("PRX", "2026-11-23", "D")}
    assert figures.variants([ln for ln in lines if ln["race_number"] in (1, 5)]) == {}


def test_pace_figures_and_features():
    lines = _lines()
    assert any("rm_early" in ln and "rm_late" in ln for ln in lines)
    newest_first = sorted([ln for ln in lines if ln.get("rm_speed") is not None], key=lambda x: -x["race_number"])[:3]
    f, ev = figures.features(newest_first)
    assert f["rm_speed_last"] == newest_first[0]["rm_speed"]
    assert f["rm_speed_best3"] == max(x["rm_speed"] for x in newest_first)
    assert ev["rm_figures"]
    empty, _ = figures.features([{"speed_fig": 70}])
    assert all(math.isnan(v) for v in empty.values())


def test_figures_reach_a_later_race_and_only_from_before(db):
    import json
    from reinman import features
    from reinman.util import norm_name
    charts.ensure_table(db)
    for race in charts.parse_chart(FIXTURE.read_text()):
        data = [dict(ln["data"]) for ln in race["lines"]]
        for ln, d in zip(race["lines"], data):
            d.pop("fractions", None)  # stored before fractions were kept: filled from results
        db.insert_many("chart_lines", [{"horse_key": norm_name(ln["horse_name"]), "race_date": "2026-11-23",
                                        "track_id": "PRX", "race_number": race["race_number"],
                                        "horse_name": ln["horse_name"], "data": json.dumps(d)}
                                       for ln, d in zip(race["lines"], data)])
        db.insert("results", {"race_id": f"PRX-20261123-{race['race_number']}", "source": "chart",
                              "winning_time": race["result"]["winning_time"], "fractions": race["result"]["fractions"]})
    runner = {"program_number": "1", "post_position": 1, "horse_id": "x1", "horse_name": "Donna Light",
              "scratched": 0, "ml_odds": 2.0}
    other = dict(runner, program_number="2", post_position=2, horse_id="x2", horse_name="Nobody Known")
    later = {"race_id": "PRX-20261130-1", "race_date": "2026-11-30", "track_id": "PRX", "surface": "D",
             "distance_yards": 1320}
    X, ev = features.featurize_race(features.build_history(db, "2026-11-30"), later, [runner, other])
    col = features.FEATURES.index("rm_speed_last")
    assert X[0, col] > 0 and math.isnan(X[1, col])
    assert not math.isnan(X[0, features.FEATURES.index("rm_early_avg3")])
    same_day = dict(later, race_id="PRX-20261123-9", race_date="2026-11-23")
    X, _ = features.featurize_race(features.build_history(db, "2026-11-23"), same_day, [runner, other])
    assert math.isnan(X[0, col])  # never a figure from the race day itself or later
