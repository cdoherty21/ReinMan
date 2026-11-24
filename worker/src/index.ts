// Rein Man Worker: private API + static app + scheduled locking.
import { COOKIE, checkPassword, makeSession, readCookie, sessionCookie, verifySession } from "./auth";

export interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  REINMAN_USERNAME?: string;
  REINMAN_PASSWORD?: string;
  /** "true" turns the password login off: anyone with the link can use the app. */
  AUTH_DISABLED?: string;
  SESSION_SECRET?: string;
  GITHUB_TOKEN?: string;
  GITHUB_REPO?: string;
  GITHUB_WORKFLOW?: string;
  GITHUB_REF?: string;
  DISPATCH_EVERY_MIN?: string;
}

const MAX_DOC_CHARS = 1_000_000;
const CHUNK_CHARS = 40_000;
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

async function dispatchMonitor(env: Env, when: Date): Promise<string> {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return "dispatch not configured";
  const every = Number(env.DISPATCH_EVERY_MIN || "15");
  const h = when.getUTCHours();
  const racing = h >= 14 || h < 6; // ~10am–2am ET covers U.S. day and night cards
  if (!racing || when.getUTCMinutes() % every !== 0) return "outside dispatch window";
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`;
  const r = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "rein-man-worker",
    },
    body: JSON.stringify({ ref: env.GITHUB_REF || "main" }),
  });
  return `dispatch ${r.status}`;
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
           WHERE n.race_id = ? AND d.research_only = 0 ORDER BY n.id`,
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
                (SELECT COALESCE(SUM(wg.payout),0) FROM wagers w JOIN wager_grades wg ON wg.wager_id = w.wager_id WHERE w.prediction_id = p.prediction_id) AS returned
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
          `SELECT id, uploaded_at, filename, mime, size_bytes, chars, race_date, track_id, research_only, status,
                  processed_at, summary, error FROM documents ORDER BY id DESC LIMIT 200`,
        )
      ).map((d) => ({ ...d, summary: parseJSON((d as { summary: unknown }).summary) }));
      return json({ rows });
    },
  ],
  [
    "GET",
    /^\/api\/documents\/(\d+)$/,
    async (_req, env, _u, m) => {
      const doc = await first(env.DB, "SELECT * FROM documents WHERE id = ?", Number(m[1]));
      if (!doc) return json({ error: "not found" }, 404);
      const notes = (await all(env.DB, "SELECT * FROM document_notes WHERE document_id = ? ORDER BY id", Number(m[1]))).map(
        (n) => ({ ...n, data: parseJSON((n as { data: unknown }).data) }),
      );
      return json({ doc: { ...doc, summary: parseJSON((doc as { summary: unknown }).summary) }, notes });
    },
  ],
  [
    "POST",
    /^\/api\/documents$/,
    async (req, env) => {
      const b = (await req.json()) as {
        filename?: string; mime?: string; size_bytes?: number; text?: string;
        race_date?: string; track_id?: string; research_only?: boolean;
      };
      const text = typeof b.text === "string" ? b.text : "";
      const filename = String(b.filename || "").slice(0, 200);
      if (!filename || text.trim().length < 20)
        return json({ error: "No readable text in this document. Scanned images aren't supported yet." }, 400);
      if (text.length > MAX_DOC_CHARS) return json({ error: `Document too large (over ${MAX_DOC_CHARS.toLocaleString()} characters).` }, 413);
      const raceDate = /^\d{4}-\d{2}-\d{2}$/.test(b.race_date ?? "") ? b.race_date : null;
      const track = /^[A-Za-z0-9_]{1,8}$/.test(b.track_id ?? "") ? b.track_id!.toUpperCase() : null;
      const ins = await env.DB.prepare(
        "INSERT INTO documents (filename, mime, size_bytes, chars, race_date, track_id, research_only) VALUES (?,?,?,?,?,?,?)",
      )
        .bind(filename, String(b.mime || "").slice(0, 100) || null, Number(b.size_bytes) || null, text.length, raceDate, track, b.research_only ? 1 : 0)
        .run();
      const id = ins.meta.last_row_id;
      const stmts = [];
      for (let i = 0, seq = 0; i < text.length; i += CHUNK_CHARS, seq++) {
        stmts.push(env.DB.prepare("INSERT INTO document_chunks (document_id, seq, text) VALUES (?,?,?)").bind(id, seq, text.slice(i, i + CHUNK_CHARS)));
      }
      for (let i = 0; i < stmts.length; i += 50) await env.DB.batch(stmts.slice(i, i + 50));
      return json({ ok: true, id, chunks: stmts.length }, 201);
    },
  ],
];

async function handleAuth(req: Request, env: Env, url: URL): Promise<Response | null> {
  if (url.pathname === "/api/login" && req.method === "POST") {
    const ip = req.headers.get("CF-Connecting-IP") || "unknown";
    const recent = await first<{ n: number }>(
      env.DB,
      "SELECT COUNT(*) AS n FROM login_failures WHERE ip = ? AND at > strftime('%Y-%m-%dT%H:%M:%fZ','now','-15 minutes')",
      ip,
    );
    if ((recent?.n ?? 0) >= 8) return json({ error: "Too many attempts. Try again later." }, 429);
    const body = (await req.json().catch(() => ({}))) as { username?: string; password?: string };
    const configured = Boolean(env.REINMAN_USERNAME && env.REINMAN_PASSWORD && env.SESSION_SECRET);
    // check both fields every time so a wrong username takes as long as a wrong password
    const userOk = await checkPassword(env.REINMAN_USERNAME?.trim().toLowerCase(), (body.username ?? "").trim().toLowerCase());
    const passOk = await checkPassword(env.REINMAN_PASSWORD, body.password ?? "");
    if (!configured || !userOk || !passOk) {
      await env.DB.prepare("INSERT INTO login_failures (ip) VALUES (?)").bind(ip).run();
      return json({ error: configured ? "Wrong username or password." : "Login is not set up on the server yet." }, 401);
    }
    const token = await makeSession(env.SESSION_SECRET!);
    return json({ ok: true }, 200, { "Set-Cookie": sessionCookie(token, 30 * 86400) });
  }
  if (url.pathname === "/api/logout" && req.method === "POST") {
    return json({ ok: true }, 200, { "Set-Cookie": sessionCookie("", 0) });
  }
  return null;
}

async function isAuthed(req: Request, env: Env): Promise<boolean> {
  if (env.AUTH_DISABLED === "true") return true;
  return verifySession(env.SESSION_SECRET ?? "", readCookie(req, COOKIE));
}

const PUBLIC_ASSETS = /^\/(login|assets\/|logo\.svg$|favicon\.svg$|manifest\.webmanifest$)/;

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
    const dispatch = await dispatchMonitor(env, new Date(controller.scheduledTime));
    await env.DB.prepare("INSERT INTO job_runs (job, finished_at, status, details) VALUES ('worker_cron', strftime('%Y-%m-%dT%H:%M:%fZ','now'), 'ok', ?)")
      .bind(JSON.stringify({ locked, dispatch }))
      .run();
    await env.DB.prepare("DELETE FROM job_runs WHERE job = 'worker_cron' AND started_at < strftime('%Y-%m-%dT%H:%M:%fZ','now','-3 days')").run();
    await env.DB.prepare("DELETE FROM login_failures WHERE at < strftime('%Y-%m-%dT%H:%M:%fZ','now','-1 day')").run();
  },
} satisfies ExportedHandler<Env>;
