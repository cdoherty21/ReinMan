import json
from pathlib import Path

from reinman import charts, documents, features, grading, ingest

FIXTURE = (Path(__file__).parent / "fixtures" / "equibase_chart_prx_20261123.txt").read_text()
FILENAME = "20261123-usa-prx-a-d.standard.pdf"
DAY = "2026-11-23"


def _races():
    return {r["race_number"]: r for r in charts.parse_chart(FIXTURE, FILENAME)}


def test_reads_a_real_equibase_chart():
    assert charts.is_chart(FIXTURE)
    races = _races()
    assert sorted(races) == [1, 5, 6, 9]
    assert all(r["settled"] and r["track_id"] == "PRX" and r["race_date"] == DAY for r in races.values())

    r1 = races[1]["result"]
    assert [(f["program_number"], f["horse_name"]) for f in r1["finishers"]] == [
        ("1", "Donna Light"), ("2", "Mi Amol"), ("5", "Inner Secrets"), ("7", "Punch and Judy"),
        ("4", "Call Me Alex"), ("3", "Lovebird")]
    assert [(f["win_payoff"], f["place_payoff"], f["show_payoff"]) for f in r1["finishers"][:3]] == [
        (7.00, 3.00, 2.10), (None, 6.00, 4.40), (None, None, 2.40)]
    assert (r1["winning_time"], r1["fractions"], r1["track_condition"], r1["surface"]) == (
        80.57, [23.18, 47.54, 73.82], "Fast", "D")
    assert r1["scratches"] == ["Likeastraightshot"]
    assert {p["wager_type"]: (p["base_amount"], p["winning_numbers"], p["payoff_amount"]) for p in r1["payoffs"]} == {
        "EXACTA": (2.0, "1-2", 30.20), "TRIFECTA": (0.5, "1-2-5", 26.95), "SUPERFECTA": (0.1, "1-2-5-7", 8.02)}

    # Race 5: a mile (an extra call), no show betting, Pick 4 with a split leg.
    r5 = races[5]
    assert [(f["win_payoff"], f["place_payoff"], f["show_payoff"]) for f in r5["result"]["finishers"][:2]] == [
        (2.80, 2.10, None), (None, 2.60, None)]
    pick4 = next(p for p in r5["result"]["payoffs"] if p["wager_type"] == "PICK4")
    assert pick4["winning_numbers"] == "1-4-2-1/3/5"
    assert grading._combo_matches(["1", "4", "2", "3"], pick4["winning_numbers"])
    assert not grading._combo_matches(["1", "4", "2", "2"], pick4["winning_numbers"])
    assert r5["result"]["scratches"] == ["Celestial Dark", "Union Purrfection"]
    # Race 6: the consolation double is left out; race 9: the Pick 5 with a carryover column.
    assert "Consolation" not in {p["wager_name"] for p in races[6]["result"]["payoffs"]}
    assert next(p for p in races[9]["result"]["payoffs"] if p["wager_type"] == "PICK5")["payoff_amount"] == 895.20


def test_running_lines_carry_calls_and_lengths_behind():
    lines = {ln["horse_name"]: ln["data"] for r in _races().values() for ln in r["lines"]}
    donna = lines["Donna Light"]
    assert (donna["call_positions"], donna["call_lengths"], donna["beaten_lengths"]) == (
        [4, 4, 1, 1], [1.0, 1.0, 0.0, 0.0], 0.0)
    assert (donna["odds"], donna["favorite"], donna["post_position"], donna["field_size"]) == (2.50, False, 1, 6)
    assert (donna["distance_yards"], donna["condition"], donna["jockey"]) == (1430, "fast", "Adorno, Abner")
    assert lines["Lovebird"]["beaten_lengths"] == 26.0
    assert lines["Ike's Gamble"]["call_positions"] == [1, 1, 1, 1, 1] and lines["Ike's Gamble"]["favorite"]
    assert lines["Smallchangegeep"]["beaten_lengths"] == 15.25
    assert lines["On Brand (IRE)"]["trouble"] and lines["Wheelhouse"]["trouble"]
    assert not lines["Righteous Felon"]["trouble"]


def test_two_digit_positions_are_resolved_by_the_field():
    # "112" in a 12-horse field: 1st by 12 for one horse means 11th by 2 for another.
    assert charts._resolve(["112", "2Head"], 2) == [(1, 12.0), (2, 0.1)]
    assert sorted(p for p, _ in charts._resolve(
        ["112", "13", "21", "32", "43", "54", "65", "76", "87", "98", "109", "1210"], 12)) == list(range(1, 13))
    assert charts._resolve(["13", "13"], 2) is None


def test_dead_heats_and_order_disagreements_are_not_stored():
    dh = FIXTURE.replace("Donna Light (Adorno, Abner)", "Donna Light (Adorno, Abner) DH", 1)
    assert not {r["race_number"]: r for r in charts.parse_chart(dh)}[1]["settled"]
    swapped = FIXTURE.replace("      1      Donna Light            5          41           41            1Head      13 1/2",
                              "      1      Donna Light            5          41           41            1Head      23 1/2", 1)
    assert swapped != FIXTURE
    assert not {r["race_number"]: r for r in charts.parse_chart(swapped)}[1]["settled"]


def _cards(db, names=None):
    for number, race in _races().items():
        runners = [{"program_number": f["program_number"], "post_position": i + 1,
                    "horse_id": f"h-{f['horse_name']}", "scratched": False,
                    "horse_name": (names or {}).get((number, f["program_number"]), f["horse_name"])}
                   for i, f in enumerate(race["result"]["finishers"])]
        ingest.store_card(db, {"meet": {"meet_id": f"PRX-{DAY}", "track_id": "PRX", "race_date": DAY},
                               "races": [{"race_number": number, "post_time": f"{DAY}T17:00:00Z",
                                          "is_cancelled": False, "changes": [], "runners": runners}]},
                          "puntersedge")


def test_apply_stores_matching_results_and_every_running_line(db):
    _cards(db, names={(6, "9"): "Someone Else"})
    out = charts.apply(db, FIXTURE, FILENAME, document_id=11)
    assert out["chart_races"] == 4 and out["chart_lines"] == 24
    assert out["races_linked"] == ["PRX-20261123-1", "PRX-20261123-5", "PRX-20261123-9"]
    assert out["skipped"] == ["PRX-20261123-6"]
    assert db.scalar("SELECT source FROM results WHERE race_id='PRX-20261123-1'") == "chart"
    assert db.scalar("SELECT winning_time FROM results WHERE race_id='PRX-20261123-5'") == 101.37
    assert db.scalar("SELECT COUNT(*) FROM result_finishers WHERE race_id='PRX-20261123-9'") == 7
    # Applying the same chart again changes nothing.
    again = charts.apply(db, FIXTURE, FILENAME, document_id=11)
    assert again["results_added"] == 0 and db.scalar("SELECT COUNT(*) FROM chart_lines") == 24


def test_chart_fills_times_on_results_from_other_sources(db):
    _cards(db)
    race = _races()[1]["result"]
    ingest.store_results(db, {"track_id": "PRX", "race_date": DAY},
                         [dict(race, winning_time=None, fractions=[])], "otb")
    out = charts.apply(db, FIXTURE, FILENAME)
    assert "PRX-20261123-1" in out["races_linked"]
    assert db.one("SELECT source, winning_time FROM results WHERE race_id='PRX-20261123-1'") == {
        "source": "otb", "winning_time": 80.57}


def test_upload_is_read_as_a_chart(db):
    _cards(db)
    doc_id = db.insert("documents", {"filename": FILENAME, "uploaded_at": "2026-11-24T19:22:28.941Z",
                                     "size_bytes": len(FIXTURE), "chars": len(FIXTURE), "status": "pending"})
    db.insert("document_chunks", {"document_id": doc_id, "seq": 0, "text": FIXTURE})
    assert documents.process_pending(db)["processed"] == 1
    summary = json.loads(db.scalar("SELECT summary FROM documents WHERE id=?", [doc_id]))
    assert summary["results_added"] == 4 and summary["chart_lines"] == 24


def test_chart_lines_feed_the_horses_next_race(db):
    charts.apply(db, FIXTURE, FILENAME)
    h = features.build_history(db, "2026-12-05")
    race = {"race_id": "PRX-20261205-3", "race_date": "2026-12-05", "track_id": "PRX", "surface": "D",
            "distance_yards": 1320, "track_condition": "Fast"}
    runners = [{"program_number": str(i + 1), "post_position": i + 1, "horse_id": f"x{i}", "horse_name": name,
                "scratched": False, "ml_odds": 3.0, "extra": None}
               for i, name in enumerate(["Donna Light", "Mi Amol", "Inner Secrets", "Unraced Horse"])]
    X, evidence = features.featurize_race(h, race, runners)
    col = {name: i for i, name in enumerate(features.FEATURES)}
    assert evidence[0]["pp_lines"] == 1 and "pp_lines" not in evidence[3]
    assert X[0, col["pp_early_pos"]] == 4 / 6
    assert X[1, col["pp_beaten_last"]] == 3.5
    # Same-day chart lines never leak into that day's own races.
    h_same = features.build_history(db, DAY)
    X_same, ev_same = features.featurize_race(h_same, dict(race, race_date=DAY), runners)
    assert "pp_lines" not in ev_same[0]
