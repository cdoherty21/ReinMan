import { useState } from "react";
import { Link } from "../main";
import { money, odds, shiftDate, stamp, todayET, useApi } from "../lib";
import { Confidence, Empty, ErrorBox, Loading } from "./common";
import { ordinal } from "./Today";

type Row = {
  race_id: string; race_date: string; track_id: string; race_number: number; top1: string; top1_name: string | null;
  top2: string | null; top3: string | null; confidence: string; decision: string; model_version: string; created_at: string;
  locked_at: string; top1_finish: number | null; winner_rank: number | null; top1_odds: number | null;
  top1_win_payoff: number | null; status: string | null; wagered: number; returned: number;
};

export function History() {
  const [from, setFrom] = useState(shiftDate(todayET(), -30));
  const [to, setTo] = useState(todayET());
  const [track, setTrack] = useState("");
  const [page, setPage] = useState(0);
  const q = new URLSearchParams({ from, to, page: String(page) });
  if (track) q.set("track", track.toUpperCase());
  const { data, error } = useApi<{ rows: Row[] }>(`/api/history?${q}`);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>History</h1>
          <p className="muted">Every locked selection, winners and losers alike. Nothing is deleted or revised.</p>
        </div>
      </div>
      <section className="filters">
        <label>From <input type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(0); }} /></label>
        <label>To <input type="date" value={to} onChange={(e) => { setTo(e.target.value); setPage(0); }} /></label>
        <label>Track <input value={track} placeholder="e.g. AQU" maxLength={5} onChange={(e) => { setTrack(e.target.value); setPage(0); }} /></label>
      </section>
      {error && <ErrorBox error={error} />}
      {!data && !error && <Loading />}
      {data && data.rows.length === 0 && <Empty>No locked selections in this range yet.</Empty>}
      {data && data.rows.length > 0 && (
        <section className="card">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Date</th><th>Race</th><th>Top pick</th><th className="num">Odds</th><th>Finish</th><th className="hide-sm">Winner rank</th><th>Conf.</th><th className="num">Wagers</th><th className="hide-sm">Locked</th></tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.race_id}>
                    <td>{r.race_date}</td>
                    <td><Link to={`/race/${r.race_id}`}><b>{r.track_id}</b> R{r.race_number}</Link></td>
                    <td>#{r.top1} {r.top1_name}</td>
                    <td className="num">{odds(r.top1_odds)}</td>
                    <td>{r.status == null ? <span className="muted">Pending</span> : r.status !== "graded" ? <span className="muted">{r.status.replace("_", " ")}</span> :
                      r.top1_finish === 1 ? <span className="hit">Won {money(r.top1_win_payoff)}</span> : r.top1_finish ? ordinal(r.top1_finish) : "Unplaced"}</td>
                    <td className="hide-sm">{r.winner_rank ? ordinal(r.winner_rank) : "—"}</td>
                    <td><Confidence level={r.confidence} /></td>
                    <td className="num">{r.wagered ? <span className={r.returned - r.wagered >= 0 ? "pos" : "neg"}>{money(r.returned - r.wagered)}</span> : <span className="muted">pass</span>}</td>
                    <td className="hide-sm small muted">{stamp(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <button className="btn ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>‹ Newer</button>
            <button className="btn ghost" disabled={data.rows.length < 100} onClick={() => setPage(page + 1)}>Older ›</button>
          </div>
        </section>
      )}
    </>
  );
}
