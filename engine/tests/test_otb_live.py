import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reinman import grading, ingest
from reinman.cli import do_results
from reinman.sources.otb_live import OTBBlocked, OTBResults, parse_day, parse_track_index, results_url

FIXTURE = (Path(__file__).parent / "fixtures" / "otb_parx_20261123.html").read_text()
DAY = "2026-11-23"
def test_parses_every_settled_race_on_a_real_day_page():
    races = {r["race_number"]: r for r in parse_day(FIXTURE, DAY)}
    assert sorted(races) == list(range(1, 10))

    r1 = races[1]
    assert [(f["program_number"], f["horse_name"], f["finish_position"]) for f in r1["finishers"]] == [
        ("1", "Donna Light", 1), ("2", "Mi Amol", 2), ("5", "Inner Secrets", 3), ("7", "Punch and Judy", 4)]
    assert (r1["finishers"][0]["win_payoff"], r1["finishers"][0]["place_payoff"],
            r1["finishers"][0]["show_payoff"]) == (7.00, 3.00, 2.10)
    assert r1["finishers"][1]["win_payoff"] is None
    assert r1["scratches"] == ["Likeastraightshot"]
    assert {p["wager_type"]: (p["base_amount"], p["winning_numbers"], p["payoff_amount"])
            for p in r1["payoffs"]} == {"EXACTA": (2.0, "1-2", 30.20), "TRIFECTA": (1.0, "1-2-5", 53.90),
                                        "SUPERFECTA": (1.0, "1-2-5-7", 80.20)}

    # Race 5 paid no show; race 6 has two Daily Double payouts; race 9 a 50-cent Pick 5.
    assert all(f["show_payoff"] is None for f in races[5]["finishers"])
    assert [p["winning_numbers"] for p in races[6]["payoffs"] if p["wager_type"] == "DAILY_DOUBLE"] == ["5/1", "5/9"]
    pick5 = next(p for p in races[9]["payoffs"] if p["wager_type"] == "PICK5")
    assert (pick5["base_amount"], pick5["winning_numbers"], pick5["payoff_amount"]) == (0.5, "1,3,5/9/4/4/7", 895.20)
    assert races[6]["payoffs"][2]["payoff_amount"] == 3550.20
    assert races[9]["scratches"] == ["Liberty Star", "Enforcer", "Macaw", "Risk Factor"]

    # Multi-race payoffs line up with the grader's combination matching.
    assert grading._combo_matches(["1", "9", "4", "4", "7"], pick5["winning_numbers"])
    assert not grading._combo_matches(["2", "9", "4", "4", "7"], pick5["winning_numbers"])


def test_other_dates_unrun_races_and_dead_heats_are_left_out():
    assert parse_day(FIXTURE, "2026-11-24") == []  # OTB serves its latest day for a missing dated page
    unrun = FIXTURE.replace('<th class="amt">Win</th>', '<th class="amt">M/L Odds</th>', 1)
    assert 1 not in {r["race_number"] for r in parse_day(unrun, DAY)}
    dead_heat = re.sub(r'<td class="amt"></td>(\s*<td class="amt">\$6\.00</td>)', r'<td class="amt">$9.00</td>\1',
                       FIXTURE, count=1)
    assert dead_heat != FIXTURE
    assert 1 not in {r["race_number"] for r in parse_day(dead_heat, DAY)}


def test_track_index_and_urls():
    links = ["https://www.offtrackbetting.com/results/63/parx-racing.html#race5",
             "https://www.offtrackbetting.com/results/77/belmont-park.html",
             "https://www.offtrackbetting.com/results/1043/lingfield-uk.html",
             "https://www.offtrackbetting.com/results/57/mountaineer-park-20261123.html"]
    assert parse_track_index(links) == {"PRX": (63, "parx-racing"), "BEL": (77, "belmont-park"),
                                        "MNR": (57, "mountaineer-park")}
    assert results_url(63, "parx-racing", DAY) == "https://www.offtrackbetting.com/results/63/parx-racing-20261123.html"


class FakeFirecrawl:
    def __init__(self, html=FIXTURE, links=(), error=None):
        self.html, self.links, self.error, self.calls = html, list(links), error, []

    def scrape(self, url, **options):
        self.calls.append((url, options))
        if self.error:
            raise self.error
        return {"links": self.links} if options.get("formats") == ["links"] else {"html": self.html}


def _parx_cards(db, track="PRX", names=None):
    fixture = {r["race_number"]: r for r in parse_day(FIXTURE, DAY)}
    post = datetime(2026, 11, 23, 17, 5, tzinfo=timezone.utc)
    races = []
    for number, result in fixture.items():
        runners = [{"program_number": f["program_number"], "post_position": int(f["program_number"]),
                    "horse_id": f"h{number}-{f['program_number']}",
                    "horse_name": (names or {}).get((number, f["program_number"]), f["horse_name"]),
                    "scratched": False} for f in result["finishers"]]
        races.append({"race_number": number, "post_time": (post + timedelta(minutes=27 * number)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"), "is_cancelled": False, "changes": [], "runners": runners})
    ingest.store_card(db, {"meet": {"meet_id": f"{track}-{DAY}", "track_id": track, "race_date": DAY},
                           "races": races}, "puntersedge")


def _source(db, client, hour=23):
    return OTBResults(db, client, now=lambda: datetime(2026, 11, 23, hour, 0, tzinfo=timezone.utc))


def test_reads_each_track_once_and_stores_official_results(db, monkeypatch):
    monkeypatch.setenv("OTB_MIN_INTERVAL_MIN", "60")
    _parx_cards(db)
    client = FakeFirecrawl()
    out = do_results(db, DAY, [_source(db, client)])
    assert out["official"] == 9
    url, options = client.calls[0]
    assert url.endswith("/results/63/parx-racing-20261123.html")
    assert options["includeTags"] == ["#results_content"]
    assert db.scalar("SELECT COUNT(*) FROM results WHERE source='otb'") == 9
    assert db.scalar("SELECT win_payoff FROM result_finishers WHERE race_id='PRX-20261123-1' "
                     "AND finish_position=1") == 7.00
    assert db.scalar("SELECT COUNT(*) FROM result_payoffs WHERE race_id='PRX-20261123-9'") == 7
    # Everything is official now, so nothing is due and no page is read again.
    assert do_results(db, DAY, [_source(db, client)])["official"] == 0
    assert len(client.calls) == 1


def test_waits_for_races_to_run_and_respects_interval_and_budget(db, monkeypatch):
    monkeypatch.setenv("OTB_MIN_INTERVAL_MIN", "60")
    _parx_cards(db)
    client = FakeFirecrawl()
    early = OTBResults(db, client, now=lambda: datetime(2026, 11, 23, 17, 0, tzinfo=timezone.utc))
    assert early.result_meets(DAY) == []  # no race has run yet

    # Race 1 (17:32Z) has run at 17:50Z; the page is read once, then not again within the hour.
    first = OTBResults(db, FakeFirecrawl(html=FIXTURE.replace("Race 2</h2>", "Race 20</h2>")),
                       now=lambda: datetime(2026, 11, 23, 17, 50, tzinfo=timezone.utc))
    assert [m["track_id"] for m in first.result_meets(DAY)] == ["PRX"]
    assert do_results(db, DAY, [first])["official"] == 8
    later = OTBResults(db, client, now=lambda: datetime(2026, 11, 23, 18, 20, tzinfo=timezone.utc))
    assert later.result_meets(DAY) == []
    assert [m["track_id"] for m in _source(db, client).result_meets(DAY)] == ["PRX"]

    monkeypatch.setenv("OTB_DAILY_PAGES", "1")
    assert do_results(db, DAY, [_source(db, client)])["official"] == 0
    assert client.calls == []


def test_block_stops_otb_for_the_day_and_mismatches_are_skipped(db):
    _parx_cards(db, names={(3, "4"): "Somebody Else"})
    blocked = FakeFirecrawl(error=OTBBlocked("OTB returned HTTP 403"))
    assert do_results(db, DAY, [_source(db, blocked, hour=21)])["official"] == 0
    assert _source(db, FakeFirecrawl()).result_meets(DAY) == []
    db.execute("DELETE FROM job_runs WHERE job='otb_blocked'")

    assert do_results(db, DAY, [_source(db, FakeFirecrawl())])["official"] == 8
    assert db.one("SELECT race_id FROM results WHERE race_id='PRX-20261123-3'") is None


def test_unknown_tracks_are_learned_from_the_daily_index(db):
    _parx_cards(db, track="BEL")
    client = FakeFirecrawl(links=["https://www.offtrackbetting.com/results/77/belmont-park.html"])
    assert do_results(db, DAY, [_source(db, client)])["official"] == 9
    assert client.calls[0][0].endswith("/results/horse-racing.html")
    assert client.calls[1][0].endswith("/results/77/belmont-park-20261123.html")
    assert db.one("SELECT otb_id, slug FROM otb_tracks WHERE track_id='BEL'") == {"otb_id": 77, "slug": "belmont-park"}
