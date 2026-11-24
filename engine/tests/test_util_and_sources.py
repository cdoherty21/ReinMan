import pytest

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

