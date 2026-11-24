-- Email login with password reset. The password hash lives here once set by a reset;
-- before that, the REINMAN_PASSWORD secret is used. session_epoch increments on every
-- reset so all existing sessions are signed out.
CREATE TABLE IF NOT EXISTS auth_state (
  key    TEXT PRIMARY KEY,   -- password_hash | password_salt | session_epoch
  value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_resets (
  token_hash  TEXT PRIMARY KEY,   -- SHA-256 of the emailed token; the token itself is never stored
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  expires_at  TEXT NOT NULL,
  used_at     TEXT
);

CREATE TABLE IF NOT EXISTS auth_events (
  kind  TEXT NOT NULL,            -- forgot
  ip    TEXT NOT NULL,
  at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_auth_events ON auth_events(kind, ip, at);
