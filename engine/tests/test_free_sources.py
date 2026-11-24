from datetime import datetime, timezone

import pytest

from reinman.cli import automatic_sources, do_import_otb
from reinman.db import SQLiteDatabase
from reinman.sources.puntersedge import board_race, final_result, meet_for


def test_tvg_board_and_results_use_same_race_identity():
    common = {"country": "US", "category": "horse", "race_number": 8}
    board = dict(common, venue="Parx Racing", track_code="PHI", post_time="2026-11-23T22:05:00Z",
                 tote={"status": "open", "read_at": "2026-11-23T22:00:00Z",
                       "runners": [{"number": 4, "name": "First Horse", "win_odds": "5/2"}]})
    row = dict(common, venue="Parx Racing", status="final", placings=[
        {"number": 4, "position": 1, "win_price": 3.75, "name": "First Horse"}],
        dividends={"straight": [{"product": "US-TOTE"}],
                   "exotics": [{"product": "US-TOTE", "type": "Exacta",
                                "base": 1, "payout": 43.50, "selection": "4,2"}]})
    day = "2026-11-23"
    now = datetime(2026, 11, 23, 22, 1, tzinfo=timezone.utc)
    assert meet_for(board, day) == meet_for(row, day)
    assert board_race(board, day, now)["runners"][0]["live_odds"] == 2.5
    assert board_race(board, day, now.replace(minute=6))["runners"][0]["live_odds"] is None
    result = final_result(row)
    assert result["finishers"][0]["win_payoff"] == 7.50
    assert result["payoffs"][0]["winning_numbers"] == "4-2"
    assert result["payoffs"][0]["base_amount"] == 1
    assert final_result(dict(row, status="interim")) is None


def test_saved_otb_result_can_be_imported_twice(tmp_path):
    page = tmp_path / "otb.html"
    page.write_text("""
    <table><tr><th>Post</th><th>Horse</th><th>WIN</th><th>PLACE</th><th>SHOW</th></tr>
    <tr><td>4</td><td>First Horse</td><td>$7.50</td><td>$3.40</td><td>$2.80</td></tr>
    <tr><td>2</td><td>Second Horse</td><td></td><td>$4.60</td><td>$3.00</td></tr></table>
    <p>$1 Exacta (4-2) Paid: $43.50</p>
    <p>50 CENT Pick 3 (6-1-4) 3 Correct Paid: $126.25</p>
    """)
    db = SQLiteDatabase()
    db.migrate()
    args = (db, str(page), "AQU", "2026-11-23", 8, False)
    assert do_import_otb(*args)["official"] == 1
    assert do_import_otb(*args)["already_official_from"] == "otb"
    assert db.scalar("SELECT win_payoff FROM result_finishers WHERE finish_position=1") == 7.50
    assert db.scalar("SELECT base_amount FROM result_payoffs WHERE wager_type='PICK3'") == 0.5
    page.write_text("<p>Access denied</p>")
    with pytest.raises(ValueError, match="finish table"):
        do_import_otb(*args)


def test_free_jobs_share_provider_budget(monkeypatch):
    monkeypatch.setenv("PUNTERSEDGE_API_KEY", "test-key")
    db = SQLiteDatabase()
    db.migrate()
    for _ in range(19):
        assert automatic_sources(db)[0].reserve("tote")
    assert not automatic_sources(db)[0].reserve("tote")
    assert automatic_sources(db)[0].reserve("results")
