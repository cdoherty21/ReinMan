import pytest

from reinman.sources.racingapi import parse_entries, parse_results
from reinman.util import fmt_odds, parse_distance_yards, parse_odds, parse_post_time, surface_code
from reinman.wagertypes import normalize_wager_type


@pytest.mark.parametrize("txt,val", [("5/2", 2.5), ("8-1", 8.0), ("9-5", 1.8), ("EVEN", 1.0), ("3", 3.0),
                                     ("", None), ("SCR", None), (None, None), ("1/9", 1 / 9)])
def test_parse_odds(txt, val):
    assert parse_odds(txt) == (pytest.approx(val) if val is not None else None)


@pytest.mark.parametrize("desc,yards", [("6 Furlongs", 1320), ("1 1/16 Miles", 1870), ("1 Mile 70 Yards", 1830),
                                        ("About 7 1/2 Furlongs", 1650), ("5 1/2 Furlongs", 1210), ("1 Mile", 1760)])
def test_distance(desc, yards):
    assert parse_distance_yards(desc) == yards


def test_distance_value_unit():
    assert parse_distance_yards(None, 600, "F") == 1320
    assert parse_distance_yards(None, 6, "F") == 1320


def test_post_time_formats():
    assert parse_post_time("2026-07-04", "1:15 PM", tz="E") == "2026-07-04T17:15:00.000Z"
    assert parse_post_time("2026-07-04", "1:15", tz="P") == "2026-07-04T20:15:00.000Z"
    assert parse_post_time("2026-07-04", None, 1783185300000) == "2026-07-04T17:15:00.000Z"
    assert parse_post_time("2026-07-04", "2026-07-04T13:15:00-04:00") == "2026-07-04T17:15:00.000Z"


def test_surface_and_wager_names():
    assert surface_code("T") == "T" and surface_code(None, "Inner turf") == "T" and surface_code("Dirt") == "D"
    assert surface_code("Tapeta") == "S"
    assert normalize_wager_type("EX", "$1 Exacta") == "EXACTA"
    assert normalize_wager_type(None, "$0.50 Pick 4") == "PICK4"
    assert normalize_wager_type("DBL", "$1 Daily Double") == "DAILY_DOUBLE"
    assert normalize_wager_type(None, "$0.10 Superfecta") == "SUPERFECTA"
    assert normalize_wager_type(None, "$0.50 Trifecta") == "TRIFECTA"
    assert fmt_odds(2.5) == "5-2" and fmt_odds(8.0) == "8-1" and fmt_odds(9.2) == "9-1" and fmt_odds(43.2) == "43-1"


ENTRIES = {  # shaped after The Racing API NA "Entries" schema (fixture)
    "meet_id": "M1", "track_id": "AQU", "track_name": "Aqueduct", "country": "USA", "date": "2026-11-27",
    "weather": {"forecast_weather_description": "Cloudy", "forecast_high": 48, "forecast_precipitation": 20},
    "races": [{
        "race_key": {"race_number": "3", "day_evening": "D"}, "post_time": "1:45 PM", "time_zone": "E",
        "distance_description": "1 1/16 Miles", "course_type": "D", "surface_description": "Dirt",
        "race_type": "CLM", "race_type_description": "Claiming", "purse": "32000", "max_claim_price": "16000",
        "track_condition": "Fast", "race_pools": [{"pool_code": "EX", "pool_name": "Exacta", "race_list": "3"},
                                                   {"pool_code": "P4", "pool_name": "Pick 4", "race_list": "3-4-5-6"}],
        "changes": [{"type": "Scratch", "text": "#4 scratched - vet"}],
        "runners": [
            {"program_number": "1", "post_pos": "1", "horse_name": "Alpha", "registration_number": "R1",
             "jockey": {"id": "J1", "first_name": "A", "last_name": "Rider"},
             "trainer": {"id": "T1", "first_name": "B", "last_name": "Trainer"},
             "morning_line_odds": "5/2", "live_odds": "2-1", "weight": "120", "medication": "L",
             "horse_data_pools": [{"pool_type_name": "WIN", "amount": "1500"}], "scratch_indicator": "N"},
            {"program_number": "4", "post_pos": "4", "horse_name": "Delta (IRE)", "morning_line_odds": "6-1",
             "scratch_indicator": "Y"},
        ]}],
}


def test_parse_entries():
    card = parse_entries(ENTRIES, {"meet_id": "M1", "track_id": "AQU", "race_date": "2026-11-27"})
    r = card["races"][0]
    assert r["race_number"] == 3 and r["distance_yards"] == 1870 and r["surface"] == "D"
    assert r["post_time"] == "2026-11-27T18:45:00.000Z"
    assert r["max_claim"] == 16000 and r["temp_f"] == 48
    assert {p["wager_type"] for p in r["pools"]} == {"EXACTA", "PICK4"}
    a, d = r["runners"]
    assert a["ml_odds"] == 2.5 and a["live_odds"] == 2.0 and a["win_pool"] == 1500
    assert a["jockey_id"] == "j:J1" and a["horse_id"] == "reg:R1"
    assert d["scratched"] and d["horse_id"] == "name:DELTA"


def test_parse_results_with_dead_heat_for_win():
    data = {"races": [{"race_key": {"race_number": "1"}, "runners": [
        {"program_number": "2", "horse_name": "A", "win_payoff": 5.2, "place_payoff": 3.0, "show_payoff": 2.4},
        {"program_number": "5", "horse_name": "B", "win_payoff": 8.0, "place_payoff": 4.0, "show_payoff": 3.0},
        {"program_number": "1", "horse_name": "C", "show_payoff": 2.8}],
        "also_ran": "D, E",
        "payoffs": [{"wager_type": "EX", "wager_name": "$2 Exacta", "base_amount": 2, "winning_numbers": "2-5",
                     "payoff_amount": "30.40"}]}]}
    res = parse_results(data)[0]
    pos = {f["horse_name"]: f["finish_position"] for f in res["finishers"]}
    assert pos["A"] == 1 and pos["B"] == 1 and pos["C"] == 3
    assert all(f["dead_heat"] for f in res["finishers"][:2])
    assert pos["D"] == 4 and pos["E"] == 5
    assert res["payoffs"][0]["wager_type"] == "EXACTA" and res["payoffs"][0]["payoff_amount"] == 30.4


def test_parse_results_normal_order():
    data = {"races": [{"race_key": {"race_number": "2"}, "runners": [
        {"program_number": "7", "horse_name": "W", "win_payoff": 10, "place_payoff": 5, "show_payoff": 3},
        {"program_number": "3", "horse_name": "P", "place_payoff": 4, "show_payoff": 3},
        {"program_number": "1", "horse_name": "S", "show_payoff": 2.5},
        {"program_number": "4", "horse_name": "F"}]}]}
    res = parse_results(data)[0]
    assert [f["finish_position"] for f in res["finishers"]] == [1, 2, 3, 4]
    assert not any(f["dead_heat"] for f in res["finishers"])
