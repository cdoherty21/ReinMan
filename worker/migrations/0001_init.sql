-- Rein Man schema. Shared by Cloudflare D1 (production) and SQLite (local/tests).
-- All timestamps are ISO-8601 UTC strings: YYYY-MM-DDTHH:MM:SS.sssZ
-- Append-only tables are protected by triggers: predictions, prediction_runners,
-- wagers, locks, grades, wager_grades, model_events. Nothing in them can be
-- updated or deleted, so losing picks can never be removed or rewritten.

CREATE TABLE IF NOT EXISTS tracks (
  track_id    TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  country     TEXT NOT NULL DEFAULT 'USA',
  category    TEXT NOT NULL DEFAULT 'thoroughbred',
  timezone    TEXT,
  lat         REAL,
  lon         REAL,
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS meets (
  meet_id     TEXT PRIMARY KEY,
  track_id    TEXT NOT NULL,
  race_date   TEXT NOT NULL,
  source      TEXT NOT NULL,
  fetched_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_meets_date ON meets(race_date);

CREATE TABLE IF NOT EXISTS races (
  race_id          TEXT PRIMARY KEY,           -- TRACK-YYYYMMDD-RN
  meet_id          TEXT,
  track_id         TEXT NOT NULL,
  race_date        TEXT NOT NULL,              -- local race date YYYY-MM-DD
  race_number      INTEGER NOT NULL,
  post_time        TEXT,                       -- UTC ISO
  surface          TEXT,                       -- D / T / S (synthetic) / NULL
  distance_yards   INTEGER,
  distance_desc    TEXT,
  race_type        TEXT,
  race_class       TEXT,
  race_name        TEXT,
  grade            TEXT,
  purse            INTEGER,
  min_claim        INTEGER,
  max_claim        INTEGER,
  age_restriction  TEXT,
  sex_restriction  TEXT,
  breed            TEXT,
  pools            TEXT,                       -- JSON: wagering pools offered [{wager_type, base, race_list}]
  track_condition  TEXT,
  weather_desc     TEXT,
  temp_f           REAL,
  precip_pct       REAL,
  is_cancelled     INTEGER NOT NULL DEFAULT 0,
  status           TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled|official|cancelled
  source           TEXT NOT NULL,
  updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_races_date ON races(race_date);
CREATE INDEX IF NOT EXISTS idx_races_track ON races(track_id, race_date);

CREATE TABLE IF NOT EXISTS runners (
  race_id          TEXT NOT NULL,
  program_number   TEXT NOT NULL,
  post_position    INTEGER,
  horse_id         TEXT NOT NULL,
  horse_name       TEXT NOT NULL,
  jockey_id        TEXT,
  jockey_name      TEXT,
  trainer_id       TEXT,
  trainer_name     TEXT,
  sire             TEXT,
  dam              TEXT,
  dam_sire         TEXT,
  weight           REAL,
  equipment        TEXT,
  medication       TEXT,
  claiming         INTEGER,
  ml_odds          REAL,       -- decimal profit odds (5/2 -> 2.5)
  live_odds        REAL,
  coupled_type     TEXT,
  scratched        INTEGER NOT NULL DEFAULT 0,
  extra            TEXT,       -- JSON: optional rich past-performance data from richer sources
  updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (race_id, program_number)
);
CREATE INDEX IF NOT EXISTS idx_runners_horse ON runners(horse_id);
CREATE INDEX IF NOT EXISTS idx_runners_jockey ON runners(jockey_id);
CREATE INDEX IF NOT EXISTS idx_runners_trainer ON runners(trainer_id);

CREATE TABLE IF NOT EXISTS odds_snapshots (
  race_id         TEXT NOT NULL,
  program_number  TEXT NOT NULL,
  captured_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  live_odds       REAL,
  win_pool        REAL,
  place_pool      REAL,
  show_pool       REAL
);
CREATE INDEX IF NOT EXISTS idx_odds_race ON odds_snapshots(race_id, captured_at);

CREATE TABLE IF NOT EXISTS race_changes (
  race_id     TEXT NOT NULL,
  change_type TEXT,
  text        TEXT NOT NULL,
  seen_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (race_id, change_type, text)
);

CREATE TABLE IF NOT EXISTS results (
  race_id          TEXT PRIMARY KEY,
  official_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  winning_time     REAL,
  fractions        TEXT,
  track_condition  TEXT,
  surface          TEXT,
  source           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS result_finishers (
  race_id          TEXT NOT NULL,
  program_number   TEXT,
  horse_name       TEXT NOT NULL,
  finish_position  INTEGER NOT NULL,
  win_payoff       REAL,
  place_payoff     REAL,
  show_payoff      REAL,
  dead_heat        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rf_race ON result_finishers(race_id);

CREATE TABLE IF NOT EXISTS result_payoffs (
  race_id          TEXT NOT NULL,
  wager_type       TEXT NOT NULL,   -- normalized: WIN PLACE SHOW EXACTA TRIFECTA SUPERFECTA DAILY_DOUBLE PICK3..PICK6 OTHER
  wager_name       TEXT,
  base_amount      REAL,
  winning_numbers  TEXT,
  payoff_amount    REAL,
  total_pool       REAL,
  carryover        REAL
);
CREATE INDEX IF NOT EXISTS idx_rp_race ON result_payoffs(race_id);

CREATE TABLE IF NOT EXISTS result_scratches (
  race_id     TEXT NOT NULL,
  horse_name  TEXT NOT NULL
);

-- ---------- Models & audit trail ----------
CREATE TABLE IF NOT EXISTS models (
  model_version  TEXT PRIMARY KEY,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  kind           TEXT NOT NULL,
  status         TEXT NOT NULL,   -- candidate|champion|retired|rejected
  train_window   TEXT,
  metrics        TEXT,
  artifact       TEXT NOT NULL,
  notes          TEXT
);

CREATE TABLE IF NOT EXISTS model_events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  model_version  TEXT,
  event          TEXT NOT NULL,
  details        TEXT
);

CREATE TABLE IF NOT EXISTS backtests (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  model_kind   TEXT NOT NULL,
  test_window  TEXT NOT NULL,
  metrics      TEXT NOT NULL
);

-- ---------- Predictions (append-only) ----------
CREATE TABLE IF NOT EXISTS predictions (
  prediction_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  race_id         TEXT NOT NULL,
  model_version   TEXT NOT NULL,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  reason          TEXT NOT NULL,          -- initial|scratch|odds|condition|jockey|refresh
  top1            TEXT NOT NULL,
  top2            TEXT,
  top3            TEXT,
  most_likely     TEXT NOT NULL,
  value_pick      TEXT,
  dark_horse      TEXT,
  confidence      TEXT NOT NULL,          -- High|Medium|Low
  confidence_score REAL NOT NULL,
  decision        TEXT NOT NULL,          -- play|pass
  pace            TEXT,                   -- JSON or NULL when unavailable
  take            TEXT NOT NULL,
  inputs_hash     TEXT NOT NULL,
  data_notes      TEXT
);
CREATE INDEX IF NOT EXISTS idx_pred_race ON predictions(race_id, created_at);

CREATE TABLE IF NOT EXISTS prediction_runners (
  prediction_id   INTEGER NOT NULL,
  program_number  TEXT NOT NULL,
  horse_name      TEXT NOT NULL,
  rank            INTEGER NOT NULL,
  win_prob        REAL NOT NULL,
  place_prob      REAL NOT NULL,
  show_prob       REAL NOT NULL,
  exp_finish      REAL NOT NULL,
  fair_odds       REAL NOT NULL,
  odds_used       REAL,
  odds_source     TEXT,                   -- live|morning_line|NULL
  market_prob     REAL,
  ev_win          REAL,
  uncertainty     REAL,
  factors         TEXT,                   -- JSON list of evidence lines
  PRIMARY KEY (prediction_id, program_number)
);

CREATE TABLE IF NOT EXISTS wagers (
  wager_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  prediction_id   INTEGER NOT NULL,
  race_id         TEXT NOT NULL,          -- race whose result settles it (last leg for multi-race)
  legs            TEXT NOT NULL,          -- JSON list of race_ids
  wager_type      TEXT NOT NULL,
  structure       TEXT NOT NULL,          -- straight|box|key|ticket
  selection       TEXT NOT NULL,          -- e.g. "3"  "3-5"  "3,5/1,2/7"
  combos          INTEGER NOT NULL,
  base_amount     REAL NOT NULL,
  cost            REAL NOT NULL,
  est_prob        REAL NOT NULL,
  est_return      REAL,
  ev              REAL,
  rationale       TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_wagers_pred ON wagers(prediction_id);

-- The official, final selection for a race: latest prediction recorded before post.
CREATE TABLE IF NOT EXISTS locks (
  race_id        TEXT PRIMARY KEY,
  prediction_id  INTEGER NOT NULL,
  locked_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS grades (
  race_id          TEXT PRIMARY KEY,
  prediction_id    INTEGER NOT NULL,
  graded_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  top1_finish      INTEGER,
  top2_finish      INTEGER,
  top3_finish      INTEGER,
  winner_rank      INTEGER,               -- Rein Man's rank of the actual winner
  top1_odds        REAL,                  -- odds used at lock time
  top1_win_payoff  REAL,                  -- official $2 payoff if won
  log_loss         REAL,
  brier            REAL,
  status           TEXT NOT NULL,         -- graded|no_contest|top1_scratched
  details          TEXT
);

CREATE TABLE IF NOT EXISTS wager_grades (
  wager_id   INTEGER PRIMARY KEY,
  graded_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  status     TEXT NOT NULL,              -- won|lost|refunded|partial_refund
  payout     REAL NOT NULL,              -- total returned (incl. refunds)
  refund     REAL NOT NULL DEFAULT 0,
  notes      TEXT
);

-- ---------- Actual (manually recorded) wagers: kept separate ----------
CREATE TABLE IF NOT EXISTS actual_wagers (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  race_date    TEXT NOT NULL,
  track_id     TEXT,
  race_number  INTEGER,
  wager_type   TEXT NOT NULL,
  selection    TEXT NOT NULL,
  amount       REAL NOT NULL,
  payout       REAL,
  settled_at   TEXT,
  notes        TEXT
);

-- ---------- Derived & operational ----------
CREATE TABLE IF NOT EXISTS stats_cache (
  key          TEXT PRIMARY KEY,
  computed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  json         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  job          TEXT NOT NULL,
  started_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  finished_at  TEXT,
  status       TEXT NOT NULL DEFAULT 'running',
  details      TEXT
);

-- ---------- Integrity triggers ----------
CREATE TRIGGER IF NOT EXISTS predictions_no_update BEFORE UPDATE ON predictions
BEGIN SELECT RAISE(ABORT, 'predictions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS predictions_no_delete BEFORE DELETE ON predictions
BEGIN SELECT RAISE(ABORT, 'predictions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS predictions_before_post BEFORE INSERT ON predictions
WHEN (SELECT post_time FROM races WHERE race_id = NEW.race_id) IS NULL
  OR (SELECT post_time FROM races WHERE race_id = NEW.race_id) <= strftime('%Y-%m-%dT%H:%M:%fZ','now')
BEGIN SELECT RAISE(ABORT, 'predictions must be recorded before post time'); END;

CREATE TRIGGER IF NOT EXISTS pr_no_update BEFORE UPDATE ON prediction_runners
BEGIN SELECT RAISE(ABORT, 'prediction_runners are append-only'); END;
CREATE TRIGGER IF NOT EXISTS pr_no_delete BEFORE DELETE ON prediction_runners
BEGIN SELECT RAISE(ABORT, 'prediction_runners are append-only'); END;

CREATE TRIGGER IF NOT EXISTS wagers_no_update BEFORE UPDATE ON wagers
BEGIN SELECT RAISE(ABORT, 'wagers are append-only'); END;
CREATE TRIGGER IF NOT EXISTS wagers_no_delete BEFORE DELETE ON wagers
BEGIN SELECT RAISE(ABORT, 'wagers are append-only'); END;

CREATE TRIGGER IF NOT EXISTS locks_no_update BEFORE UPDATE ON locks
BEGIN SELECT RAISE(ABORT, 'locks are permanent'); END;
CREATE TRIGGER IF NOT EXISTS locks_no_delete BEFORE DELETE ON locks
BEGIN SELECT RAISE(ABORT, 'locks are permanent'); END;
CREATE TRIGGER IF NOT EXISTS locks_valid BEFORE INSERT ON locks
WHEN (SELECT p.created_at FROM predictions p WHERE p.prediction_id = NEW.prediction_id AND p.race_id = NEW.race_id) IS NULL
  OR (SELECT p.created_at FROM predictions p WHERE p.prediction_id = NEW.prediction_id)
     >= (SELECT post_time FROM races WHERE race_id = NEW.race_id)
BEGIN SELECT RAISE(ABORT, 'lock must reference a prediction for this race recorded before post time'); END;

CREATE TRIGGER IF NOT EXISTS grades_no_update BEFORE UPDATE ON grades
BEGIN SELECT RAISE(ABORT, 'grades are permanent'); END;
CREATE TRIGGER IF NOT EXISTS grades_no_delete BEFORE DELETE ON grades
BEGIN SELECT RAISE(ABORT, 'grades are permanent'); END;
CREATE TRIGGER IF NOT EXISTS wager_grades_no_update BEFORE UPDATE ON wager_grades
BEGIN SELECT RAISE(ABORT, 'wager grades are permanent'); END;
CREATE TRIGGER IF NOT EXISTS wager_grades_no_delete BEFORE DELETE ON wager_grades
BEGIN SELECT RAISE(ABORT, 'wager grades are permanent'); END;

CREATE TRIGGER IF NOT EXISTS model_events_no_update BEFORE UPDATE ON model_events
BEGIN SELECT RAISE(ABORT, 'audit trail is append-only'); END;
CREATE TRIGGER IF NOT EXISTS model_events_no_delete BEFORE DELETE ON model_events
BEGIN SELECT RAISE(ABORT, 'audit trail is append-only'); END;
CREATE TRIGGER IF NOT EXISTS models_no_delete BEFORE DELETE ON models
BEGIN SELECT RAISE(ABORT, 'model versions are preserved'); END;
CREATE TRIGGER IF NOT EXISTS models_artifact_frozen BEFORE UPDATE OF artifact, kind, created_at, train_window ON models
BEGIN SELECT RAISE(ABORT, 'model artifacts are immutable'); END;

-- Login throttling (worker auth)
CREATE TABLE IF NOT EXISTS login_failures (
  ip  TEXT NOT NULL,
  at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_login_failures ON login_failures(ip, at);
