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


def test_medication_codes_and_profile_missing_history():
    from reinman import medication
    assert medication.normalize("L^{1}") == "L1"
    assert medication.normalize("") is None
    assert medication.first_lasix("L", None) is None
    assert medication.first_lasix("L1", None) == 1.0
    assert medication.first_lasix("L", "O") == 0.0
    profile, _ = build([], {"race_date": "2024-05-01", "track_id": "KEE"}, {})
    profile["current_context"] = {"medication": "L^{1}", "reported_figures": [18, 33, 36],
                                  "identity": {"age_sex": "4/G", "owner": "Owner"},
                                  "medication_report": {"source": "https://example.com/card"}}
    result = evidence(profile)
    assert any("4-year-old gelding" in x for x in result)
    assert any("18 → 33 → 36" in x for x in result)
    assert any("First-time Lasix" in x for x in result)
    assert any("Insufficient trip evidence" in x for x in result)


def test_profiles_use_official_tracked_finishes():
    h = HistoryBuilder()
    horse = {"horse_id": "h", "horse_name": "Horse", "program_number": "1", "scratched": False}
    rival = {"horse_id": "r", "horse_name": "Rival", "program_number": "2", "scratched": False}
    previous = {"race_date": "2024-04-01", "track_id": "KEE", "surface": "D", "distance_yards": 1320}
    h.add_race(previous, [horse, rival], {"1": 1, "2": 2})
    _, ev = featurize_race(h, dict(previous, race_date="2024-05-01"), [horse, rival])
    assert ev[0]["horse_profile"]["record"] == {"starts": 1, "wins": 1, "itm": 1}
    assert ev[0]["horse_profile"]["comments"] == 0


def test_blank_medication_does_not_inherit_old_code(db):
    from reinman import ingest
    meet = {"meet_id": "KEE-2099-01-01", "race_date": "2099-01-01", "track_id": "KEE"}
    runner = {"horse_id": "h", "horse_name": "Horse", "program_number": "1", "medication": "L"}
    race = {"race_number": 1, "runners": [runner]}
    ingest.store_card(db, {"meet": meet, "races": [race]}, "test")
    current = dict(runner, medication=None, extra={"medication_report": {"code": None, "source": "PuntersEdge"}})
    ingest.store_card(db, {"meet": meet, "races": [dict(race, runners=[current])]}, "test")
    assert db.scalar("SELECT medication FROM runners") is None
