// Rein Man Worker: private API + static app + scheduled locking.
import { redditConfigured, searchDiscussions, type RedditConfig } from "./reddit";
import { etHour, monitorPlan } from "./schedule";
import { COOKIE, checkPassword, hashPassword, makeSession, randomToken, readCookie, sameEmail, sessionCookie, sha256, verifySession } from "./auth";

export interface Env extends RedditConfig {
  DB: D1Database;
  ASSETS: Fetcher;
  /** The login email; password-reset emails go here. (REINMAN_USERNAME is accepted as a fallback.) */
  REINMAN_EMAIL?: string;
  REINMAN_USERNAME?: string;
  /** First password, used until a reset stores a new one in D1. */
  REINMAN_PASSWORD?: string;
  /** Resend (resend.com) API key for password-reset emails. */
  RESEND_API_KEY?: string;
  /** Sender address; defaults to Resend's test sender, which can only mail the account owner. */
  MAIL_FROM?: string;
  /** "true" turns the password login off: anyone with the link can use the app. */
  AUTH_DISABLED?: string;
  SESSION_SECRET?: string;
  GITHUB_TOKEN?: string;
  GITHUB_REPO?: string;
  GITHUB_WORKFLOW?: string;
  GITHUB_REF?: string;
  MONITOR_MIN_GAP_MIN?: string;
}

const MAX_DOC_CHARS = 1_000_000;
const CHUNK_CHARS = 40_000;
const MAX_DOC_BYTES = 8 * 1024 * 1024;
const FILE_CHUNK_BYTES = 192 * 1024;
const NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')";

const json = (data: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers },
  });

const SECURITY_HEADERS: Record<string, string> = {
  "X-Frame-Options": "DENY",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Content-Security-Policy":
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
  "X-Robots-Tag": "noindex, nofollow",
};

function withSecurity(res: Response): Response {
  const r = new Response(res.body, res);
  for (const [k, v] of Object.entries(SECURITY_HEADERS)) r.headers.set(k, v);
  return r;
}

async function all<T = Record<string, unknown>>(db: D1Database, sql: string, ...params: unknown[]): Promise<T[]> {
  const r = await db.prepare(sql).bind(...params).all<T>();
  return r.results ?? [];
}
async function first<T = Record<string, unknown>>(db: D1Database, sql: string, ...params: unknown[]): Promise<T | null> {
  return (await db.prepare(sql).bind(...params).first<T>()) ?? null;
}

function parseJSON(v: unknown) {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
}

function etDate(d = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(d);
}

// ------------------------------------------------------------------ locking
export async function lockDue(db: D1Database): Promise<number> {
  const r = await db
    .prepare(
      `INSERT OR IGNORE INTO locks (race_id, prediction_id)
       SELECT r.race_id,
              (SELECT p.prediction_id FROM predictions p WHERE p.race_id = r.race_id AND p.created_at < r.post_time
                ORDER BY p.created_at DESC, p.prediction_id DESC LIMIT 1)
       FROM races r LEFT JOIN locks l ON l.race_id = r.race_id
       WHERE l.race_id IS NULL AND r.post_time IS NOT NULL AND r.post_time <= ${NOW_SQL}
         AND EXISTS (SELECT 1 FROM predictions p WHERE p.race_id = r.race_id AND p.created_at < r.post_time)`,
    )
    .run();
  return r.meta.changes ?? 0;
}

/** Start the race-day monitor when the card calls for it (see schedule.ts): just before
 *  posts, a warm-up before the first race, and results sweeps after races. Post times are
 *  re-read on every tick, so the plan follows the card as it changes. */
async function dispatchMonitor(env: Env, when: Date): Promise<string> {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return "dispatch not configured";
  const rows = await all<{ post_time: string; status: string }>(env.DB,
    `SELECT post_time, status FROM races WHERE is_cancelled = 0 AND status != 'cancelled'
       AND post_time BETWEEN strftime('%Y-%m-%dT%H:%M:%fZ','now','-3 hours')
                         AND strftime('%Y-%m-%dT%H:%M:%fZ','now','+24 hours')`);
  const last = await first<{ at: string | null }>(env.DB,
    "SELECT MAX(started_at) AS at FROM job_runs WHERE job = 'monitor_dispatch' AND status IN ('ok','running')");
  const plan = monitorPlan(when.getTime(),
    rows.map((r) => ({ post: Date.parse(r.post_time), official: r.status === "official" }))
      .filter((r) => Number.isFinite(r.post)),
    last?.at ? Date.parse(last.at) : null, etHour(when), Number(env.MONITOR_MIN_GAP_MIN || "20"));
  if (!plan.due) return plan.reason;
  const claim = await first<{ id: number }>(env.DB,
    `INSERT INTO job_runs (job, status) SELECT 'monitor_dispatch', 'running' WHERE NOT EXISTS (
       SELECT 1 FROM job_runs WHERE job = 'monitor_dispatch' AND status IN ('ok','running')
       AND started_at >= strftime('%Y-%m-%dT%H:%M:%fZ','now','-4 minutes')) RETURNING id`);
  if (!claim) return "already started";
  let result = "dispatch failed";
  try {
    result = await dispatchWorkflow(env, env.GITHUB_WORKFLOW || "reinman-monitor.yml");
  } catch (error) {
    console.error("Could not start the monitor", error);
  } finally {
    await env.DB.prepare(`UPDATE job_runs SET finished_at=${NOW_SQL}, status=?, details=? WHERE id=?`)
      .bind(result === "dispatch 204" ? "ok" : "error", `${plan.reason}: ${result}`, claim.id).run();
  }
  return `${plan.reason}: ${result}`;
}

async function dispatchWorkflow(env: Env, workflow: string): Promise<string> {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return "dispatch not configured";
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflow}/dispatches`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": "rein-man-worker",
    },
    body: JSON.stringify({ ref: env.GITHUB_REF || "main" }),
  });
  return `dispatch ${response.status}`;
}

/** Start a workflow at most once per ``cooldown`` (an SQLite modifier like '-20 hours').
 *  The claim is written to job_runs first, so overlapping cron ticks can't both start it. */
async function dispatchOnce(env: Env, job: string, workflow: string, cooldown: string): Promise<string> {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return "dispatch not configured";
  const claim = await first<{ id: number }>(env.DB,
    `INSERT INTO job_runs (job, status)
     SELECT ?, 'running' WHERE NOT EXISTS (
       SELECT 1 FROM job_runs WHERE job=? AND status IN ('ok','running')
       AND started_at >= strftime('%Y-%m-%dT%H:%M:%fZ','now', ?)
     ) RETURNING id`, job, job, cooldown);
  if (!claim) return "already started";
  let result = "dispatch failed";
  try {
    result = await dispatchWorkflow(env, workflow);
  } catch (error) {
    console.error(`Could not start ${workflow}`, error);
  } finally {
    await env.DB.prepare(`UPDATE job_runs SET finished_at=${NOW_SQL}, status=?, details=? WHERE id=?`)
      .bind(result === "dispatch 204" ? "ok" : "error", result, claim.id).run();
  }
  return result;
}

/** Daily cycle ~6:40am ET (10:40 UTC) and weekly learning Mondays ~5:20am ET (09:20 UTC). */
export function scheduledJobsDue(when: Date): { daily: boolean; learn: boolean } {
  const h = when.getUTCHours(), m = when.getUTCMinutes();
  return {
    daily: h === 10 && m >= 40 && m < 55,
    learn: when.getUTCDay() === 1 && h === 9 && m >= 20 && m < 35,
  };
}

async function dispatchDocuments(env: Env): Promise<string> {
  const pending = await first(env.DB, "SELECT id FROM documents WHERE status='pending' LIMIT 1");
  if (!pending) return "no pending documents";
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return "dispatch not configured";
  const claim = await first(env.DB,
    `INSERT INTO job_runs (job, status)
     SELECT 'document_dispatch', 'running' WHERE NOT EXISTS (
       SELECT 1 FROM job_runs WHERE job='document_dispatch'
       AND started_at >= strftime('%Y-%m-%dT%H:%M:%fZ','now','-2 minutes')
     ) RETURNING id`);
  if (!claim) return "recently dispatched";
  let result = "dispatch failed";
  try {
    result = await dispatchWorkflow(env, "reinman-documents.yml");
  } catch (error) {
    console.error("Could not start document scanner", error);
  } finally {
    await env.DB.prepare(`UPDATE job_runs SET finished_at=${NOW_SQL}, status=?, details=? WHERE id=?`)
      .bind(result === "dispatch 204" ? "ok" : "error", result, claim.id).run();
  }
  return result;
}

// ------------------------------------------------------------------ API
type Handler = (req: Request, env: Env, url: URL, m: RegExpMatchArray) => Promise<Response>;

const LATEST_PRED = `COALESCE((SELECT prediction_id FROM locks WHERE race_id = r.race_id),
  (SELECT MAX(prediction_id) FROM predictions WHERE race_id = r.race_id))`;

const routes: [string, RegExp, Handler][] = [
  [
    "GET",
    /^\/api\/today$/,
    async (_req, env, url) => {
      const date = url.searchParams.get("date") || etDate();
      const races = await all(
        env.DB,
        `SELECT r.race_id, r.track_id, t.name AS track_name, r.race_number, r.post_time, r.surface, r.distance_desc,
                r.distance_yards, r.race_class, r.race_type, r.purse, r.track_condition, r.status, r.is_cancelled,
                p.prediction_id, p.top1, p.top2, p.top3, p.value_pick, p.dark_horse, p.confidence, p.decision,
                p.model_version, p.created_at AS predicted_at,
                (SELECT COUNT(*) FROM runners x WHERE x.race_id = r.race_id AND x.scratched = 0) AS field_size,
                (SELECT 1 FROM locks l WHERE l.race_id = r.race_id) AS locked,
                (SELECT GROUP_CONCAT(program_number) FROM result_finishers f WHERE f.race_id = r.race_id AND f.finish_position = 1) AS winner,
                g.top1_finish, g.status AS grade_status
         FROM races r LEFT JOIN tracks t ON t.track_id = r.track_id
         LEFT JOIN predictions p ON p.prediction_id = ${LATEST_PRED}
         LEFT JOIN grades g ON g.race_id = r.race_id
         WHERE r.race_date = ? ORDER BY r.post_time, r.track_id`,
        date,
      );
      const names = await all<{ race_id: string; program_number: string; horse_name: string }>(
        env.DB,
        `SELECT ru.race_id, ru.program_number, ru.horse_name FROM runners ru JOIN races r ON r.race_id = ru.race_id
         WHERE r.race_date = ?`,
        date,
      );
      const nm = new Map(names.map((n) => [`${n.race_id}|${n.program_number}`, n.horse_name]));
      for (const r of races as Record<string, unknown>[]) {
        for (const k of ["top1", "top2", "top3", "value_pick", "dark_horse"]) {
          if (r[k]) r[`${k}_name`] = nm.get(`${r.race_id}|${r[k]}`) ?? null;
        }
      }
      return json({ date, races });
    },
  ],
  [
    "GET",
    /^\/api\/race\/([A-Za-z0-9-]+)$/,
    async (_req, env, _url, m) => {
      const id = m[1];
      const race = await first(
        env.DB,
        `SELECT r.*, t.name AS track_name FROM races r LEFT JOIN tracks t ON t.track_id = r.track_id WHERE race_id = ?`,
        id,
      );
      if (!race) return json({ error: "not found" }, 404);
      const lock = await first<{ prediction_id: number; locked_at: string }>(
        env.DB,
        "SELECT * FROM locks WHERE race_id = ?",
        id,
      );
      const snapshots = await all(
        env.DB,
        "SELECT prediction_id, created_at, reason, model_version, top1, confidence, decision FROM predictions WHERE race_id = ? ORDER BY prediction_id",
        id,
      );
      const want = new URL(_req.url).searchParams.get("snapshot");
      const pid = want ? Number(want) : (lock?.prediction_id ?? (snapshots.at(-1) as { prediction_id?: number })?.prediction_id);
      const prediction = pid ? await first(env.DB, "SELECT * FROM predictions WHERE prediction_id = ? AND race_id = ?", pid, id) : null;
      const picks = pid
        ? (await all(env.DB, "SELECT * FROM prediction_runners WHERE prediction_id = ? ORDER BY rank", pid)).map((r) => ({
            ...r,
            factors: parseJSON((r as { factors: unknown }).factors),
          }))
        : [];
      const wagers = pid
        ? await all(
            env.DB,
            `SELECT w.*, g.status AS grade_status, g.payout FROM wagers w LEFT JOIN wager_grades g ON g.wager_id = w.wager_id
             WHERE w.prediction_id = ? ORDER BY w.wager_id`,
            pid,
          )
        : [];
      const runners = await all(env.DB, "SELECT * FROM runners WHERE race_id = ? ORDER BY post_position, program_number", id);
      const results = await all(env.DB, "SELECT * FROM result_finishers WHERE race_id = ? ORDER BY finish_position", id);
      const payoffs = await all(env.DB, "SELECT * FROM result_payoffs WHERE race_id = ?", id);
      const changes = await all(env.DB, "SELECT * FROM race_changes WHERE race_id = ? ORDER BY seen_at", id);
      const grade = await first(env.DB, "SELECT * FROM grades WHERE race_id = ?", id);
      const docNotes = (
        await all(
          env.DB,
          `SELECT n.id, n.document_id, n.program_number, n.horse_name, n.kind, n.text, n.data, d.filename, d.uploaded_at
           FROM document_notes n JOIN documents d ON d.id = n.document_id
           WHERE n.race_id = ? AND d.status = 'processed'
           AND n.id IN (
             SELECT MIN(n2.id) FROM document_notes n2 JOIN documents d2 ON d2.id = n2.document_id
             WHERE n2.race_id = n.race_id AND d2.status = 'processed'
             GROUP BY n2.race_id, n2.program_number, n2.kind, n2.text
           ) ORDER BY n.id`,
          id,
        )
      ).map((n) => ({ ...n, data: parseJSON((n as { data: unknown }).data) }));
      if (prediction) {
        (prediction as Record<string, unknown>).pace = parseJSON((prediction as Record<string, unknown>).pace);
        (prediction as Record<string, unknown>).data_notes = parseJSON((prediction as Record<string, unknown>).data_notes);
      }
      return json({ race, lock, snapshots, prediction, picks, wagers, runners, results, payoffs, changes, grade, docNotes });
    },
  ],
  [
    "GET",
    /^\/api\/race\/([A-Za-z0-9-]+)\/reddit\/([A-Za-z0-9]+)$/,
    async (_req, env, _url, m) => {
      if (!redditConfigured(env)) return json({ status: "unconfigured", discussions: [] });
      const horse = await first<{ horse_name: string }>(env.DB,
        "SELECT horse_name FROM runners WHERE race_id=? AND program_number=?", m[1], m[2]);
      if (!horse) return json({ error: "Horse not found" }, 404);
      // Shared rate limit across Worker instances, without saving Reddit content.
      const claim = await first<{ id: number }>(env.DB,
        `INSERT INTO job_runs (job, status) SELECT 'reddit_lookup', 'ok'
         WHERE NOT EXISTS (SELECT 1 FROM job_runs WHERE job='reddit_lookup'
           AND started_at >= strftime('%Y-%m-%dT%H:%M:%fZ','now','-10 seconds')) RETURNING id`);
      if (!claim) return json({ status: "rate_limited", discussions: [] }, 429);
      try {
        return json({ status: "ok", discussions: await searchDiscussions(env, horse.horse_name) });
      } catch {
        return json({ status: "unavailable", discussions: [] }, 503);
      }
    },
  ],
  [
    "GET",
    /^\/api\/opportunities$/,
    async (_req, env, url) => {
      const date = url.searchParams.get("date") || etDate();
      const wagers = await all(
        env.DB,
        `SELECT w.*, r.track_id, r.race_number, r.post_time, t.name AS track_name, p.confidence, p.created_at AS predicted_at,
                g.status AS grade_status, g.payout
         FROM races r JOIN predictions p ON p.prediction_id = ${LATEST_PRED}
         JOIN wagers w ON w.prediction_id = p.prediction_id
         LEFT JOIN tracks t ON t.track_id = r.track_id
         LEFT JOIN wager_grades g ON g.wager_id = w.wager_id
         WHERE r.race_date = ? ORDER BY w.ev DESC`,
        date,
      );
      const value = await all(
        env.DB,
        `SELECT r.race_id, r.track_id, r.race_number, r.post_time, t.name AS track_name, pr.program_number, pr.horse_name,
                pr.win_prob, pr.fair_odds, pr.odds_used, pr.odds_source, pr.ev_win, p.confidence,
                (CASE WHEN pr.program_number = p.dark_horse THEN 1 ELSE 0 END) AS is_dark_horse
         FROM races r JOIN predictions p ON p.prediction_id = ${LATEST_PRED}
         JOIN prediction_runners pr ON pr.prediction_id = p.prediction_id
         LEFT JOIN tracks t ON t.track_id = r.track_id
         WHERE r.race_date = ? AND (pr.program_number = p.value_pick OR pr.program_number = p.dark_horse)
         ORDER BY pr.ev_win DESC`,
        date,
      );
      return json({ date, wagers, value });
    },
  ],
  [
    "GET",
    /^\/api\/intelligence$/,
    async (_req, env, url) => {
      const track = url.searchParams.get("track");
      // Only complete official fields count toward rankings. Partial results
      // (e.g. top-four-only) would inflate the apparent win percentage.
      const leaders = await all(env.DB, `
        WITH complete AS (
          SELECT r.race_id FROM races r
          WHERE r.status='official' AND r.race_date>=date('now','-30 days')
            AND r.race_date<date('now') AND (? IS NULL OR r.track_id=?)
            AND NOT EXISTS (
              SELECT 1 FROM runners u WHERE u.race_id=r.race_id AND u.scratched=0
              AND NOT EXISTS (SELECT 1 FROM result_finishers f
                  WHERE f.race_id=u.race_id AND f.program_number=u.program_number)
            )
        )
        SELECT ru.trainer_id AS id, MAX(ru.trainer_name) AS name, COUNT(*) AS starts,
               SUM(CASE WHEN f.finish_position=1 THEN 1 ELSE 0 END) AS wins,
               1.0*SUM(CASE WHEN f.finish_position=1 THEN 1 ELSE 0 END)/COUNT(*) AS win_pct
        FROM complete c JOIN runners ru ON ru.race_id=c.race_id
        JOIN (SELECT race_id,program_number,MIN(finish_position) AS finish_position
              FROM result_finishers GROUP BY race_id,program_number) f
          ON f.race_id=ru.race_id AND f.program_number=ru.program_number
        WHERE ru.scratched=0 AND ru.trainer_id IS NOT NULL AND ru.trainer_name IS NOT NULL
        GROUP BY ru.trainer_id HAVING COUNT(*)>=10
        ORDER BY win_pct DESC, starts DESC, name LIMIT 10`, track, track);
      const headlines = await all(env.DB,
        "SELECT title,url,source,published_at FROM racing_news WHERE published_at>=datetime('now','-7 days') ORDER BY published_at DESC LIMIT 12");
      return json({ leaders, headlines });
    },
  ],
  [
    "GET",
    /^\/api\/performance$/,
    async (_req, env, url) => {
      const track = url.searchParams.get("track");
      const row = await first<{ json: string; computed_at: string }>(
        env.DB,
        "SELECT json, computed_at FROM stats_cache WHERE key = ?",
        track ? `track:${track}` : "overall",
      );
      const tracks = await all(
        env.DB,
        "SELECT substr(key, 7) AS track_id, t.name FROM stats_cache LEFT JOIN tracks t ON t.track_id = substr(key, 7) WHERE key LIKE 'track:%' ORDER BY 1",
      );
      return json({ computed_at: row?.computed_at ?? null, stats: row ? JSON.parse(row.json) : null, tracks });
    },
  ],
  [
    "GET",
    /^\/api\/history$/,
    async (_req, env, url) => {
      const from = url.searchParams.get("from") || "0000-00-00";
      const to = url.searchParams.get("to") || "9999-12-31";
      const track = url.searchParams.get("track");
      const page = Math.max(0, Number(url.searchParams.get("page") || 0));
      const rows = await all(
        env.DB,
        `SELECT r.race_id, r.race_date, r.track_id, r.race_number, r.post_time, p.top1, p.top2, p.top3, p.value_pick,
                p.confidence, p.decision, p.model_version, p.created_at, l.locked_at,
                g.top1_finish, g.top2_finish, g.top3_finish, g.winner_rank, g.top1_odds, g.top1_win_payoff, g.status,
                (SELECT horse_name FROM prediction_runners x WHERE x.prediction_id = p.prediction_id AND x.program_number = p.top1) AS top1_name,
                (SELECT COALESCE(SUM(w.cost),0) FROM wagers w WHERE w.prediction_id = p.prediction_id) AS wagered,
                (SELECT COALESCE(SUM(wg.payout),0) FROM wagers w JOIN wager_grades wg ON wg.wager_id = w.wager_id WHERE w.prediction_id = p.prediction_id) AS returned,
                (SELECT COUNT(*) FROM wagers w LEFT JOIN wager_grades wg ON wg.wager_id = w.wager_id
                 WHERE w.prediction_id = p.prediction_id AND wg.payout IS NULL) AS pending_wagers,
                (SELECT COUNT(*) FROM wagers w WHERE w.prediction_id = p.prediction_id) AS wager_count,
                (SELECT w.wager_type FROM wagers w WHERE w.prediction_id = p.prediction_id ORDER BY w.wager_id LIMIT 1) AS wager_type,
                (SELECT w.selection FROM wagers w WHERE w.prediction_id = p.prediction_id ORDER BY w.wager_id LIMIT 1) AS wager_selection,
                (SELECT wg.status FROM wagers w LEFT JOIN wager_grades wg ON wg.wager_id = w.wager_id
                 WHERE w.prediction_id = p.prediction_id ORDER BY w.wager_id LIMIT 1) AS wager_status
         FROM locks l JOIN races r ON r.race_id = l.race_id JOIN predictions p ON p.prediction_id = l.prediction_id
         LEFT JOIN grades g ON g.race_id = l.race_id
         WHERE r.race_date BETWEEN ? AND ? AND (? IS NULL OR r.track_id = ?)
         ORDER BY r.post_time DESC LIMIT 100 OFFSET ?`,
        from,
        to,
        track,
        track,
        page * 100,
      );
      return json({ rows, page });
    },
  ],
  [
    "GET",
    /^\/api\/models$/,
    async (_req, env) => {
      const models = (
        await all(env.DB, "SELECT model_version, created_at, kind, status, train_window, metrics, notes FROM models ORDER BY created_at DESC")
      ).map((m) => ({ ...m, metrics: parseJSON((m as { metrics: unknown }).metrics) }));
      const events = (await all(env.DB, "SELECT * FROM model_events ORDER BY id DESC LIMIT 200")).map((e) => ({
        ...e,
        details: parseJSON((e as { details: unknown }).details),
      }));
      const backtests = (await all(env.DB, "SELECT * FROM backtests ORDER BY id DESC LIMIT 20")).map((b) => ({
        ...b,
        metrics: parseJSON((b as { metrics: unknown }).metrics),
      }));
      return json({ models, events, backtests });
    },
  ],
  [
    "GET",
    /^\/api\/status$/,
    async (_req, env) => {
      const jobs = await all(env.DB, "SELECT id, job, started_at, finished_at, status, substr(details,1,600) AS details FROM job_runs ORDER BY id DESC LIMIT 40");
      const counts = await first(
        env.DB,
        `SELECT (SELECT COUNT(*) FROM races) AS races, (SELECT COUNT(*) FROM results) AS results,
                (SELECT COUNT(*) FROM predictions) AS predictions, (SELECT COUNT(*) FROM locks) AS locks,
                (SELECT COUNT(*) FROM grades) AS grades, (SELECT MIN(race_date) FROM races) AS first_date,
                (SELECT MAX(race_date) FROM races) AS last_date`,
      );
      const champion = await first(env.DB, "SELECT model_version, kind, created_at FROM models WHERE status = 'champion' ORDER BY created_at DESC LIMIT 1");
      return json({ jobs, counts, champion });
    },
  ],
  [
    "GET",
    /^\/api\/actual-wagers$/,
    async (_req, env) => json({ rows: await all(env.DB, "SELECT * FROM actual_wagers ORDER BY created_at DESC LIMIT 500") }),
  ],
  [
    "POST",
    /^\/api\/actual-wagers$/,
    async (req, env) => {
      const b = (await req.json()) as Record<string, unknown>;
      const amount = Number(b.amount);
      if (!b.race_date || !b.wager_type || !b.selection || !(amount > 0)) return json({ error: "race_date, wager_type, selection and a positive amount are required" }, 400);
      await env.DB.prepare(
        "INSERT INTO actual_wagers (race_date, track_id, race_number, wager_type, selection, amount, notes) VALUES (?,?,?,?,?,?,?)",
      )
        .bind(String(b.race_date), b.track_id ?? null, b.race_number ? Number(b.race_number) : null, String(b.wager_type), String(b.selection), amount, b.notes ?? null)
        .run();
      return json({ ok: true }, 201);
    },
  ],
  [
    "POST",
    /^\/api\/actual-wagers\/(\d+)\/settle$/,
    async (req, env, _u, m) => {
      const b = (await req.json()) as { payout?: number };
      const payout = Number(b.payout);
      if (!(payout >= 0)) return json({ error: "payout must be >= 0" }, 400);
      await env.DB.prepare(`UPDATE actual_wagers SET payout = ?, settled_at = ${NOW_SQL} WHERE id = ? AND payout IS NULL`)
        .bind(payout, Number(m[1]))
        .run();
      return json({ ok: true });
    },
  ],
  [
    "GET",
    /^\/api\/documents$/,
    async (_req, env) => {
      const rows = (
        await all(
          env.DB,
          `SELECT id, uploaded_at, filename, mime, size_bytes, chars, race_date, track_id, status,
                  processed_at, summary, error FROM documents WHERE status != 'uploading' ORDER BY id DESC LIMIT 200`,
        )
      ).map((d) => ({ ...d, summary: parseJSON((d as { summary: unknown }).summary) }));
      return json({ rows });
    },
  ],
  [
    "GET",
    /^\/api\/documents\/(\d+)$/,
    async (_req, env, _u, m) => {
      const doc = await first(env.DB, "SELECT * FROM documents WHERE id = ? AND status != 'uploading'", Number(m[1]));
      if (!doc) return json({ error: "not found" }, 404);
      const notes = (await all(env.DB, "SELECT * FROM document_notes WHERE document_id = ? ORDER BY id", Number(m[1]))).map(
        (n) => ({ ...n, data: parseJSON((n as { data: unknown }).data) }),
      );
      return json({ doc: { ...doc, summary: parseJSON((doc as { summary: unknown }).summary) }, notes });
    },
  ],
  [
    "GET",
    /^\/api\/documents\/(\d+)\/file$/,
    async (_req, env, url, m) => {
      const id = Number(m[1]);
      const doc = await first<{ filename: string }>(
        env.DB, "SELECT filename FROM documents WHERE id = ? AND status != 'uploading'", id,
      );
      if (!doc) return json({ error: "not found" }, 404);
      const firstChunk = await first<{ pdf: number }>(
        env.DB, "SELECT hex(substr(bytes, 1, 5)) = '255044462D' AS pdf FROM document_file_chunks WHERE document_id = ? AND seq = 0", id,
      );
      const headers = new Headers({ "Cache-Control": "private, no-store" });
      const download = url.searchParams.has("download");
      if (!firstChunk || (!download && /\.(html?|docx|xlsx|png|jpe?g|webp|tiff?)$/i.test(doc.filename))) {
        const text = await all<{ text: string }>(
          env.DB, "SELECT text FROM document_chunks WHERE document_id = ? ORDER BY seq", id,
        );
        headers.set("Content-Type", "text/plain; charset=utf-8");
        if (download) headers.set("Content-Disposition", 'attachment; filename="extracted-text.txt"');
        return new Response(text.map((row) => row.text).join(""), { headers });
      }
      const bytes: Uint8Array<ArrayBuffer>[] = [];
      for (let start = 0; ; start += 4) {
        const rows = await all<{ bytes: number[] }>(
          env.DB, "SELECT bytes FROM document_file_chunks WHERE document_id=? AND seq>=? ORDER BY seq LIMIT 4", id, start,
        );
        bytes.push(...rows.map((row) => Uint8Array.from(row.bytes)));
        if (rows.length < 4) break;
      }
      const pdf = firstChunk.pdf === 1;
      // Only native PDFs and inert text are displayed on the authenticated origin.
      headers.set("Content-Type", download ? "application/octet-stream" : pdf ? "application/pdf" : "text/plain; charset=utf-8");
      const name = encodeURIComponent(doc.filename.replace(/[\uD800-\uDFFF]/gu, "\uFFFD")).replace(/['()*]/g, (c) => `%${c.charCodeAt(0).toString(16)}`);
      headers.set("Content-Disposition", `${download ? "attachment" : "inline"}; filename*=UTF-8''${name}`);
      return new Response(new Blob(bytes), { headers });
    },
  ],
  [
    "POST",
    /^\/api\/documents\/scan$/,
    async (_req, env) => {
      const result = await dispatchDocuments(env);
      return json({ started: result === "dispatch 204" || result === "recently dispatched" });
    },
  ],
  [
    "POST",
    /^\/api\/documents$/,
    async (req, env) => {
      const b = (await req.json()) as {
        filename?: string; mime?: string; size_bytes?: number; text?: string;
        race_date?: string; track_id?: string; original?: string;
      };
      const text = typeof b.text === "string" ? b.text : "";
      const filename = String(b.filename || "").slice(0, 200);
      if (!filename || (!text.trim() && b.original === undefined))
        return json({ error: "Choose a document with readable text or upload the original file." }, 400);
      if (text.length > MAX_DOC_CHARS) return json({ error: `Document too large (over ${MAX_DOC_CHARS.toLocaleString()} characters).` }, 413);
      const raceDate = /^\d{4}-\d{2}-\d{2}$/.test(b.race_date ?? "") ? b.race_date : null;
      const track = /^[A-Za-z0-9_]{1,8}$/.test(b.track_id ?? "") ? b.track_id!.toUpperCase() : null;
      let original: string | undefined;
      if (b.original !== undefined) {
        if (typeof b.original !== "string") return json({ error: "Invalid original file." }, 400);
        if (b.original.length > Math.ceil(MAX_DOC_BYTES / 3) * 4)
          return json({ error: "Original file exceeds 8 MB." }, 413);
        try {
          original = atob(b.original);
        } catch {
          return json({ error: "Invalid original file." }, 400);
        }
        if (original.length > MAX_DOC_BYTES) return json({ error: "Original file exceeds 8 MB." }, 413);
        if (!original.length) return json({ error: "Original file is empty." }, 400);
      }
      // Include optional card hints so a corrected hint can be submitted for the same file.
      const content = original === undefined ? new TextEncoder().encode(text)
        : Uint8Array.from(original, (c) => c.charCodeAt(0));
      const digest = await crypto.subtle.digest("SHA-256", content);
      const fingerprint = JSON.stringify([Array.from(new Uint8Array(digest)), raceDate, track]);
      const findUpload = () => first<{ id: number; status: string }>(env.DB,
        "SELECT d.id, d.status FROM document_upload_fingerprints f JOIN documents d ON d.id=f.document_id WHERE f.fingerprint=?",
        fingerprint);
      const duplicateResponse = (doc: { id: number; status: string }) =>
        doc.status === "uploading"
          ? json({ error: "This file is already being uploaded. Try again if it does not appear." }, 409)
          : json({ ok: true, id: doc.id, duplicate: true });
      const existing = await findUpload();
      if (existing) return duplicateResponse(existing);
      let id: number;
      try {
        const [ins] = await env.DB.batch([
          env.DB.prepare(
            "INSERT INTO documents (filename, mime, size_bytes, chars, race_date, track_id, status) VALUES (?,?,?,?,?,?,'uploading')",
          ).bind(filename, String(b.mime || "").slice(0, 100) || null, original?.length ?? (Number(b.size_bytes) || null),
            text.length, raceDate, track),
          env.DB.prepare("INSERT INTO document_upload_fingerprints VALUES (?, last_insert_rowid())").bind(fingerprint),
        ]);
        id = ins.meta.last_row_id;
      } catch (error) {
        const duplicate = await findUpload();
        if (duplicate) return duplicateResponse(duplicate);
        throw error;
      }
      try {
        const textRows = [];
        for (let i = 0, seq = 0; i < text.length; i += CHUNK_CHARS, seq++) {
          textRows.push(env.DB.prepare("INSERT INTO document_chunks (document_id, seq, text) VALUES (?,?,?)")
            .bind(id, seq, text.slice(i, i + CHUNK_CHARS)));
        }
        for (let i = 0; i < textRows.length; i += 8) await env.DB.batch(textRows.slice(i, i + 8));
        if (original !== undefined) {
          const batchBytes = FILE_CHUNK_BYTES * 2;
          for (let offset = 0; offset < content.byteLength; offset += batchBytes) {
            const statements = [];
            for (let i = offset; i < Math.min(offset + batchBytes, content.byteLength); i += FILE_CHUNK_BYTES) {
              const bytes = content.slice(i, i + FILE_CHUNK_BYTES);
              statements.push(env.DB.prepare("INSERT INTO document_file_chunks (document_id, seq, bytes) VALUES (?,?,?)")
                .bind(id, i / FILE_CHUNK_BYTES, bytes.buffer));
            }
            await env.DB.batch(statements);
          }
        }
        await env.DB.prepare("UPDATE documents SET status = 'pending' WHERE id = ?").bind(id).run();
      } catch (error) {
        console.error("Document storage failed", { id, size: content.byteLength, error });
        try {
          await env.DB.batch([
            env.DB.prepare("DELETE FROM document_chunks WHERE document_id=?").bind(id),
            env.DB.prepare("DELETE FROM documents WHERE id = ? AND status = 'uploading'").bind(id),
          ]);
        } catch (cleanupError) {
          console.error("Document staging cleanup failed", { id, cleanupError });
        }
        return json({ error: "The file could not be saved. Please retry this upload." }, 503);
      }
      return json({ ok: true, id }, 201);
    },
  ],
];

type AuthState = { hash: string | null; salt: string | null; epoch: number };

async function authState(db: D1Database): Promise<AuthState> {
  const rows = await all<{ key: string; value: string }>(db, "SELECT key, value FROM auth_state");
  const m = new Map(rows.map((r) => [r.key, r.value]));
  return { hash: m.get("password_hash") ?? null, salt: m.get("password_salt") ?? null, epoch: Number(m.get("session_epoch") ?? 0) };
}

const loginEmail = (env: Env) => env.REINMAN_EMAIL || env.REINMAN_USERNAME;
const RESET_MINUTES = 30;
const MIN_PASSWORD = 10;

async function tooMany(db: D1Database, table: "login_failures" | "auth_events", ip: string, limit: number, window: string, kind?: string) {
  const sql = table === "login_failures"
    ? `SELECT COUNT(*) AS n FROM login_failures WHERE ip = ? AND at > strftime('%Y-%m-%dT%H:%M:%fZ','now','${window}')`
    : `SELECT COUNT(*) AS n FROM auth_events WHERE kind = ? AND ip = ? AND at > strftime('%Y-%m-%dT%H:%M:%fZ','now','${window}')`;
  const r = table === "login_failures" ? await first<{ n: number }>(db, sql, ip) : await first<{ n: number }>(db, sql, kind, ip);
  return (r?.n ?? 0) >= limit;
}

async function sendResetEmail(env: Env, to: string, link: string): Promise<boolean> {
  if (!env.RESEND_API_KEY) return false;
  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: env.MAIL_FROM || "Rein Man <onboarding@resend.dev>",
      to: [to],
      subject: "Reset your Rein Man password",
      text: `Someone (hopefully you) asked to reset the Rein Man password.\n\nSet a new password here (the link works once, for ${RESET_MINUTES} minutes):\n${link}\n\nIf this wasn't you, ignore this email; your password hasn't changed.`,
      html: `<p>Someone (hopefully you) asked to reset the Rein Man password.</p><p><a href="${link}">Set a new password</a>. The link works once, for ${RESET_MINUTES} minutes.</p><p>If this wasn't you, ignore this email; your password hasn't changed.</p>`,
    }),
  });
  if (!r.ok) console.error("Resend error", r.status, await r.text());
  return r.ok;
}

async function handleAuth(req: Request, env: Env, url: URL): Promise<Response | null> {
  const ip = req.headers.get("CF-Connecting-IP") || "unknown";
  if (url.pathname === "/api/login" && req.method === "POST") {
    if (await tooMany(env.DB, "login_failures", ip, 8, "-15 minutes")) return json({ error: "Too many attempts. Try again later." }, 429);
    const body = (await req.json().catch(() => ({}))) as { email?: string; username?: string; password?: string };
    const st = await authState(env.DB);
    const email = loginEmail(env);
    const configured = Boolean(email && env.SESSION_SECRET && (st.hash || env.REINMAN_PASSWORD));
    // evaluate both checks every time so a wrong email takes as long as a wrong password
    const emailOk = sameEmail(email, body.email ?? body.username ?? "");
    const given = body.password ?? "";
    const passOk = st.hash && st.salt && env.SESSION_SECRET
      ? await checkPassword(st.hash, await hashPassword(env.SESSION_SECRET, st.salt, given))
      : await checkPassword(env.REINMAN_PASSWORD, given);
    if (!configured || !emailOk || !passOk) {
      await env.DB.prepare("INSERT INTO login_failures (ip) VALUES (?)").bind(ip).run();
      return json({ error: configured ? "Wrong email or password." : "Login is not set up on the server yet." }, 401);
    }
    const token = await makeSession(env.SESSION_SECRET!, st.epoch);
    return json({ ok: true }, 200, { "Set-Cookie": sessionCookie(token, 30 * 86400) });
  }
  if (url.pathname === "/api/logout" && req.method === "POST") {
    return json({ ok: true }, 200, { "Set-Cookie": sessionCookie("", 0) });
  }
  if (url.pathname === "/api/forgot" && req.method === "POST") {
    // Same answer whatever the email, so the form can't be used to discover the address.
    const generic = json({ ok: true, message: "If that email is the Rein Man login, a reset link is on its way." });
    if (await tooMany(env.DB, "auth_events", ip, 3, "-1 hour", "forgot")) return json({ error: "Too many requests. Try again in an hour." }, 429);
    await env.DB.prepare("INSERT INTO auth_events (kind, ip) VALUES ('forgot', ?)").bind(ip).run();
    const body = (await req.json().catch(() => ({}))) as { email?: string };
    const email = loginEmail(env);
    if (!email || !env.SESSION_SECRET || !sameEmail(email, body.email ?? "")) return generic;
    if (!env.RESEND_API_KEY) return json({ error: "Password-reset email isn't set up on the server yet (RESEND_API_KEY)." }, 503);
    const token = randomToken();
    const expires = new Date(Date.now() + RESET_MINUTES * 60_000).toISOString();
    await env.DB.prepare("INSERT INTO password_resets (token_hash, expires_at) VALUES (?, ?)").bind(await sha256(token), expires).run();
    const sent = await sendResetEmail(env, email.trim(), `${url.origin}/reset?token=${encodeURIComponent(token)}`);
    if (!sent) return json({ error: "The reset email couldn't be sent. Check the Resend setup." }, 502);
    return generic;
  }
  if (url.pathname === "/api/reset" && req.method === "POST") {
    if (await tooMany(env.DB, "login_failures", ip, 8, "-15 minutes")) return json({ error: "Too many attempts. Try again later." }, 429);
    const body = (await req.json().catch(() => ({}))) as { token?: string; password?: string };
    const password = body.password ?? "";
    if (password.length < MIN_PASSWORD) return json({ error: `Use at least ${MIN_PASSWORD} characters.` }, 400);
    if (!env.SESSION_SECRET) return json({ error: "Login is not set up on the server yet." }, 503);
    const th = await sha256(body.token ?? "");
    // single use: claim the token atomically
    const claimed = await env.DB.prepare(
      `UPDATE password_resets SET used_at = ${NOW_SQL} WHERE token_hash = ? AND used_at IS NULL AND expires_at > ? RETURNING token_hash`,
    ).bind(th, new Date().toISOString()).first();
    if (!claimed) {
      await env.DB.prepare("INSERT INTO login_failures (ip) VALUES (?)").bind(ip).run();
      return json({ error: "This reset link is invalid, used or expired. Request a new one." }, 400);
    }
    const st = await authState(env.DB);
    const salt = randomToken(16);
    const hash = await hashPassword(env.SESSION_SECRET, salt, password);
    const epoch = st.epoch + 1;
    const up = "INSERT INTO auth_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value";
    await env.DB.batch([
      env.DB.prepare(up).bind("password_salt", salt),
      env.DB.prepare(up).bind("password_hash", hash),
      env.DB.prepare(up).bind("session_epoch", String(epoch)),
      env.DB.prepare("UPDATE password_resets SET used_at = COALESCE(used_at, " + NOW_SQL + ")"),
    ]);
    // signed out everywhere else; signed in here with the new epoch
    const token = await makeSession(env.SESSION_SECRET, epoch);
    return json({ ok: true }, 200, { "Set-Cookie": sessionCookie(token, 30 * 86400) });
  }
  return null;
}

async function isAuthed(req: Request, env: Env): Promise<boolean> {
  if (env.AUTH_DISABLED === "true") return true;
  const token = readCookie(req, COOKIE);
  if (!token || !env.SESSION_SECRET) return false;
  return verifySession(env.SESSION_SECRET, token, (await authState(env.DB)).epoch);
}

const PUBLIC_ASSETS = /^\/(login|reset|assets\/|logo\.svg$|favicon\.svg$|manifest\.webmanifest$)/;

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    try {
      const authRes = await handleAuth(req, env, url);
      if (authRes) return withSecurity(authRes);
      const authed = await isAuthed(req, env);
      if (url.pathname === "/api/session") return withSecurity(json({ authenticated: authed, authRequired: env.AUTH_DISABLED !== "true" }));
      if (url.pathname.startsWith("/api/")) {
        if (!authed) return withSecurity(json({ error: "unauthorized" }, 401));
        if (req.method === "POST" && req.headers.get("Content-Type")?.includes("application/json") !== true)
          return withSecurity(json({ error: "expected JSON" }, 415));
        for (const [method, re, h] of routes) {
          const m = url.pathname.match(re);
          if (m && method === req.method) return withSecurity(await h(req, env, url, m));
        }
        return withSecurity(json({ error: "not found" }, 404));
      }
      // Static app: only the login shell and its assets are reachable without a session.
      if (!authed && !PUBLIC_ASSETS.test(url.pathname)) {
        if (url.pathname === "/" || !url.pathname.includes(".")) {
          return withSecurity(await env.ASSETS.fetch(new Request(new URL("/", url), { headers: req.headers })));
        }
        return withSecurity(new Response("Unauthorized", { status: 401 }));
      }
      return withSecurity(await env.ASSETS.fetch(req));
    } catch (e) {
      console.error(e);
      return withSecurity(json({ error: "internal error" }, 500));
    }
  },

  async scheduled(controller: ScheduledController, env: Env): Promise<void> {
    const locked = await lockDue(env.DB);
    const when = new Date(controller.scheduledTime);
    const due = scheduledJobsDue(when);
    const dispatch = {
      monitor: await dispatchMonitor(env, when),
      documents: await dispatchDocuments(env),
      daily: due.daily ? await dispatchOnce(env, "daily_dispatch", "reinman-daily.yml", "-20 hours") : "not due",
      learn: due.learn ? await dispatchOnce(env, "learn_dispatch", "reinman-learn.yml", "-6 days") : "not due",
    };
    await env.DB.prepare("INSERT INTO job_runs (job, finished_at, status, details) VALUES ('worker_cron', strftime('%Y-%m-%dT%H:%M:%fZ','now'), 'ok', ?)")
      .bind(JSON.stringify({ locked, dispatch }))
      .run();
    await env.DB.prepare("DELETE FROM job_runs WHERE job IN ('worker_cron','reddit_lookup') AND started_at < strftime('%Y-%m-%dT%H:%M:%fZ','now','-3 days')").run();
    await env.DB.prepare("DELETE FROM login_failures WHERE at < strftime('%Y-%m-%dT%H:%M:%fZ','now','-1 day')").run();
  },
} satisfies ExportedHandler<Env>;


