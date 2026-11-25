from datetime import datetime, timedelta, timezone

from reinman import ingest, schedule
from reinman.util import to_iso

NOW = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)


def _races(db, minutes_ahead, track="PRX"):
    for n, m in enumerate(minutes_ahead, start=1):
        ingest.store_card(db, {"meet": {"meet_id": f"{track}-x", "track_id": track, "race_date": "2026-11-25"},
                               "races": [{"race_number": n, "post_time": to_iso(NOW + timedelta(minutes=m)),
                                          "is_cancelled": False, "changes": [], "runners": []}]}, "t")


def _read(db, minutes_ago):
    stamp = to_iso(NOW - timedelta(minutes=minutes_ago))
    db.insert("job_runs", {"job": schedule.BOARD_JOB, "started_at": stamp, "finished_at": stamp, "status": "reserved"})


def test_reads_the_board_just_before_posts_and_not_in_between(db):
    _races(db, [10, 40, 70])
    assert schedule.board_due(db, NOW)[0]
    _read(db, 5)
    assert schedule.board_due(db, NOW) == (False, "board read 5 min ago")
    assert not schedule.board_due(db, NOW + timedelta(minutes=20))[0]        # nothing posting soon
    assert schedule.board_due(db, NOW + timedelta(minutes=30))[0]            # race 2 in 10 min


def test_scarce_reads_go_to_the_busiest_windows(db):
    _races(db, [10, 40, 41, 42, 70, 71, 72])
    for k in range(18):
        _read(db, 30 + k)                                                    # one read left today
    due, why = schedule.board_due(db, NOW)
    assert not due and "busier" in why
    assert schedule.board_due(db, NOW + timedelta(minutes=30))[0]


def test_warm_up_before_the_first_race_and_quiet_with_no_racing(db):
    assert schedule.board_due(db, NOW) == (True, "no races on file: looking for cards")
    _read(db, 60)
    assert not schedule.board_due(db, NOW)[0]
    _races(db, [80])
    assert not schedule.board_due(db, NOW)[0]                                # read an hour ago
    later = schedule.board_due(db, NOW + timedelta(hours=3))                 # race gone, nothing on file
    assert later == (True, "no races on file: looking for cards")
    db.execute("DELETE FROM job_runs")
    assert schedule.board_due(db, NOW)[1].startswith("warm-up")


def test_a_moved_post_time_is_a_reason_to_reprice(db):
    _races(db, [30])
    ingest.store_card(db, {"meet": {"meet_id": "PRX-x", "track_id": "PRX", "race_date": "2026-11-25"},
                           "races": [{"race_number": 1, "post_time": to_iso(NOW + timedelta(minutes=40)),
                                      "is_cancelled": False, "changes": [],
                                      "runners": [{"program_number": "1", "horse_id": "a", "horse_name": "A",
                                                   "scratched": False}]}]}, "t")
    assert "post_time" in ingest.store_card(db, {
        "meet": {"meet_id": "PRX-x", "track_id": "PRX", "race_date": "2026-11-25"},
        "races": [{"race_number": 1, "post_time": to_iso(NOW + timedelta(minutes=55)), "is_cancelled": False,
                   "changes": [], "runners": [{"program_number": "1", "horse_id": "a", "horse_name": "A",
                                               "scratched": False}]}]}, "t")["PRX-20261125-1"]
