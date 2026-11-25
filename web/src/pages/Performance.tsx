import { useState } from "react";
import { api, money, pct, signedPct, stamp, todayET, useApi, wagerLabel } from "../lib";
import { HorseSelection, Empty, ErrorBox, Loading } from "./common";
import { CalibrationChart, PLChart } from "./Charts";

type PickM = {
  races: number; graded: number; no_contest: number; top1_scratched: number; wins: number; win_pct: number | null;
  place_pct: number | null; show_pct: number | null; winner_in_top3_pct: number | null; flat_win_wagered: number;
  flat_win_returned: number; flat_win_net: number; flat_win_roi: number | null; avg_odds: number | null;
  log_loss: number | null; brier: number | null;
};
type WagerM = {
  wagers: number; won: number; hit_pct: number | null; wagered: number; returned: number; net: number; roi: number | null;
  refunded: number; avg_est_ev: number | null; max_drawdown: number; longest_losing_streak: number;
};
type Stats = {
  picks: PickM; wagers: WagerM; pick_breakdowns: Record<string, Record<string, PickM>>;
  wager_breakdowns: Record<string, Record<string, WagerM>>;
  selection_ranking: Record<string, { n: number; win_pct: number | null; itm_pct: number | null }>;
  pl_series: { date: string; wagers_net: number; top_pick_flat_net: number }[];
  calibration: { predicted: number; actual: number; n: number }[];
  benchmarks: Record<string, number | null>;
  actual_wagers?: { recorded: number; settled: number; wagered: number; returned: number; net: number; roi: number | null };
  drift?: { status: string; recent_log_loss?: number; prior_log_loss?: number };
};

const DIMS: [string, string][] = [
  ["track", "Racetrack"], ["wager_type", "Wager type"], ["race_class", "Race class"], ["distance", "Distance"],
  ["surface", "Surface"], ["track_condition", "Track condition"], ["odds_range", "Odds range"], ["confidence", "Confidence"],
  ["model_version", "Model version"], ["day", "Day"], ["month", "Month"], ["year", "Year"],
];

export function Performance() {
  const [track, setTrack] = useState("");
  const { data, error } = useApi<{ computed_at: string | null; stats: Stats | null; tracks: { track_id: string; name: string | null }[] }>(
    `/api/performance${track ? `?track=${track}` : ""}`,
  );
  const [dim, setDim] = useState("track");
  const [view, setView] = useState<"wagers" | "picks">("wagers");
  const s = data?.stats;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Performance</h1>
          <p className="muted">
            Every locked selection, graded against official results. {data?.computed_at ? `Updated ${stamp(data.computed_at)}.` : ""}
          </p>
        </div>
        <label className="select">
          <span className="sr-only">Racetrack</span>
          <select value={track} onChange={(e) => setTrack(e.target.value)}>
            <option value="">All racetracks</option>
            {data?.tracks.map((t) => <option key={t.track_id} value={t.track_id}>{t.name || t.track_id}</option>)}
          </select>
        </label>
      </div>
      {error && <ErrorBox error={error} />}
      {!data && !error && <Loading />}
      {data && !s && <Empty>No graded races yet. The record starts with the first locked selection and its official result.</Empty>}
      {s && (
        <>
          {s.picks.graded < 1000 && (
            <p className="notice">
              Sample size: {s.picks.graded.toLocaleString()} graded races. More results are needed to assess long-term performance.
            </p>
          )}
          <section className="tiles">
            <div className="tile"><span>Graded races</span><b>{s.picks.graded.toLocaleString()}</b></div>
            <div className="tile"><span>Top pick wins</span><b>{pct(s.picks.win_pct)}</b><small>Place {pct(s.picks.place_pct)} · Show {pct(s.picks.show_pct)}</small></div>
            <div className="tile"><span>Hypothetical ROI</span><b className={(s.wagers.roi ?? 0) >= 0 ? "pos" : "neg"}>{signedPct(s.wagers.roi)}</b><small>{s.wagers.wagers} wagers · net {money(s.wagers.net)}</small></div>
            <div className="tile"><span>Wagered / returned</span><b>{money(s.wagers.wagered)}</b><small>returned {money(s.wagers.returned)}</small></div>
            <div className="tile"><span>Max drawdown</span><b>{money(s.wagers.max_drawdown)}</b><small>Longest losing streak {s.wagers.longest_losing_streak}</small></div>
            <div className="tile"><span>Top pick, $2 flat win</span><b className={(s.picks.flat_win_roi ?? 0) >= 0 ? "pos" : "neg"}>{signedPct(s.picks.flat_win_roi)}</b><small>avg odds {s.picks.avg_odds?.toFixed(1) ?? "—"}-1</small></div>
          </section>

          <section className="card">
            <h2>Profit / Loss</h2>
            <PLChart data={s.pl_series} series={[{ key: "wagers_net", label: "Wagers (hypothetical)" }, { key: "top_pick_flat_net", label: "Top pick $2 flat win" }]} />
          </section>

          <div className="two-col">
            <section className="card">
              <h2>Market Benchmarks</h2>
              <div className="table-wrap"><table className="data compact">
                <tbody>
                  <tr><td>Rein Man top pick wins</td><td className="num">{pct(s.benchmarks.model_top_pick_win_pct)}</td></tr>
                  <tr><td>Morning-line favorite wins</td><td className="num">{pct(s.benchmarks.ml_favorite_win_pct)}</td></tr>
                  <tr><td>Public (tote) favorite wins</td><td className="num">{pct(s.benchmarks.public_favorite_win_pct)}</td></tr>
                  <tr><td>Log-loss: Rein Man</td><td className="num">{s.benchmarks.model_log_loss?.toFixed(4) ?? "—"}</td></tr>
                  <tr><td>Log-loss: market-implied</td><td className="num">{s.benchmarks.market_log_loss?.toFixed(4) ?? "—"}</td></tr>
                  <tr><td>Log-loss: random (uniform)</td><td className="num">{s.benchmarks.uniform_log_loss?.toFixed(4) ?? "—"}</td></tr>
                </tbody>
              </table></div>
              <p className="muted small">Lower log-loss is better. Beating the market-implied line is the bar that matters.</p>
              {s.drift && <p className="small">Model health: <b>{s.drift.status}</b>{s.drift.recent_log_loss ? ` (recent ${s.drift.recent_log_loss.toFixed(3)} vs prior ${s.drift.prior_log_loss?.toFixed(3)})` : ""}</p>}
            </section>
            <section className="card">
              <h2>Probability Calibration</h2>
              <CalibrationChart bins={s.calibration} />
            </section>
          </div>

          <section className="card">
            <h2>Selection Ranking</h2>
            <div className="table-wrap"><table className="data compact">
              <thead><tr><th>Rein Man's rank</th><th className="num">Races</th><th className="num">Won</th><th className="num">In the money</th></tr></thead>
              <tbody>
                {Object.entries(s.selection_ranking).map(([k, v]) => (
                  <tr key={k}><td>{k.replace("rank_", "#")}</td><td className="num">{v.n}</td><td className="num">{pct(v.win_pct)}</td><td className="num">{pct(v.itm_pct)}</td></tr>
                ))}
              </tbody>
            </table></div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>Breakdown</h2>
              <div className="seg">
                <button className={view === "wagers" ? "on" : ""} onClick={() => setView("wagers")}>Wagers</button>
                <button className={view === "picks" ? "on" : ""} onClick={() => setView("picks")}>Top picks</button>
              </div>
              <select value={dim} onChange={(e) => setDim(e.target.value)} aria-label="Break down by">
                {DIMS.filter(([k]) => (view === "wagers" ? s.wager_breakdowns[k] : s.pick_breakdowns[k])).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </div>
            <Breakdown s={s} dim={dim} view={view} />
          </section>

          {!track && s.actual_wagers && <ActualWagers summary={s.actual_wagers} />}
        </>
      )}
      <Intelligence track={track} />
      <Models />
    </>
  );
}

function Breakdown({ s, dim, view }: { s: Stats; dim: string; view: "wagers" | "picks" }) {
  if (view === "wagers") {
    const rows = Object.entries(s.wager_breakdowns[dim] ?? {});
    if (!rows.length) return <p className="muted">No graded wagers.</p>;
    return (
      <div className="table-wrap"><table className="data">
        <thead><tr><th></th><th className="num">Wagers</th><th className="num">Hit</th><th className="num">Wagered</th><th className="num">Returned</th><th className="num">Net</th><th className="num">ROI</th><th className="num hide-sm">Max DD</th></tr></thead>
        <tbody>{rows.map(([k, v]) => (
          <tr key={k}><td>{dim === "wager_type" ? wagerLabel(k) : k}</td><td className="num">{v.wagers}</td><td className="num">{pct(v.hit_pct)}</td><td className="num">{money(v.wagered)}</td><td className="num">{money(v.returned)}</td>
            <td className={`num ${v.net >= 0 ? "pos" : "neg"}`}>{money(v.net)}</td><td className="num">{signedPct(v.roi)}</td><td className="num hide-sm">{money(v.max_drawdown)}</td></tr>
        ))}</tbody>
      </table></div>
    );
  }
  const rows = Object.entries(s.pick_breakdowns[dim] ?? {});
  if (!rows.length) return <p className="muted">No graded picks.</p>;
  return (
    <div className="table-wrap"><table className="data">
      <thead><tr><th></th><th className="num">Races</th><th className="num">Win</th><th className="num">Place</th><th className="num">Show</th><th className="num">$2 flat ROI</th><th className="num hide-sm">Log-loss</th></tr></thead>
      <tbody>{rows.map(([k, v]) => (
        <tr key={k}><td>{k}</td><td className="num">{v.graded}</td><td className="num">{pct(v.win_pct)}</td><td className="num">{pct(v.place_pct)}</td><td className="num">{pct(v.show_pct)}</td>
          <td className="num">{signedPct(v.flat_win_roi)}</td><td className="num hide-sm">{v.log_loss?.toFixed(3) ?? "—"}</td></tr>
      ))}</tbody>
    </table></div>
  );
}

type Actual = { id: number; created_at: string; race_date: string; track_id: string | null; race_number: number | null; wager_type: string; selection: string; amount: number; payout: number | null; notes: string | null };

function ActualWagers({ summary }: { summary: NonNullable<Stats["actual_wagers"]> }) {
  const { data, reload } = useApi<{ rows: Actual[] }>("/api/actual-wagers");
  const [f, setF] = useState({ race_date: todayET(), track_id: "", race_number: "", wager_type: "WIN", selection: "", amount: "", notes: "" });
  const [err, setErr] = useState<string | null>(null);
  return (
    <section className="card">
      <h2>My Actual Wagers <span className="muted small">(kept separate from Rein Man's hypothetical record)</span></h2>
      <p>Recorded {summary.recorded} · settled {summary.settled} · wagered {money(summary.wagered)} · returned {money(summary.returned)} · net <b className={summary.net >= 0 ? "pos" : "neg"}>{money(summary.net)}</b> · ROI {signedPct(summary.roi)}</p>
      <form className="inline-form" onSubmit={async (e) => {
        e.preventDefault(); setErr(null);
        try {
          await api("/api/actual-wagers", { method: "POST", body: JSON.stringify({ ...f, amount: Number(f.amount), track_id: f.track_id.toUpperCase() || null }) });
          setF({ ...f, selection: "", amount: "", notes: "" }); reload();
        } catch (x) { setErr((x as Error).message); }
      }}>
        <input type="date" value={f.race_date} onChange={(e) => setF({ ...f, race_date: e.target.value })} aria-label="Date" />
        <input placeholder="Track" value={f.track_id} onChange={(e) => setF({ ...f, track_id: e.target.value })} size={5} aria-label="Track" />
        <input placeholder="Race" value={f.race_number} onChange={(e) => setF({ ...f, race_number: e.target.value })} size={3} aria-label="Race" inputMode="numeric" />
        <select value={f.wager_type} onChange={(e) => setF({ ...f, wager_type: e.target.value })} aria-label="Wager type">
          {["WIN", "PLACE", "SHOW", "EXACTA", "TRIFECTA", "SUPERFECTA", "DAILY_DOUBLE", "PICK3", "PICK4", "PICK5", "PICK6"].map((w) => <option key={w} value={w}>{wagerLabel(w)}</option>)}
        </select>
        <input placeholder="Selection (e.g. 3-5)" value={f.selection} onChange={(e) => setF({ ...f, selection: e.target.value })} aria-label="Selection" />
        <input placeholder="Amount $" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} size={6} inputMode="decimal" aria-label="Amount" />
        <button className="btn primary">Record</button>
      </form>
      {err && <p className="error">{err}</p>}
      {data && data.rows.length > 0 && (
        <div className="table-wrap"><table className="data compact">
          <thead><tr><th>Date</th><th>Race</th><th>Wager</th><th>Selection</th><th className="num">Amount</th><th className="num">Payout</th></tr></thead>
          <tbody>{data.rows.map((r) => (
            <tr key={r.id}><td>{r.race_date}</td><td>{r.track_id ?? ""} {r.race_number ? `R${r.race_number}` : ""}</td><td>{wagerLabel(r.wager_type)}</td><td className="mono"><HorseSelection selection={r.selection} /></td><td className="num">{money(r.amount)}</td>
              <td className="num">{r.payout != null ? money(r.payout) : <SettleButton id={r.id} onDone={reload} />}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </section>
  );
}

function SettleButton({ id, onDone }: { id: number; onDone: () => void }) {
  const [v, setV] = useState("");
  return (
    <form className="settle" onSubmit={async (e) => { e.preventDefault(); await api(`/api/actual-wagers/${id}/settle`, { method: "POST", body: JSON.stringify({ payout: Number(v) }) }); onDone(); }}>
      <input value={v} onChange={(e) => setV(e.target.value)} placeholder="$ back" size={6} inputMode="decimal" aria-label="Payout" />
      <button className="btn ghost small" disabled={v === ""}>Settle</button>
    </form>
  );
}

type Model = { model_version: string; created_at: string; kind: string; status: string; train_window: string | null; metrics: Record<string, unknown> | null; notes: string | null };

function Models() {
  const { data, error } = useApi<{ models: Model[]; events: { id: number; at: string; model_version: string | null; event: string; details: Record<string, unknown> }[]; backtests: { id: number; at: string; test_window: string; metrics: { races: number; mean: Record<string, number | null>; bets: Record<string, { n: number; roi: number | null }> } }[] }>("/api/models");
  const status = useApi<{ jobs: { id: number; job: string; started_at: string; status: string; details: string | null }[]; counts: Record<string, number | string | null>; champion: { model_version: string } | null }>("/api/status");
  if (error) return null;
  return (
    <section className="card">
      <h2>Model Learning &amp; Audit</h2>
      {status.data && (
        <p className="small muted">
          Champion: <b>{status.data.champion?.model_version ?? "—"}</b> · {String(status.data.counts.races)} races stored ({String(status.data.counts.first_date ?? "—")} → {String(status.data.counts.last_date ?? "—")}) ·
          {" "}{String(status.data.counts.predictions)} snapshots · {String(status.data.counts.locks)} locked · {String(status.data.counts.grades)} graded
        </p>
      )}
      {!data ? <Loading /> : (
        <>
          <div className="table-wrap"><table className="data compact">
            <thead><tr><th>Version</th><th>Kind</th><th>Status</th><th className="hide-sm">Window</th><th className="num">Test log-loss</th><th className="num">vs champion</th></tr></thead>
            <tbody>{data.models.map((m) => (
              <tr key={m.model_version}><td className="mono">{m.model_version}</td><td>{m.kind}</td><td><span className={`status ${m.status}`}>{m.status}</span></td>
                <td className="hide-sm small muted">{m.train_window ?? "—"}</td>
                <td className="num">{typeof m.metrics?.log_loss === "number" ? (m.metrics.log_loss as number).toFixed(4) : "—"}</td>
                <td className="num">{typeof m.metrics?.ll_diff === "number" ? (m.metrics.ll_diff as number).toFixed(4) : "—"}</td></tr>
            ))}</tbody>
          </table></div>
          {data.backtests.length > 0 && (
            <>
              <h3>Walk-forward backtests</h3>
              <div className="table-wrap"><table className="data compact">
                <thead><tr><th>Run</th><th>Window</th><th className="num">Races</th><th className="num">Log-loss model / ML / closing</th><th className="num">Top pick ROI</th><th className="num">Value vs ML ROI</th></tr></thead>
                <tbody>{data.backtests.map((b) => (
                  <tr key={b.id}><td>{stamp(b.at)}</td><td>{b.test_window}</td><td className="num">{b.metrics.races}</td>
                    <td className="num">{b.metrics.mean.ll_model?.toFixed(3)} / {b.metrics.mean.ll_ml?.toFixed(3)} / {b.metrics.mean.ll_close?.toFixed(3) ?? "—"}</td>
                    <td className="num">{signedPct(b.metrics.bets.top_pick_flat?.roi)}</td><td className="num">{signedPct(b.metrics.bets.value_vs_morning_line?.roi)} ({b.metrics.bets.value_vs_morning_line?.n ?? 0})</td></tr>
                ))}</tbody>
              </table></div>
            </>
          )}
          <h3>Recent model events</h3>
          <ul className="timeline small">
            {data.events.slice(0, 15).map((e) => <li key={e.id}>{stamp(e.at)} — <b>{e.event}</b> {e.model_version ?? ""} <span className="muted">{JSON.stringify(e.details).slice(0, 160)}</span></li>)}
          </ul>
          {status.data && (
            <>
              <h3>Automation log</h3>
              <ul className="timeline small">
                {status.data.jobs.slice(0, 12).map((j) => <li key={j.id}>{stamp(j.started_at)} — {j.job} <span className={`status ${j.status}`}>{j.status}</span></li>)}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}


type IntelligenceData = {
  leaders: { id: string; name: string; starts: number; wins: number; win_pct: number }[];
  headlines: { title: string; url: string; source: string; published_at: string }[];
};

function Intelligence({ track }: { track: string }) {
  const { data, error } = useApi<IntelligenceData>(`/api/intelligence${track ? `?track=${encodeURIComponent(track)}` : ""}`);
  return (
    <section className="card">
      <h2>Racing Intelligence</h2>
      {error && <ErrorBox error={error} />}
      {!data && !error && <Loading />}
      {data && (
        <>
          <h3>Leading trainers · last 30 days</h3>
          {data.leaders.length ? (
            <div className="table-wrap"><table className="data compact">
              <thead><tr><th>Trainer</th><th className="num">Starts</th><th className="num">Wins</th><th className="num">Win rate</th></tr></thead>
              <tbody>{data.leaders.map((t) => (
                <tr key={t.id}><td>{t.name}</td><td className="num">{t.starts}</td><td className="num">{t.wins}</td><td className="num">{pct(t.win_pct)}</td></tr>
              ))}</tbody>
            </table></div>
          ) : <p className="muted small">Rankings appear once trainers have 10 starts in fully recorded official fields.</p>}
          <h3>Recent racing headlines</h3>
          {data.headlines.length ? <ul className="timeline small">
            {data.headlines.map((n) => <li key={n.url}><a href={n.url} target="_blank" rel="noopener noreferrer">{n.title}</a> <span className="muted"> · {n.source} · {stamp(n.published_at)}</span></li>)}
          </ul> : <p className="muted small">No recent headlines collected yet.</p>}
        </>
      )}
    </section>
  );
}
