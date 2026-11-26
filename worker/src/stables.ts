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
const parseList = (value:unknown):string[] => {
  try { const list=typeof value==="string"?JSON.parse(value):value; return Array.isArray(list)?list.filter((v):v is string=>typeof v==="string"):[]; }
  catch { return []; }
};
const parseForm = (value:unknown):number[] => {
  try {
    const list = typeof value === "string" ? JSON.parse(value) : value;
    return Array.isArray(list) ? list.filter((n):n is number=>typeof n==="number" && Number.isFinite(n)) : [];
  } catch { return []; }
};
const upcoming = `r.status = 'scheduled' AND r.is_cancelled = 0 AND ru.scratched = 0
  AND r.post_time > strftime('%Y-%m-%dT%H:%M:%fZ','now')`;
const summary = `SELECT ru.horse_id, ru.horse_name, ru.trainer_name, ru.jockey_name, ru.sire, ru.dam, ru.dam_sire,
  ru.extra, r.race_date, r.track_id, e.image, e.biography,e.retrieved_at,
  (SELECT json_group_array(finish_position) FROM (SELECT sf.finish_position FROM runners sr
    JOIN races rr USING(race_id) JOIN result_finishers sf ON sf.race_id=sr.race_id AND sf.program_number=sr.program_number
    WHERE sr.horse_id=ru.horse_id AND sr.scratched=0 AND sf.finish_position>0 ORDER BY rr.race_date DESC LIMIT 3)) AS recent_form,
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
    ru.jockey_name, ru.trainer_name, ru.post_position, ru.medication, ru.equipment, ru.live_odds, ru.sire, ru.dam, ru.dam_sire,
    ru.scratched, ru.extra, COALESCE(f.finish_position,json_extract(c.data,'$.finish')) AS finish_position, c.data AS chart_data
    FROM runners ru JOIN races r USING(race_id)
    LEFT JOIN result_finishers f ON f.race_id=ru.race_id AND f.program_number=ru.program_number
    LEFT JOIN chart_lines c ON ru.horse_id LIKE 'name:%' AND c.horse_key=substr(ru.horse_id,6) AND c.race_date=r.race_date AND c.track_id=r.track_id AND c.race_number=r.race_number AND c.horse_name=ru.horse_name COLLATE NOCASE
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
    r.distance_yards, r.track_condition, r.weather_desc, r.temp_f, r.race_class, r.source, ru.program_number, ru.live_odds, ru.ml_odds, ru.updated_at AS odds_recorded_at, ru.post_position, ru.jockey_name, ru.trainer_name,
    pr.win_prob, pr.fair_odds, p.confidence, pr.factors, pr.odds_source
    FROM runners ru JOIN races r USING(race_id)
    LEFT JOIN predictions p ON p.prediction_id=(SELECT MAX(prediction_id) FROM predictions WHERE race_id=r.race_id AND created_at < r.post_time)
    LEFT JOIN prediction_runners pr ON pr.prediction_id=p.prediction_id AND pr.program_number=ru.program_number
    WHERE ru.horse_id=? AND ${upcoming} ORDER BY r.post_time LIMIT 1`, [id]);
  let opponents: Row[] = [];
  if (next) {
    opponents = await rows(db, "SELECT horse_name FROM runners WHERE race_id=? AND horse_id<>? AND scratched=0 ORDER BY post_position", [next.race_id, id]);
    const price = next.live_odds;
    next.factors = parseList(next.factors);
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
  const identity:Row = {}, owners = new Set<string>(), trainers = new Set<string>(), jockeys = new Set<string>();
  const provenance:Record<string,unknown> = {};
  for(const h of history){
    const extra = parse(h.extra), known = extra.horse_identity as Row | undefined;
    if(known && typeof known==="object"){
      for(const [key,value] of Object.entries(known)) if(value && !identity[key]) {
        identity[key]=value; provenance[key]={source:known.source || extra.research_source || "stored race card",date:h.race_date};
      }
      if(typeof known.owner==="string") owners.add(known.owner);
    }
    if(typeof h.trainer_name==="string") trainers.add(h.trainer_name);
    if(typeof h.jockey_name==="string") jockeys.add(h.jockey_name);
  }
  const form = history.map(h=>parse(h.extra).formfav).find(Boolean) ?? {};
  for(const key of ["sire","dam","dam_sire"]) if(!horse[key]) horse[key]=history.find(h=>h[key])?.[key];
  const figures=completed.map(h=>Number(h.speed_fig || parse(h.chart_data).speed_fig)).filter(n=>Number.isFinite(n)&&n>0);
  const evidence=parseList(latestEvidence?.factors);
  const insights = { strengths:evidence.filter(line=>line.startsWith("+ ")).slice(0,4),
    weaknesses:evidence.filter(line=>line.startsWith("− ")).slice(0,4),
    behavior:evidence.filter(line=>line.includes("Racing behavior |")||line.includes("Trip history |")),
    preparation:evidence.filter(line=>line.includes("Preparation |")||line.includes("travel")||line.includes("miles from last race")),
    speed:evidence.filter(line=>line.includes("speed figure")||line.includes("Beyer figures")||line.includes("pace figures")),
    fitness:evidence.filter(line=>line.includes("Race fit |")).slice(0,10)
  };
  return { ...horse, horse_name:horse.horse_name, horse_id:horse.horse_id, recent_form:parseForm(horse.recent_form), form, identity, provenance, connections:{owners:[...owners],trainers:[...trainers],jockeys:[...jockeys]}, insights, speedFigures:figures, workouts: [...workouts.values()].sort((a,b)=>String(b.date).localeCompare(String(a.date))), pastPerformances, evidence, image: parse(horse.image), biography: parse(horse.biography), record, history: history.map(h => ({ ...h, details: {...parse(h.extra),chart:parse(h.chart_data)}, extra: undefined, chart_data: undefined } as Row)), next, opponents, news, preferences, extra: undefined };
}

// Archive-only keys are provisional. Never attach records to a permanent runner ID
// merely because the name matches. Winner-only archives do not supply losing starts.
async function archiveProfile(db: D1Database, key: string) {
  const chart = await rows(db, "SELECT * FROM chart_lines WHERE horse_key=? ORDER BY race_date DESC LIMIT 500", [key]);
  const winners = await rows(db, "SELECT * FROM historical_winners WHERE horse_key=? ORDER BY race_date DESC LIMIT 500", [key]);
  if (!chart.length && !winners.length) return null;
  const history = chart.map(c => {
    const d = parse(c.data);
    return {race_id:"archive:"+String(c.document_id)+":"+String(c.race_date)+":"+String(c.race_number),race_date:c.race_date,
      track_id:c.track_id,race_number:c.race_number,surface:d.surface,distance_yards:d.distance_yards,
      finish_position:d.finish,jockey_name:d.jockey,details:d,source:"Imported chart"};
  });
  const winnerRecords = winners.map(w=>({date:w.race_date,track:w.track_id,race_number:w.race_number,source:"Winner archive",...parse(w.data)}));
  const record = assessment(history.filter(h=>Number(h.finish_position)>0).map(h=>Number(h.finish_position)));
  const favorite = await one(db,"SELECT 1 AS favorite FROM stable_favorites WHERE horse_id=?",["archive:"+key]);
  return {horse_id:"archive:"+key,horse_name:(chart[0]??winners[0]).horse_name, archive:true, identity:{},
    favorite:favorite?.favorite??0,record,history,winnerRecords,news:[],next:null,opponents:[],preferences:{},
    evidence:["Historical name key requires corroborating identity before linking to an active horse.", "Winner-only observations are excluded from career rates."]};
}
async function archiveDirectory(db: D1Database, url: URL) {
  const cursor = url.searchParams.get("after") ?? "";
  const q = (url.searchParams.get("q")??"").trim().slice(0,100);
  const term = "%"+q.replace(/[\\%_]/g,"\\$&")+"%";
  const records = await rows(db, `WITH archived AS (
    SELECT horse_key, horse_name, race_date, track_id, data FROM chart_lines WHERE horse_key>?
    UNION ALL SELECT horse_key, horse_name, race_date, track_id, data FROM historical_winners WHERE horse_key>?
  ) SELECT 'archive:'||horse_key AS horse_id,horse_name, MAX(race_date) AS race_date, track_id,
    EXISTS(SELECT 1 FROM stable_favorites f WHERE f.horse_id='archive:'||horse_key) AS favorite
    FROM archived WHERE (horse_name LIKE ? ESCAPE '\\' OR data LIKE ? ESCAPE '\\' OR track_id LIKE ? ESCAPE '\\')
    AND (?=0 OR EXISTS(SELECT 1 FROM stable_favorites f WHERE f.horse_id='archive:'||horse_key))
    GROUP BY horse_key ORDER BY horse_key LIMIT 25`,
    [cursor.replace(/^archive:/,""),cursor.replace(/^archive:/,""),term,term,term,url.searchParams.get("favorites")==="1"?1:0]);
  return {horses:records.slice(0,24).map(h=>({...h,archive:true,identity:{}})),after:records.length===25?records[23].horse_id:null};
}

export async function stables(req: Request, db: D1Database, url: URL): Promise<Response> {
  const path = url.pathname;
  if (path === "/api/stables/counts" && req.method === "GET") {
    return json(await one(db, `SELECT
      (SELECT COUNT(DISTINCT horse_id) FROM runners) AS indexed,
      (SELECT COUNT(*) FROM (SELECT horse_key FROM chart_lines UNION SELECT horse_key FROM historical_winners)) AS archive_keys,
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
      UNION ALL
      SELECT 'value:'||ru.race_id||':'||ru.horse_id,ru.horse_id,ru.horse_name,
        'Potential value opportunity',p.created_at,ru.race_id
      FROM stable_favorites f JOIN runners ru ON ru.horse_id=f.horse_id JOIN races r USING(race_id)
      JOIN predictions p ON p.prediction_id=(SELECT MAX(prediction_id) FROM predictions WHERE race_id=r.race_id)
      JOIN prediction_runners pr ON pr.prediction_id=p.prediction_id AND pr.program_number=ru.program_number
      WHERE ${upcoming} AND ru.live_odds IS NOT NULL AND pr.win_prob*(1+ru.live_odds)>1.1
      UNION ALL
      SELECT 'assessment:'||ru.race_id||':'||ru.horse_id||':'||p.prediction_id,ru.horse_id,ru.horse_name,
        'Race assessment changed',p.created_at,ru.race_id
      FROM stable_favorites f JOIN runners ru ON ru.horse_id=f.horse_id JOIN races r USING(race_id)
      JOIN predictions p ON p.prediction_id=(SELECT MAX(prediction_id) FROM predictions WHERE race_id=r.race_id)
      JOIN prediction_runners pr ON pr.prediction_id=p.prediction_id AND pr.program_number=ru.program_number
      JOIN predictions prior ON prior.prediction_id=(SELECT MAX(prediction_id) FROM predictions WHERE race_id=r.race_id AND prediction_id<p.prediction_id)
      JOIN prediction_runners old ON old.prediction_id=prior.prediction_id AND old.program_number=ru.program_number
      WHERE ${upcoming} AND (ABS(pr.win_prob-old.win_prob)>=0.10 OR p.confidence<>prior.confidence)
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
    if(candidates.length<6){
      const excluded=candidates.map(c=>c.horse_id);
      const placeholders=excluded.map(()=>"?").join(",");
      const fallback=await rows(db,`SELECT ru.horse_id, MAX(r.race_date) AS last_date
        FROM races r JOIN runners ru USING(race_id) JOIN result_finishers f ON f.race_id=ru.race_id AND f.program_number=ru.program_number
        WHERE r.race_date>=date('now','-120 days') AND f.finish_position>0 AND ru.scratched=0
        ${excluded.length?"AND ru.horse_id NOT IN ("+placeholders+")":""}
        GROUP BY ru.horse_id ORDER BY COUNT(*) DESC,last_date DESC LIMIT ?`,[...excluded,6-candidates.length]);
      candidates.push(...fallback);
    }
    const profiles = await Promise.all(candidates.map(c => profile(db, String(c.horse_id))));
    return json(profiles.filter(p => p !== null).sort((a,b) => Number(b.record.improving)-Number(a.record.improving) || Number(!!b.next)-Number(!!a.next) || b.record.starts-a.record.starts).slice(0,5).map(({history,workouts,pastPerformances,news,preferences,evidence,...p})=>p));
  }
  if (path === "/api/stables" && req.method === "GET") {
    if (url.searchParams.get("scope")==="archive") return json(await archiveDirectory(db,url));
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
    const performance=url.searchParams.get("performance");
    if(performance==="winner") filters.push("EXISTS(SELECT 1 FROM runners sr JOIN result_finishers sf ON sf.race_id=sr.race_id AND sf.program_number=sr.program_number WHERE sr.horse_id=ru.horse_id AND sf.finish_position=1)");
    if(performance==="recent-itm") filters.push("EXISTS(SELECT 1 FROM runners sr JOIN races rr USING(race_id) JOIN result_finishers sf ON sf.race_id=sr.race_id AND sf.program_number=sr.program_number WHERE sr.horse_id=ru.horse_id AND sf.finish_position BETWEEN 1 AND 3 AND rr.race_date>=date('now','-30 days'))");
    if (url.searchParams.get("favorites")==="1") filters.push("EXISTS(SELECT 1 FROM stable_favorites f WHERE f.horse_id=ru.horse_id)");
    if (url.searchParams.get("upcoming")==="1") filters.push(upcoming);
    const horses = await rows(db, summary + " WHERE " + filters.join(" AND ") + " GROUP BY ru.horse_id HAVING MAX(r.race_date) IS NOT NULL ORDER BY ru.horse_id LIMIT 25", args);
    return json({ horses: horses.slice(0,24).map(h => ({...h, image: parse(h.image), biography: parse(h.biography), form: parse(h.extra).formfav ?? {}, recent_form: parseForm(h.recent_form), identity: parse(h.extra).horse_identity ?? {}, extra: undefined })), after: horses.length===25 ? horses[23].horse_id : null });
  }
  if(path==="/api/stables/compare" && req.method==="GET"){
    const ids=[...new Set(url.searchParams.getAll("id"))].slice(0,4);
    if(!ids.length) return json({horses:[],headToHead:[]});
    const horses=await Promise.all(ids.map(id=>id.startsWith("archive:")?archiveProfile(db,id.slice(8)):profile(db,id)));
    const common=new Map<string,Row[]>();
    for(const horse of horses){
      if(!horse||"archive" in horse)continue;
      for(const h of horse.history){
        if(!Number(h.finish_position)||h.scratched)continue;
        const key=String(h.race_id), entrants=common.get(key)??[];
        entrants.push({horse_name:horse.horse_name,finish:h.finish_position,date:h.race_date,track:h.track_id,race_id:key});
        common.set(key,entrants);
      }
    }
    return json({horses:horses.filter(Boolean),headToHead:[...common.values()].filter(group=>group.length>1)});
  }
  const match = path.match(/^\/api\/stables\/horse\/([^/]+)(\/favorite)?$/);
  if (match) {
    const id = decodeURIComponent(match[1]);
    if (match[2] && req.method === "POST") {
      const body = await req.json() as { favorite?: boolean };
      if (typeof body.favorite !== "boolean") return json({error:"Expected favorite boolean"},400);
      const exists = id.startsWith("archive:") ? await one(db,"SELECT horse_key FROM chart_lines WHERE horse_key=? UNION SELECT horse_key FROM historical_winners WHERE horse_key=? LIMIT 1",[id.slice(8),id.slice(8)]) : await one(db,"SELECT horse_id FROM runners WHERE horse_id=? LIMIT 1",[id]);
      if (!exists) return json({error:"Horse not found"},404);
      await db.prepare(body.favorite ? "INSERT OR IGNORE INTO stable_favorites(horse_id) VALUES (?)" : "DELETE FROM stable_favorites WHERE horse_id=?").bind(id).run();
      return json({favorite:body.favorite});
    }
    if (!match[2] && req.method === "GET") {
      const data = id.startsWith("archive:") ? await archiveProfile(db,id.slice(8)) : await profile(db,id);
      return data ? json(data) : json({error:"Horse not found"},404);
    }
  }
  return json({error:"Not found"},404);
}
