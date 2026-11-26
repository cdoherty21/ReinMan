/** The Stables reads durable race records directly, so new imports appear without a second copy. */
type Row = Record<string, unknown>;
const rows = async (db: D1Database, sql: string, args: unknown[] = []) =>
  (await db.prepare(sql).bind(...args).all<Row>()).results ?? [];
const one = async (db: D1Database, sql: string, args: unknown[] = []) =>
  (await rows(db, sql, args))[0] ?? null;
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
});
const parse = (value: unknown): Row => {
  try { return typeof value === "string" ? JSON.parse(value) ?? {} : {}; } catch { return {}; }
};
const upcoming = `r.status = 'scheduled' AND r.is_cancelled = 0 AND ru.scratched = 0
  AND r.post_time > strftime('%Y-%m-%dT%H:%M:%fZ','now')`;
const summary = `SELECT ru.horse_id, ru.horse_name, ru.trainer_name, ru.jockey_name, ru.sire, ru.dam, ru.dam_sire,
  ru.extra, r.race_date, r.track_id, e.image, e.biography,
  EXISTS(SELECT 1 FROM stable_favorites f WHERE f.horse_id=ru.horse_id) AS favorite
  FROM runners ru JOIN races r USING(race_id) LEFT JOIN stable_enrichment e USING(horse_id)`;

export function assessment(finishes: number[]) {
  const starts = finishes.length, wins = finishes.filter(n => n === 1).length;
  const places = finishes.filter(n => n === 2).length, shows = finishes.filter(n => n === 3).length;
  const recent = finishes.slice(0, 3), previous = finishes.slice(3, 6);
  const mean = (v: number[]) => v.reduce((a, b) => a + b, 0) / v.length;
  const improving = recent.length === 3 && previous.length === 3 && mean(recent) < mean(previous);
  return {
    starts, wins, places, shows, recent, improving,
    classification: improving ? "Rising Star" : starts >= 5 && (wins + places + shows) / starts >= .6 ? "Proven Performer" : "Watching Closely",
    confidence: starts >= 10 ? "High" : starts >= 3 ? "Moderate" : "Limited",
    reason: starts ? `${wins + places + shows} top-three finishes from ${starts} recorded starts.${improving ? " Recent finishing positions have improved compared with the preceding three starts." : ""}` : "Awaiting verified race results.",
    weakness: starts < 5 ? "Too few recorded finishes to establish reliable preferences." : "Finishing positions alone cannot establish pace, class or surface suitability.",
  };
}

async function profile(db: D1Database, id: string) {
  const horse = await one(db, summary + " WHERE ru.horse_id=? ORDER BY r.race_date DESC, r.race_number DESC LIMIT 1", [id]);
  if (!horse) return null;
  const history = await rows(db, `SELECT r.race_id, r.race_date, r.track_id, r.race_number, r.surface,
    r.distance_yards, r.track_condition, r.race_class, r.purse, r.weather_desc, r.temp_f,
    ru.jockey_name, ru.trainer_name, ru.post_position, ru.medication, ru.equipment, ru.live_odds,
    ru.scratched, ru.extra, f.finish_position
    FROM runners ru JOIN races r USING(race_id)
    LEFT JOIN result_finishers f ON f.race_id=ru.race_id AND f.program_number=ru.program_number
    WHERE ru.horse_id=? ORDER BY r.race_date DESC, r.race_number DESC LIMIT 500`, [id]);
  const workouts = new Map<string, Row>(), past = new Map<string, Row>();
  for (const h of history) {
    const extra = parse(h.extra);
    for (const work of Array.isArray(extra.workouts) ? extra.workouts as Row[] : []) {
      if (work.date) workouts.set([work.date,work.track,work.distance_yards,work.time].join("|"),work);
    }
    for (const line of Array.isArray(extra.past_performances) ? extra.past_performances as Row[] : []) {
      if (line.date && line.track && Number(line.finish)>0 && String(line.date)<String(h.race_date)) {
        past.set([line.date,line.track].join("|"),line);
      }
    }
  }
  for (const h of history) {
    if (!h.scratched && Number(h.finish_position)>0) past.set([h.race_date,h.track_id].join("|"),{...h,date:h.race_date,track:h.track_id,finish:h.finish_position});
  }
  const completed = [...past.values()].sort((a,b)=>String(b.date).localeCompare(String(a.date)));
  const record = assessment(completed.map(h=>Number(h.finish)));
  const pastPerformances = completed.filter(h=>!h.race_id);
  const latestEvidence = await one(db, `SELECT pr.factors FROM runners ru JOIN races r USING(race_id)
    JOIN predictions p ON p.prediction_id=(SELECT MAX(prediction_id) FROM predictions WHERE race_id=r.race_id)
    JOIN prediction_runners pr ON pr.prediction_id=p.prediction_id AND pr.program_number=ru.program_number
    WHERE ru.horse_id=? AND pr.factors IS NOT NULL ORDER BY r.race_date DESC LIMIT 1`, [id]);
  const next = await one(db, `SELECT r.race_id, r.race_date, r.track_id, r.race_number, r.post_time, r.surface,
    r.distance_yards, ru.program_number, ru.live_odds, ru.ml_odds,
    pr.win_prob, pr.fair_odds, p.confidence, pr.factors, pr.odds_source
    FROM runners ru JOIN races r USING(race_id)
    LEFT JOIN predictions p ON p.prediction_id=(SELECT MAX(prediction_id) FROM predictions WHERE race_id=r.race_id AND created_at < r.post_time)
    LEFT JOIN prediction_runners pr ON pr.prediction_id=p.prediction_id AND pr.program_number=ru.program_number
    WHERE ru.horse_id=? AND ${upcoming} ORDER BY r.post_time LIMIT 1`, [id]);
  let opponents: Row[] = [];
  if (next) {
    opponents = await rows(db, "SELECT horse_name FROM runners WHERE race_id=? AND horse_id<>? AND scratched=0 ORDER BY post_position", [next.race_id, id]);
    const price = next.live_odds;
    next.verdict = next.win_prob == null ? "Insufficient Data" : price == null ? "Price Dependent" : Number(next.win_prob)*(1+Number(price))>1.1 ? "Strong Interest" : Number(next.win_prob)*(1+Number(price))<1 ? "Avoid" : "Neutral";
  }
  const news = await rows(db, `SELECT DISTINCT n.title,n.source,n.url,n.published_at,n.kind,n.sentence
    FROM runners ru JOIN news_events n USING(race_id,program_number)
    WHERE ru.horse_id=? AND n.role='horse' ORDER BY n.published_at DESC LIMIT 20`, [id]);
  const preferences: Record<string, { starts: number; itm: number }> = {};
  for (const h of completed) {
    for (const [key, value] of Object.entries({ Surface: h.surface, Distance: h.distance_yards, Track: h.track_id || h.track, Condition: h.track_condition || h.condition, Jockey: h.jockey_name || h.jockey, Trainer: h.trainer_name || h.trainer })) {
      if (!value) continue;
      const label = key + " · " + value;
      const sample = preferences[label] ??= { starts: 0, itm: 0 };
      sample.starts++; if (Number(h.finish)<=3) sample.itm++;
    }
  }
  const form = parse(horse.extra).formfav ?? {};
  return { ...horse, form, workouts: [...workouts.values()].sort((a,b)=>String(b.date).localeCompare(String(a.date))), pastPerformances, evidence: latestEvidence?.factors ? JSON.parse(String(latestEvidence.factors)) : [], image: parse(horse.image), biography: parse(horse.biography), identity: parse(horse.extra).horse_identity ?? {}, record, history: history.map(h => ({ ...h, details: parse(h.extra), extra: undefined })), next, opponents, news, preferences, extra: undefined };
}

export async function stables(req: Request, db: D1Database, url: URL): Promise<Response> {
  const path = url.pathname;
  if (path === "/api/stables/counts" && req.method === "GET") {
    return json(await one(db, `SELECT
      (SELECT COUNT(DISTINCT horse_id) FROM runners) AS indexed,
      COUNT(CASE WHEN biography IS NOT NULL THEN 1 END) AS enriched,
      COUNT(CASE WHEN image IS NOT NULL THEN 1 END) AS photographs FROM stable_enrichment`));
  }
  if (path === "/api/stables/notifications" && req.method === "GET") {
    return json(await rows(db, `WITH events AS (
      SELECT 'entry:'||ru.race_id||':'||ru.horse_id||':'||ru.scratched AS event_id, ru.horse_id, ru.horse_name,
        CASE WHEN ru.scratched=1 THEN 'Scratched' ELSE 'New entry' END AS title,
        r.race_date AS date, r.race_id
      FROM stable_favorites f JOIN runners ru ON ru.horse_id=f.horse_id JOIN races r USING(race_id)
      WHERE r.race_date>=date('now','-2 days') AND r.is_cancelled=0 AND r.status='scheduled'
      UNION ALL
      SELECT 'news:'||ru.horse_id||':'||n.url, ru.horse_id,ru.horse_name,n.title,n.published_at,ru.race_id
      FROM stable_favorites f JOIN runners ru ON ru.horse_id=f.horse_id JOIN news_events n USING(race_id,program_number)
      WHERE n.role='horse' AND n.published_at>=date('now','-14 days')
    ) SELECT DISTINCT events.* FROM events LEFT JOIN stable_notification_reads USING(event_id)
      WHERE stable_notification_reads.event_id IS NULL ORDER BY date DESC LIMIT 30`));
  }
  if (path === "/api/stables/notifications/read" && req.method === "POST") {
    const body = await req.json() as { eventId?: string };
    if (typeof body.eventId !== "string" || body.eventId.length>1000) return json({error:"Invalid event"},400);
    await db.prepare("INSERT OR IGNORE INTO stable_notification_reads(event_id) VALUES (?)").bind(body.eventId).run();
    return json({ok:true});
  }
  if (path === "/api/stables/featured" && req.method === "GET") {
    const candidates = await rows(db, `SELECT ru.horse_id, MAX(p.confidence_score) AS score
      FROM races r JOIN runners ru USING(race_id)
      JOIN predictions p ON p.prediction_id=(SELECT MAX(prediction_id) FROM predictions WHERE race_id=r.race_id)
      JOIN prediction_runners pr ON pr.prediction_id=p.prediction_id AND pr.program_number=ru.program_number
      WHERE ${upcoming} AND (pr.rank<=2 OR pr.program_number=p.dark_horse OR pr.program_number=p.value_pick)
      GROUP BY ru.horse_id ORDER BY score DESC LIMIT 6`);
    const profiles = await Promise.all(candidates.map(c => profile(db, String(c.horse_id))));
    return json(profiles.filter(p => p !== null).sort((a,b) => Number(b.record.improving)-Number(a.record.improving) || b.record.starts-a.record.starts).slice(0,5).map(({history,workouts,pastPerformances,news,preferences,evidence,...p})=>p));
  }
  if (path === "/api/stables" && req.method === "GET") {
    const q = (url.searchParams.get("q") ?? "").trim().slice(0,100);
    const cursor = url.searchParams.get("after") ?? "";
    const term = "%" + q.replace(/[\\%_]/g, "\\$&") + "%";
    const filters = ["ru.horse_id>?"], args: unknown[] = [cursor];
    if (q) {
      filters.push(`(ru.horse_name LIKE ? ESCAPE '\\' OR ru.trainer_name LIKE ? ESCAPE '\\' OR ru.jockey_name LIKE ? ESCAPE '\\' OR ru.sire LIKE ? ESCAPE '\\' OR ru.dam LIKE ? ESCAPE '\\' OR ru.dam_sire LIKE ? ESCAPE '\\' OR r.track_id LIKE ? ESCAPE '\\' OR r.race_class LIKE ? ESCAPE '\\' OR ru.extra LIKE ? ESCAPE '\\')`);
      args.push(...Array(9).fill(term));
    }
    const surface = url.searchParams.get("surface");
    if (surface && ["D","T","S"].includes(surface)) { filters.push("r.surface=?"); args.push(surface); }
    const age = url.searchParams.get("age");
    if (age && /^\d{1,2}$/.test(age)) { filters.push("json_extract(CASE WHEN json_valid(ru.extra) THEN ru.extra ELSE '{}' END,'$.horse_identity.age_sex') LIKE ?"); args.push(age+"/%"); }
    const sex = url.searchParams.get("sex");
    if (sex && /^[GCFMHR]$/.test(sex)) { filters.push("json_extract(CASE WHEN json_valid(ru.extra) THEN ru.extra ELSE '{}' END,'$.horse_identity.age_sex') LIKE ?"); args.push("%/"+sex); }
    if (url.searchParams.get("favorites")==="1") filters.push("EXISTS(SELECT 1 FROM stable_favorites f WHERE f.horse_id=ru.horse_id)");
    if (url.searchParams.get("upcoming")==="1") filters.push(upcoming);
    const horses = await rows(db, summary + " WHERE " + filters.join(" AND ") + " GROUP BY ru.horse_id HAVING MAX(r.race_date) IS NOT NULL ORDER BY ru.horse_id LIMIT 25", args);
    return json({ horses: horses.slice(0,24).map(h => ({...h, image: parse(h.image), biography: parse(h.biography), form: parse(h.extra).formfav ?? {}, identity: parse(h.extra).horse_identity ?? {}, extra: undefined })), after: horses.length===25 ? horses[23].horse_id : null });
  }
  const match = path.match(/^\/api\/stables\/horse\/([^/]+)(\/favorite)?$/);
  if (match) {
    const id = decodeURIComponent(match[1]);
    if (match[2] && req.method === "POST") {
      const body = await req.json() as { favorite?: boolean };
      if (typeof body.favorite !== "boolean") return json({error:"Expected favorite boolean"},400);
      if (!await one(db,"SELECT horse_id FROM runners WHERE horse_id=? LIMIT 1",[id])) return json({error:"Horse not found"},404);
      await db.prepare(body.favorite ? "INSERT OR IGNORE INTO stable_favorites(horse_id) VALUES (?)" : "DELETE FROM stable_favorites WHERE horse_id=?").bind(id).run();
      return json({favorite:body.favorite});
    }
    if (!match[2] && req.method === "GET") {
      const data = await profile(db,id);
      return data ? json(data) : json({error:"Horse not found"},404);
    }
  }
  return json({error:"Not found"},404);
}
