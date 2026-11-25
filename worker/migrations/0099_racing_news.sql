CREATE TABLE IF NOT EXISTS racing_news (
    url TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS racing_news_recent ON racing_news(published_at);
