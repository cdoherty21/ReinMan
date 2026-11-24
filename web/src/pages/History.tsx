import { useEffect, useRef, useState } from "react";
import { Link } from "../main";
import { money, odds, shiftDate, stamp, todayET, useApi, wagerLabel } from "../lib";
import { Confidence, Empty, ErrorBox, Loading } from "./common";
import { ordinal } from "./Today";

type Row = {
  race_id: string; race_date: string; track_id: string; race_number: number; top1: string; top1_name: string | null;
  top2: string | null; top3: string | null; confidence: string; decision: string; model_version: string; created_at: string;
  locked_at: string; top1_finish: number | null; winner_rank: number | null; top1_odds: number | null;
  top1_win_payoff: number | null; status: string | null; wagered: number; returned: number; pending_wagers: number;
  wager_count: number; wager_type: string | null; wager_selection: string | null; wager_status: string | null;
};

type Wager = {
  wager_id: number; wager_type: string; selection: string; cost: number;
  grade_status: string | null; payout: number | null;
};

function profit(value: number) {
  return (value > 0 ? "+" : "") + money(value);
}

function profitClass(value: number) {
  return value > 0 ? "pos" : value < 0 ? "neg" : "muted";
}

function TopPickProfit({ row }: { row: Row }) {
  if (row.status === "top1_scratched" || row.status === "no_contest") {
    return <span className="muted">{money(0)} · Refunded</span>;
  }
  if (row.status !== "graded") return <span className="muted">Pending</span>;
  if (row.top1_finish === 1 && (row.top1_win_payoff == null || row.top1_win_payoff <= 0)) {
    return <span className="muted">Awaiting payout</span>;
  }
  const net = row.top1_finish === 1 ? row.top1_win_payoff! - 2 : -2;
  return <span className={profitClass(net)}>{profit(net)}</span>;
}

function WagerBreakdown({ row, onClose }: { row: Row; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const { data, error } = useApi<{ wagers: Wager[] }>(`/api/race/${encodeURIComponent(row.race_id)}`);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return (
    <dialog ref={dialog} className="wager-dialog card" onClose={onClose} aria-labelledby="wager-breakdown-title">
      <div className="card-head">
        <h2 id="wager-breakdown-title">{row.track_id} R{row.race_number} · Wager breakdown</h2>
        <button className="btn ghost small" onClick={() => dialog.current?.close()}>Close</button>
      </div>
      <p className="muted small">{row.race_date} · Rein Man's hypothetical wagers</p>
      {error && <ErrorBox error={error} />}
      {!data && !error && <Loading />}
      {data && (
        <>
          {data.wagers.length === 0 ? <Empty>No recommended wagers.</Empty> : (
            <div className="table-wrap">
              <table className="data compact">
                <thead><tr><th>Bet</th><th>Selection</th><th>Result</th><th className="num">Stake</th><th className="num">Payout</th><th className="num">Net</th></tr></thead>
                <tbody>
                  {data.wagers.map((w) => {
                    const net = w.payout == null ? null : w.payout - w.cost;
                    return (
                      <tr key={w.wager_id}>
                        <td>{wagerLabel(w.wager_type)}</td>
                        <td className="mono">{w.selection}</td>
                        <td><span className={`res ${w.grade_status ?? ""}`}>{w.grade_status?.replace(/_/g, " ") ?? "Pending"}</span></td>
                        <td className="num">{money(w.cost)}</td>
                        <td className="num">{money(w.payout)}</td>
                        <td className={`num ${net == null ? "muted" : profitClass(net)}`}>{net == null ? "—" : profit(net)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p><b>Recommended Bets P/L </b>{data.wagers.some((w) => w.payout == null) ? <span className="muted">Pending settlement</span> : (() => {
            const net = data.wagers.reduce((sum, w) => sum + (w.payout ?? 0) - w.cost, 0);
            return <b className={profitClass(net)}>{profit(net)}</b>;
          })()}</p>
        </>
      )}
    </dialog>
  );
}

export function History() {
  const [from, setFrom] = useState(shiftDate(todayET(), -30));
  const [to, setTo] = useState(todayET());
  const [track, setTrack] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Row | null>(null);
  const q = new URLSearchParams({ from, to, page: String(page) });
  if (track) q.set("track", track.toUpperCase());
  const { data, error } = useApi<{ rows: Row[] }>(`/api/history?${q}`);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>History</h1>
          <p className="muted">Top Pick P/L assumes a $2 win bet on every top pick. Recommended Bets P/L tracks only Rein Man’s suggested wagers.</p>
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
                <tr><th>Date</th><th>Race</th><th>Top pick</th><th className="num">Odds</th><th>Top pick finish</th><th className="hide-sm">Winner rank</th><th>Conf.</th><th className="num">Top Pick P/L</th><th>Recommended bets</th><th className="num">Recommended Bets P/L</th><th className="hide-sm">Locked</th></tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.race_id}>
                    <td>{r.race_date}</td>
                    <td><Link to={`/race/${r.race_id}`}><b>{r.track_id}</b> R{r.race_number}</Link></td>
                    <td>#{r.top1} {r.top1_name}</td>
                    <td className="num">{odds(r.top1_odds)}</td>
                    <td>{r.status == null ? <span className="muted">Pending</span> : r.status !== "graded" ? <span className="muted">{r.status.replace("_", " ")}</span> :
                      r.top1_finish === 1 ? <span className="hit">1st</span> : r.top1_finish ? ordinal(r.top1_finish) : "Unplaced"}</td>
                    <td className="hide-sm">{r.winner_rank ? ordinal(r.winner_rank) : "—"}</td>
                    <td><Confidence level={r.confidence} /></td>
                    <td className="num"><TopPickProfit row={r} /></td>
                    <td>{r.wager_count === 1 && r.wager_type ? <><span>{wagerLabel(r.wager_type)} {r.wager_selection}</span><br /><span className={`res ${r.wager_status ?? ""}`}>{r.wager_status?.replace(/_/g, " ") ?? "Pending"}</span></> : r.wager_count > 1 ? <button type="button" className="linkish" onClick={() => setSelected(r)}>{r.wager_count} bets</button> : <span className="muted">Pass</span>}</td>
                    <td className="num">{r.wagered ? <button type="button" className="btn ghost small" onClick={() => setSelected(r)} aria-label={`View wagers for ${r.track_id} race ${r.race_number}`}><span className={r.pending_wagers > 0 ? "muted" : profitClass(r.returned - r.wagered)}>{r.pending_wagers > 0 ? "Pending" : profit(r.returned - r.wagered)} ▾</span></button> : <span className="muted">Pass</span>}</td>
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
      {selected && <WagerBreakdown key={selected.race_id} row={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
