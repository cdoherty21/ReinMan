import numpy as np

from reinman.features import FEATURES, HistoryBuilder, featurize_race
from reinman.models.core import ExplodedLogit, LOGIT_FEATURES, Standardizer, load_model
from reinman.news import parse_feed, relevant
from reinman.research_bundle import cards_from_bundle


def test_intelligence_uses_prior_venue_lineage_and_workouts():
    h = HistoryBuilder()
    previous = {"race_date": "2026-11-15", "track_id": "CD", "surface": "D",
                "distance_yards": 1320, "status": "official"}
    horse = {"horse_id": "a", "horse_name": "Example Horse", "program_number": "1",
             "scratched": False, "trainer_id": "t", "sire": "Sire One", "dam_sire": "Dam Sire",
             "extra": {"workouts": [{"date": "2026-11-10", "track": "CD", "distance_yards": 880, "time": 48}]}}
    other = {"horse_id": "b", "horse_name": "Other Horse", "program_number": "2", "scratched": False}
    h.add_programs(previous, [horse, other])
    h.add_race(previous, [horse, other], {"1": 1, "2": 2})
    current = dict(previous, race_date="2026-11-25", track_id="FL")
    X, ev = featurize_race(h, current, [dict(horse, sire=None, dam_sire=None, extra=None), other])
    assert X[0, FEATURES.index("travel_miles_log")] > 0
    assert ev[0]["pedigree"]["dam_sire"] == "Dam Sire"
    assert X[0, FEATURES.index("pp_works_30d")] == 1
    assert ev[0]["trainer_30d"] == {"wins": 1, "starts": 1}
    assert np.isfinite(X[0, FEATURES.index("damsire_surface_win")])
    assert np.isnan(X[1, FEATURES.index("pp_works_30d")])


def test_old_model_artifact_remains_usable():
    legacy_features = FEATURES[:FEATURES.index("dam_surface_win")]
    legacy_logit = [name for name in LOGIT_FEATURES if name in legacy_features]
    model = ExplodedLogit()
    model.std = Standardizer([FEATURES.index(f) for f in legacy_logit], np.ones(len(legacy_logit)))
    model.beta = np.zeros(len(legacy_logit))
    artifact = {"kind": "ensemble", "features": legacy_features, "logit": model.to_json(),
                "gbm": None, "w_market": [1, 0, 0], "w_nomarket": [1, 0]}
    p, _ = load_model(artifact).predict(np.zeros((3, len(FEATURES))))
    assert np.allclose(p, np.full(3, 1 / 3))


def test_rss_matches_full_names_and_rejects_future_articles():
    xml = b"""<rss><channel><item><title>Example Horse set for return</title>
    <link>https://example.com/news</link><pubDate>Tue, 24 Nov 2026 12:00:00 GMT</pubDate></item>
    <item><title>Example Horse tomorrow</title><link>https://example.com/future</link>
    <pubDate>Thu, 26 Nov 2026 12:00:00 GMT</pubDate></item></channel></rss>"""
    rows = parse_feed(xml, "Example", "2026-11-25T00:00:00.000Z")
    assert len(rows) == 1
    assert relevant(rows, {"horse_name": "Example Horse"}, {}) == rows
    assert not relevant(rows, {"horse_name": "Example"}, {})


def test_bundle_declines_unverified_program_and_quarter_horse():
    row = {"c": "CD", "n": 1, "date": "2099-11-25", "track": "Churchill Downs",
           "breed": "thoroughbred", "starters": 1, "url": "https://example.com/entry",
           "post": "1:00 PM ET", "entries": [{"horse": "Example Horse", "program": ""}]}
    cards = cards_from_bundle([row, dict(row, breed="quarter horse")])
    assert len(cards) == 1
    assert not cards[0]["complete"]
    assert cards[0]["races"][0]["runners"][0]["program_number"] is None



def test_news_publisher_and_relevance():
    xml = b"""<rss><channel><item><title>Sovereignty Resumes Jogging - Paulick Report</title>
    <link>https://news.google.com/rss/articles/abc</link><source>Paulick Report</source>
    <pubDate>Tue, 24 Nov 2026 12:00:00 GMT</pubDate></item></channel></rss>"""
    rows = parse_feed(xml, "Google News: Paulick Report", "2026-11-25T00:00:00.000Z")
    assert rows[0]["source"] == "Paulick Report"
    assert rows[0]["title"] == "Sovereignty Resumes Jogging"
    assert relevant(rows, {"horse_name": "Sovereignty"}, {}) == rows
    assert relevant(rows + [dict(rows[0], url="https://example.com/duplicate")],
                    {"horse_name": "Sovereignty"}, {}) == rows


def test_news_refresh_deduplicates_and_throttles(db, monkeypatch):
    from reinman import news
    monkeypatch.setattr(news, "FEEDS", (("First", "https://example.com/feed"), ("Second", "https://example.com/feed2")))
    def fake(source, url, now):
        return [{"url": "https://example.com/story", "source": source, "title": "Horse workout",
                 "published_at": now, "first_seen_at": now}]
    monkeypatch.setattr(news, "_fetch_feed", fake)
    assert news.refresh(db)["unique_headlines"] == 1
    assert db.scalar("SELECT COUNT(*) FROM racing_news") == 1
    assert news.refresh(db)["skipped"] == "recent news poll"
    monkeypatch.setattr(news, "FEEDS", (("Changed", "https://example.com/feed3"),))
    assert news.refresh(db)["unique_headlines"] == 1


def test_news_defers_rate_limited_provider(db, monkeypatch):
    from reinman import news
    calls = []
    monkeypatch.setattr(news, "FEEDS", (
        ("First", "https://example.com/one"), ("Second", "https://example.com/two"),
        ("Independent", "https://other.example/feed")))
    def fake(source, url, now):
        calls.append(source)
        if source == "First":
            raise news.FeedRateLimited(source)
        return []
    monkeypatch.setattr(news, "_fetch_feed", fake)
    result = news.refresh(db)
    assert calls == ["First", "Independent"]
    assert result["feeds"]["Second"] == "rate limited"
