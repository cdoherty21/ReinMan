from reinman.horse_profiles import build, evidence
from reinman.features import FEATURES, HistoryBuilder, featurize_race
import math


def line(day, comment, finish=2, start=2, **kwargs):
    return {"date": day, "track": "KEE", "race_number": 2, "field_size": 8,
            "call_positions": [start, finish], "finish": finish, "comment": comment,
            "layout": "chart", "document_id": 12, "distance_yards": 1320,
            "surface": "D", "jockey": "Smith John", "condition": "fast", **kwargs}


def test_profile_uses_observed_trips_and_excludes_future():
    race = {"race_date": "2024-05-01", "track_id": "KEE", "surface": "D",
            "distance_yards": 1320, "track_condition": "Fast"}
    runner = {"horse_name": "Real Horse", "horse_id": "r", "scratched": False, "jockey_name": "Smith John"}
    lines = [line("2024-04-01", "Broke slowly, rallied"),
             line("2024-04-09", "Dueled, weakened", finish=5),
             line("2024-04-19", "Checked, rallied", finish=1),
             line("2024-05-01", "Refused to load"),
             line("2024-05-02", "Broke slowly")]
    profile, features = build(lines + [lines[0]], race, runner)
    assert profile["starts"] == 3 and profile["comments"] == 3
    assert profile["events"]["gate difficulty"] == 1
    assert profile["events"]["traffic trouble"] == 1
    assert profile["comparisons"]["today's jockey"]["starts"] == 3
    assert profile["comparisons"]["pace pressure"]["itm"] == 0
    assert "profile_pressure_itm" not in features
    assert features["profile_gate_event_rate"] == 1/3
    assert any("document #12" in x for x in evidence(profile))
    X, ev = featurize_race(HistoryBuilder(chart={"REAL HORSE": lines}), race, [runner])
    assert X[0, FEATURES.index("profile_gate_event_rate")] == 1/3
    assert ev[0]["horse_profile"]["starts"] == 3


def test_missing_comments_and_negation_are_not_trouble():
    race = {"race_date": "2024-05-01", "track_id": "KEE"}
    p, f = build([line("2024-04-01", None), line("2024-04-02", "No trouble, never blocked")], race, {})
    assert p["comments"] == 1
    assert p["events"] == {}
    assert not f
    assert evidence(build([], race, {})[0]) == ["No verified running lines available before this race."]
