import { Link } from "../main";
import { money, odds, pct, postTime, signedPct, useApi, wagerLabel } from "../lib";
import { Confidence, DatePicker, Empty, ErrorBox, Loading, useDateParam } from "./common";

type W = {
  wager_id: number; race_id: string; wager_type: string; selection: string; cost: number; est_prob: number; ev: number;
  track_id: string; track_name: string | null; race_number: number; post_time: string; confidence: string;
  grade_status: string | null; payout: number | null; rationale: string | null;
};
type V = {
  race_id: string; track_id: string; track_name: string | null; race_number: number; post_time: string;
  program_number: string; horse_name: string; win_prob: number; fair_odds: number; odds_used: number | null;
  odds_source: string | null; ev_win: number | null; confidence: string; is_dark_horse: number;
};

export function Opportunities() {
  const [date, setDate] = useDateParam();
  const { data, error } = useApi<{ wagers: W[]; value: V[] }>(`/api/opportunities?date=${date}`);
  const now = Date.now();
  const upcoming = (data?.wagers ?? []).filter((w) => Date.parse(w.post_time) > now);
  const settled = (data?.wagers ?? []).filter((w) => Date.parse(w.post_time) <= now);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Value Opportunities</h1>
          <p className="muted">Potential value, ranked by estimated edge after takeout.</p>
        </div>
        <DatePicker date={date} onChange={setDate} />
      </div>
      {error && <ErrorBox error={error} />}
      {!data && !error && <Loading />}
      {data && (
        <>
          <section className="card">
            <h2>Rein Man's Best Bets</h2>
            {upcoming.length === 0 ? (
              <Empty>No upcoming bets meet the value thresholds.</Empty>
            ) : (
              <WagerTable rows={upcoming} />
            )}
          </section>
          <section className="card">
            <h2>Value Picks &amp; Dark Horses</h2>
            {data.value.length === 0 ? <p className="muted">None identified for this date.</p> : (
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>Post</th><th>Race</th><th>Horse</th><th className="num">Win %</th><th className="num">Fair</th><th className="num">Odds</th><th className="num">EV</th><th>Conf.</th></tr></thead>
                  <tbody>
                    {data.value.map((v) => (
                      <tr key={`${v.race_id}-${v.program_number}`}>
                        <td className="num">{postTime(v.post_time)}</td>
                        <td><Link to={`/race/${v.race_id}`}><b>{v.track_id}</b> R{v.race_number}</Link></td>
                        <td>#{v.program_number} {v.horse_name} {v.is_dark_horse ? <span className="tag dark">Dark horse</span> : <span className="tag value">Value</span>}</td>
                        <td className="num">{pct(v.win_prob)}</td>
                        <td className="num">{odds(v.fair_odds)}</td>
                        <td className="num">{odds(v.odds_used)}{v.odds_source === "morning_line" ? <sup>ML</sup> : null}</td>
                        <td className={`num ${v.ev_win && v.ev_win > 0 ? "pos" : ""}`}>{signedPct(v.ev_win)}</td>
                        <td><Confidence level={v.confidence} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          {settled.length > 0 && (
            <section className="card">
              <h2>Earlier Races</h2>
              <WagerTable rows={settled} />
            </section>
          )}
          <p className="muted small">
            EV for exotic and multi-race wagers is estimated from market-implied combination probabilities and typical takeout;
            win EV uses live tote odds when available, otherwise the morning line (with a stricter threshold).
          </p>
        </>
      )}
    </>
  );
}

function WagerTable({ rows }: { rows: W[] }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr><th>Post</th><th>Race</th><th>Wager</th><th>Selection</th><th className="num">Cost</th><th className="num">Hit</th><th className="num">Est. EV</th><th>Conf.</th><th>Result</th></tr></thead>
        <tbody>
          {rows.map((w) => (
            <tr key={w.wager_id}>
              <td className="num">{postTime(w.post_time)}</td>
              <td><Link to={`/race/${w.race_id}`}><b>{w.track_id}</b> R{w.race_number}</Link></td>
              <td>{wagerLabel(w.wager_type)}</td>
              <td className="mono">{w.selection}</td>
              <td className="num">{money(w.cost)}</td>
              <td className="num">{pct(w.est_prob)}</td>
              <td className="num pos">{signedPct(w.ev)}</td>
              <td><Confidence level={w.confidence} /></td>
              <td>{w.grade_status ? <span className={`res ${w.grade_status}`}>{w.grade_status.replace("_", " ")} {money(w.payout)}</span> : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
