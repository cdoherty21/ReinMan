import { Link } from "../main";
import { postTime, surfaceName, useApi } from "../lib";
import { Confidence, DatePicker, Decision, Empty, ErrorBox, Loading, RM, useDateParam } from "./common";

export type RaceRow = {
  race_id: string; track_id: string; track_name: string | null; race_number: number; post_time: string | null;
  surface: string | null; distance_desc: string | null; distance_yards: number | null; race_class: string | null;
  race_type: string | null; purse: number | null; track_condition: string | null; status: string; is_cancelled: number;
  prediction_id: number | null; top1: string | null; top2: string | null; top3: string | null; value_pick: string | null;
  dark_horse: string | null; confidence: string | null; decision: string | null; model_version: string | null;
  predicted_at: string | null; field_size: number; locked: number | null; winner: string | null;
  top1_finish: number | null; grade_status: string | null;
  top1_name?: string; top2_name?: string; top3_name?: string; value_pick_name?: string; dark_horse_name?: string;
};

export function distance(r: { distance_desc: string | null; distance_yards: number | null }) {
  if (r.distance_desc) return r.distance_desc;
  if (!r.distance_yards) return "—";
  const f = r.distance_yards / 220;
  return f >= 8 ? `${(r.distance_yards / 1760).toFixed(2).replace(/\.?0+$/, "")} mi` : `${f.toFixed(1).replace(/\.0$/, "")}f`;
}

function PickCell({ r }: { r: RaceRow }) {
  if (!r.top1) return <span className="muted">Awaiting analysis</span>;
  return (
    <span className="picks">
      <RM />
      <b>#{r.top1}</b> {r.top1_name}
      <span className="muted"> · #{r.top2} · #{r.top3}</span>
    </span>
  );
}

export function Today() {
  const [date, setDate] = useDateParam();
  const { data, error } = useApi<{ date: string; races: RaceRow[] }>(`/api/today?date=${date}`);
  const races = data?.races ?? [];
  const now = Date.now();
  const upcoming = races.filter((r) => r.status === "scheduled" && !r.is_cancelled && r.post_time && Date.parse(r.post_time) > now);
  const done = races.filter((r) => !upcoming.includes(r));
  const tracks = [...new Map(races.map((r) => [r.track_id, r.track_name || r.track_id])).entries()];
  const plays = upcoming.filter((r) => r.decision === "play").sort((a, b) => (a.post_time! < b.post_time! ? -1 : 1));
  const values = upcoming.filter((r) => r.value_pick && r.value_pick !== r.top1);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Today</h1>
          <p className="muted">Rein Man's Race Card for {new Date(`${date}T12:00:00`).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" })}</p>
        </div>
        <DatePicker date={date} onChange={setDate} />
      </div>
      {error && <ErrorBox error={error} />}
      {!data && !error && <Loading />}
      {data && races.length === 0 && (
        <Empty>
          No race cards stored for this date. Cards appear automatically once the data source publishes entries
          and the monitor job runs. If this persists, check the data source configuration (see SETUP.md).
        </Empty>
      )}
      {races.length > 0 && (
        <>
          <section className="tiles">
            <div className="tile"><span>Racetracks</span><b>{tracks.length}</b></div>
            <div className="tile"><span>Races</span><b>{races.length}</b></div>
            <div className="tile"><span>Still to run</span><b>{upcoming.length}</b></div>
            <div className="tile gold"><span>Rein Man is playing</span><b>{plays.length}</b></div>
          </section>
          <section className="chips" aria-label="Racetracks">
            {tracks.map(([id, name]) => (
              <Link key={id} to={`/cards?date=${date}&track=${id}`} className="chip">{name}</Link>
            ))}
          </section>

          <section className="card">
            <h2>Rein Man's Best Bets</h2>
            {plays.length === 0 ? (
              <p className="muted">No upcoming race clears Rein Man's value threshold right now. He's happy to wait.</p>
            ) : (
              <ul className="bets">
                {plays.slice(0, 8).map((r) => (
                  <li key={r.race_id}>
                    <Link to={`/race/${r.race_id}`}>
                      <span className="when">{postTime(r.post_time)}</span>
                      <span className="where">{r.track_name || r.track_id} R{r.race_number}</span>
                      <PickCell r={r} />
                      <Confidence level={r.confidence} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {values.length > 0 && (
            <section className="card">
              <h2>Value Opportunities</h2>
              <ul className="bets">
                {values.slice(0, 8).map((r) => (
                  <li key={r.race_id}>
                    <Link to={`/race/${r.race_id}`}>
                      <span className="when">{postTime(r.post_time)}</span>
                      <span className="where">{r.track_name || r.track_id} R{r.race_number}</span>
                      <span><span className="tag value">Value</span> #{r.value_pick} {r.value_pick_name}</span>
                      {r.dark_horse && <span><span className="tag dark">Dark horse</span> #{r.dark_horse} {r.dark_horse_name}</span>}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="card">
            <h2>Upcoming Races</h2>
            {upcoming.length === 0 ? <p className="muted">All races for this date have been run.</p> : <RaceTable rows={upcoming} />}
          </section>
          {done.length > 0 && (
            <section className="card">
              <h2>Completed &amp; Results</h2>
              <RaceTable rows={done} completed />
            </section>
          )}
        </>
      )}
    </>
  );
}

export function RaceTable({ rows, completed }: { rows: RaceRow[]; completed?: boolean }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Post</th><th>Race</th><th className="hide-sm">Conditions</th><th>Rein Man's Picks</th>
            <th>Conf.</th><th>{completed ? "Result" : "Call"}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.race_id} className={r.is_cancelled ? "cancelled" : ""}>
              <td className="num">{postTime(r.post_time)}</td>
              <td><Link to={`/race/${r.race_id}`}><b>{r.track_id}</b> R{r.race_number}</Link></td>
              <td className="hide-sm muted">
                {distance(r)} {surfaceName(r.surface)} · {r.race_class || r.race_type || "—"} · {r.field_size} runners
                {r.track_condition ? ` · ${r.track_condition}` : ""}
              </td>
              <td><PickCell r={r} /></td>
              <td><Confidence level={r.confidence} /></td>
              <td>
                {completed || r.winner || r.is_cancelled || r.locked ? (
                  r.is_cancelled ? <span className="muted">Cancelled</span> :
                  r.winner ? (
                    <span>
                      Won by #{r.winner}{" "}
                      {r.top1_finish === 1 ? <span className="hit">✓ Rein Man</span> : r.top1_finish ? <span className="muted">(pick {ordinal(r.top1_finish)})</span> : null}
                    </span>
                  ) : <span className="muted">{r.locked ? "Locked · awaiting result" : "No selection recorded"}</span>
                ) : (
                  <Decision d={r.decision} />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ordinal(n: number) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
