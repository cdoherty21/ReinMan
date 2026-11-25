from datetime import datetime, timedelta, timezone

from reinman import features, ingest, news, news_events, schedule
from reinman.util import to_iso

NOW = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Barn notes</title><link rel="alternate" href="https://example.com/notes"/>
<updated>2026-11-25T12:00:00Z</updated><content type="html">&lt;p&gt;Tapit's Gunner was scratched from Saturday's race.&lt;/p&gt;</content></entry>
</feed>"""


def test_atom_feeds_and_article_text_are_read():
    rows = news.parse_feed(ATOM, "Track notes", to_iso(NOW))
    assert rows == [{"url": "https://example.com/notes", "source": "Track notes", "title": "Barn notes",
                     "published_at": "2026-11-25T12:00:00.000Z", "first_seen_at": to_iso(NOW),
                     "body": "Tapit's Gunner was scratched from Saturday's race."}]


def _card(db):
    ingest.store_card(db, {"meet": {"meet_id": "PRX-x", "track_id": "PRX", "race_date": "2026-11-25"},
                           "races": [{"race_number": 1, "post_time": to_iso(NOW + timedelta(minutes=20)),
                                      "is_cancelled": False, "changes": [], "runners": [
                                          {"program_number": "1", "horse_id": "a", "horse_name": "Tapit's Gunner",
                                           "jockey_name": "Silvera, Ruben", "trainer_name": "Demasi, Kathleen",
                                           "scratched": False},
                                          {"program_number": "2", "horse_id": "b", "horse_name": "Bold",
                                           "jockey_name": "Ocasio, Luis", "trainer_name": "Ness, Jamie",
                                           "scratched": False}]}]}, "t")


def _items(*pairs):
    return [{"url": f"https://example.com/{i}", "source": "Paulick Report", "title": t, "body": b,
             "published_at": to_iso(NOW - timedelta(hours=3)), "first_seen_at": to_iso(NOW - timedelta(hours=2))}
            for i, (t, b) in enumerate(pairs)]


def test_events_need_the_name_and_the_event_in_one_sentence(db):
    _card(db)
    runners = db.query("SELECT * FROM runners")
    events = news_events.extract(_items(
        ("Tapit's Gunner scratched", ""),
        ("Kathleen Demasi suspended 30 days by stewards", ""),
        ("Luis Ocasio wins three", "Bold was bumped. Elsewhere a horse was scratched."),   # name and event apart
        ("Ruben Silvera named to ride Tapit's Gunner", "")), runners)
    kinds = {(e["program_number"], e["kind"], e["role"]) for e in events}
    # One event per runner, kind and article, however many of its names the article mentions.
    assert kinds == {("1", "scratch", "horse"), ("1", "barn_trouble", "trainer"), ("1", "jockey_change", "horse")}


def test_rumours_stay_unconfirmed_until_the_card_agrees_and_earn_an_odds_read(db):
    _card(db)
    db.insert_many("racing_news", [{k: v for k, v in i.items() if k != "body"}
                                   for i in _items(("Tapit's Gunner scratched", ""))])
    assert news_events.refresh(db, "2026-11-25")["events"] == 1
    events = news_events.for_race(db, "PRX-20261125-1", db.query("SELECT * FROM runners"), to_iso(NOW))
    assert events[0]["confirmed"] is False
    assert "Word on the rail, unconfirmed" in news_events.summary(events)
    for k in range(18):          # one read left, and no busy-window logic can outrank a rumour
        db.insert("job_runs", {"job": schedule.BOARD_JOB, "started_at": to_iso(NOW - timedelta(minutes=30 + k)),
                               "status": "reserved"})
    due, why = schedule.board_due(db, NOW)
    assert due and "unconfirmed" in why
    db.execute("UPDATE runners SET scratched=1 WHERE program_number='1'")
    events = news_events.for_race(db, "PRX-20261125-1", db.query("SELECT * FROM runners"), to_iso(NOW))
    assert events[0]["confirmed"] is True


def test_news_caution_only_counts_news_seen_before_post(db):
    _card(db)
    db.insert_many("racing_news", [{k: v for k, v in i.items() if k != "body"}
                                   for i in _items(("Kathleen Demasi suspended by stewards", ""))])
    news_events.refresh(db, "2026-11-25")
    h = features.HistoryBuilder(news_caution=news_events.load_caution(db))
    race = db.one("SELECT * FROM races")
    X, _ = features.featurize_race(h, race, db.query("SELECT * FROM runners ORDER BY program_number"))
    col = features.FEATURES.index("news_caution")
    assert list(X[:, col]) == [1.0, 0.0]
    X_early, _ = features.featurize_race(h, dict(race, post_time=to_iso(NOW - timedelta(hours=5))),
                                         db.query("SELECT * FROM runners ORDER BY program_number"))
    assert X_early[0, col] == 0.0
