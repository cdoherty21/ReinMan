import { Fragment, useState } from "react";
import { Link } from "../main";
import { money, odds, pct, postTime, signedPct, stamp, surfaceName, useApi, wagerLabel } from "../lib";
import { HorseNumber, HorseSelection, Confidence, Decision, ErrorBox, Loading } from "./common";
import { distance, ordinal } from "./Today";

type Pick = {
  program_number: string; horse_name: string; rank: number; win_prob: number; place_prob: number; show_prob: number;
  exp_finish: number; fair_odds: number; odds_used: number | null; odds_source: string | null; market_prob: number | null;
  ev_win: number | null; uncertainty: number | null; factors: string[];
};
type Runner = {
  program_number: string; post_position: number | null; horse_name: string; jockey_name: string | null;
  trainer_name: string | null; sire: string | null; dam: string | null; weight: number | null; equipment: string | null;
  medication: string | null; ml_odds: number | null; live_odds: number | null; scratched: number;
  extra?: string | null;
};
type PastPerf = {
  date: string; track: string; distance_raw: string; condition: string; speed_fig: number | null;
  post_position: number; call_positions: number[]; finish: number; field_size: number | null;
  odds: number; favorite: boolean; comment: string | null; trouble: boolean;
};

function pastPerformances(r: Runner | undefined): PastPerf[] {
  if (!r?.extra) return [];
  try {
    return (JSON.parse(r.extra).past_performances ?? []) as PastPerf[];
  } catch {
    return [];
  }
}

function PastPerformances({ rows }: { rows: PastPerf[] }) {
  if (!rows.length) return null;
  const ordinal = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? "th" : ["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
  return (
    <div className="pp">
      <h4>Past performances <span className="muted small">(from your uploaded programs)</span></h4>
      <div className="table-wrap">
        <table className="data compact">
          <thead>
            <tr><th>Date</th><th>Track</th><th>Dist</th><th>Cond</th><th className="num">Fig</th><th>Calls</th>
              <th>Finish</th><th className="num">Odds</th><th className="hide-sm">Comment</th></tr>
          </thead>
          <tbody>
            {rows.slice(0, 8).map((p) => (
              <tr key={`${p.date}-${p.track}`} className={p.finish === 1 ? "is-pick" : ""}>
                <td>{p.date.slice(5)}<span className="muted">/{p.date.slice(2, 4)}</span></td>
                <td>{p.track}</td>
                <td>{p.distance_raw}</td>
                <td>{p.condition}</td>
                <td className="num">{p.speed_fig ?? "—"}</td>
                <td className="mono">{p.call_positions.slice(0, -1).join("-")}</td>
                <td><b>{ordinal(p.finish)}</b>{p.field_size ? <span className="muted"> of {p.field_size}</span> : null}</td>
                <td className="num">{p.favorite ? "*" : ""}{p.odds.toFixed(2)}</td>
                <td className="hide-sm small">{p.trouble && <span className="tag scr">trouble</span>} {p.comment}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
type Wager = {
  wager_id: number; wager_type: string; structure: string; selection: string; combos: number; base_amount: number;
  cost: number; est_prob: number; est_return: number | null; ev: number | null; rationale: string | null; legs: string;
  grade_status: string | null; payout: number | null;
};
type RaceResp = {
  race: Record<string, string | number | null>;
  lock: { prediction_id: number; locked_at: string } | null;
  snapshots: { prediction_id: number; created_at: string; reason: string; model_version: string; top1: string; confidence: string; decision: string }[];
  prediction: null | {
    prediction_id: number; created_at: string; model_version: string; top1: string; top2: string | null; top3: string | null;
    most_likely: string; value_pick: string | null; dark_horse: string | null; confidence: string; confidence_score: number;
    decision: string; take: string; pace: { scenario: string; early_speed_count: number } | null;
    data_notes: { unavailable: string[]; odds_basis: string } | null;
  };
  picks: Pick[]; wagers: Wager[]; runners: Runner[];
  results: { program_number: string | null; horse_name: string; finish_position: number; win_payoff: number | null; place_payoff: number | null; show_payoff: number | null; dead_heat: number }[];
  payoffs: { wager_type: string; wager_name: string | null; winning_numbers: string; payoff_amount: number | null; base_amount: number | null }[];
  changes: { change_type: string | null; text: string; seen_at: string }[];
  grade: null | { top1_finish: number | null; winner_rank: number | null; status: string };
  docNotes?: DocNote[];
};
type DocNote = { id: number; document_id: number; program_number: string | null; horse_name: string | null; kind: string; text: string; filename: string; uploaded_at: string };

const FACTOR_NAMES: Record<string, string> = {
  morning_line: "Morning line", horse_history: "Horse history", jockey_trainer: "Jockey/trainer stats",
  pedigree: "Pedigree", post_bias: "Post-position bias", speed_figures: "Speed figures", pace_figures: "Pace / running positions",
  trouble_notes: "Trouble notes", workouts: "Workouts",
};

export function Race({ id }: { id: string }) {
  const [snap, setSnap] = useState<number | null>(null);
  const { data, error } = useApi<RaceResp>(`/api/race/${encodeURIComponent(id)}${snap ? `?snapshot=${snap}` : ""}`);
  const [open, setOpen] = useState<string | null>(null);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const { race, prediction: p, picks, runners, wagers, results } = data;
  const byProg = new Map(runners.map((r) => [r.program_number, r]));
  const name = (prog: string | null) => (prog ? <><HorseNumber number={prog} /> {byProg.get(prog)?.horse_name ?? ""}</> : "—");
  const scratched = runners.filter((r) => r.scratched);
  const notesFor = (prog: string) => (data.docNotes ?? []).filter((n) => n.program_number === prog);
  const isLocked = data.lock && p && data.lock.prediction_id === p.prediction_id;
  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumb"><Link to={`/cards?date=${race.race_date}&track=${race.track_id}`}>{String(race.track_name || race.track_id)}</Link> · {String(race.race_date)}</p>
          <h1>Race {String(race.race_number)}{race.post_time ? <span className="muted"> · {postTime(race.post_time as string)}</span> : null}</h1>
          <p className="muted">
            {distance({ distance_desc: race.distance_desc as string, distance_yards: race.distance_yards as number })} ·{" "}
            {surfaceName(race.surface as string)} · {String(race.race_class || race.race_type || "—")}
            {race.purse ? ` · Purse ${money(Number(race.purse)).replace(".00", "")}` : ""}
            {race.track_condition ? ` · Track ${race.track_condition}` : ""}
            {race.weather_desc ? ` · ${race.weather_desc}` : ""}
            {race.temp_f != null ? ` · ${Math.round(Number(race.temp_f))}°F` : ""}
          </p>
        </div>
        {p && (
          <div className="stamp">
            {isLocked ? <span className="locked">🔒 Locked</span> : <span className="muted">Latest snapshot</span>}
            <span className="small muted">Recorded {stamp(p.created_at)}</span>
            <span className="small muted">Model {p.model_version}</span>
          </div>
        )}
      </div>

      {!p ? (
        <div className="empty">{!race.post_time ? "Pre-race picks need a verified post time. The entries and available evidence are below." : "Rein Man has not recorded an analysis for this race."}</div>
      ) : (
        <>
          <section className="take">
            <h2><img src="/gentleman-64.png" className="gentleman-logo" width={44} height={44} alt="" /> Rein Man's Take</h2>
            <p>{p.take}</p>
          </section>
          <section className="selections">
            <div><span>First</span><b>{name(p.top1)}</b></div>
            <div><span>Second</span><b>{name(p.top2)}</b></div>
            <div><span>Third</span><b>{name(p.top3)}</b></div>
            <div><span>Most likely winner</span><b>{name(p.most_likely)}</b></div>
            <div><span>Value contender</span><b>{p.value_pick ? name(p.value_pick) : "None at these prices"}</b></div>
            <div><span>Dark horse</span><b>{p.dark_horse ? name(p.dark_horse) : "None supported by evidence"}</b></div>
            <div><span>Confidence</span><b><Confidence level={p.confidence} /> <span className="muted small">{p.confidence_score.toFixed(2)}</span></b></div>
            <div><span>Decision</span><b><Decision d={p.decision} /></b></div>
            <div><span>Projected pace</span><b>{p.pace ? `${p.pace.scenario} (${p.pace.early_speed_count} early)` : <span className="muted">Unavailable — no running-position data</span>}</b></div>
          </section>

          <section className="card">
            <h2>Racing Form</h2>
            <div className="table-wrap">
              <table className="data form">
                <thead>
                  <tr>
                    <th>Rank</th><th>#</th><th>Horse</th><th className="hide-sm">Jockey</th><th className="hide-sm">Trainer</th>
                    <th className="num hide-sm">ML</th><th className="num">Odds</th><th className="num">Win %</th><th className="num hide-sm">Fair</th>
                    <th className="num hide-sm">EV</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {picks.map((x) => {
                    const r = byProg.get(x.program_number);
                    const isOpen = open === x.program_number;
                    const pick = x.rank <= 3;
                    return (
                      <Fragment key={x.program_number}>
                        <tr className={`${pick ? "is-pick" : ""} ${r?.scratched ? "scratched" : ""}`} onClick={() => setOpen(isOpen ? null : x.program_number)}>
                          <td>{x.rank}</td>
                          <td className="num"><HorseNumber number={x.program_number} /></td>
                          <td className="horse">
                            <b>{x.horse_name}</b>
                            {x.program_number === p.value_pick && <span className="tag value">Value</span>}
                            {x.program_number === p.dark_horse && <span className="tag dark">Dark horse</span>}
                            {r?.scratched ? <span className="tag scr">Scratched</span> : null}
                          </td>
                          <td className="hide-sm">{r?.jockey_name ?? "—"}{notesFor(x.program_number).length > 0 && <span className="tag value" title="Notes from your documents">doc</span>}</td>
                          <td className="hide-sm">{r?.trainer_name ?? "—"}</td>
                          <td className="num hide-sm">{odds(r?.ml_odds)}</td>
                          <td className="num">{odds(x.odds_used)}{x.odds_source === "live" ? "" : x.odds_used != null ? <sup title="morning line">ML</sup> : ""}</td>
                          <td className="num"><Bar v={x.win_prob} /> {pct(x.win_prob)}</td>
                          <td className="num hide-sm">{odds(x.fair_odds)}</td>
                          <td className={`num hide-sm ${x.ev_win != null && x.ev_win > 0 ? "pos" : ""}`}>{signedPct(x.ev_win)}</td>
                          <td><button className="btn ghost small" aria-expanded={isOpen}>{isOpen ? "Hide" : "Analysis"}</button></td>
                        </tr>
                        {isOpen && (
                          <tr className="analysis">
                            <td colSpan={11}>
                              <div className="analysis-grid">
                                <div>
                                  <h4>Probabilities</h4>
                                  <p>Win {pct(x.win_prob)} · Place {pct(x.place_prob)} · Show {pct(x.show_prob)}</p>
                                  <p>Expected finish {x.exp_finish.toFixed(1)} · Market {pct(x.market_prob)} · Uncertainty ±{pct(x.uncertainty)}</p>
                                </div>
                                <div>
                                  <h4>Evidence</h4>
                                  <ul className="factors">
                                    {x.factors.map((f, i) => <li key={i} className={f.startsWith("+") ? "pos" : f.startsWith("−") ? "neg" : ""}>{f}</li>)}
                                  </ul>
                                </div>
                                <div>
                                  <h4>Connections</h4>
                                  <p>J: {r?.jockey_name ?? "—"} · T: {r?.trainer_name ?? "—"}</p>
                                  <p className="muted">
                                    {r?.sire ? `By ${r.sire}` : ""}{r?.dam ? ` out of ${r.dam}` : ""}
                                    {r?.weight ? ` · ${r.weight} lb` : ""}{r?.medication ? ` · Med ${r.medication}` : ""}
                                    {r?.equipment ? ` · Equip ${r.equipment}` : ""}
                                  </p>
                                </div>
                              </div>
                              <PastPerformances rows={pastPerformances(r)} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {scratched.length > 0 && <p className="muted small">Scratched: {scratched.map((s, i) => <Fragment key={s.program_number}>{i > 0 && ", "}<HorseNumber number={s.program_number} /> {s.horse_name}</Fragment>)}</p>}
            {p.data_notes && p.data_notes.unavailable.length > 0 && (
              <p className="notice small">
                Not available for this race from the current data source:{" "}
                {p.data_notes.unavailable.map((u) => FACTOR_NAMES[u] ?? u).join(", ")}. Rein Man does not guess at missing data.
                Odds basis: {p.data_notes.odds_basis.replace("_", " ")}.
              </p>
            )}
          </section>

          <section className="card">
            <h2>Recommended Wagers <span className="muted small">(hypothetical, tracked)</span></h2>
            {wagers.length === 0 ? (
              <p className="muted">Pass. No wager in this race clears Rein Man's value threshold. The top three are ranked anyway.</p>
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>Type</th><th>Selection</th><th className="num">Cost</th><th className="num">Hit prob.</th><th className="num">Est. EV</th><th className="hide-sm">Why</th><th>Result</th></tr></thead>
                  <tbody>
                    {wagers.map((w) => (
                      <tr key={w.wager_id}>
                        <td>{wagerLabel(w.wager_type)}{JSON.parse(w.legs).length > 1 ? <span className="muted small"> (from R{String(race.race_number)})</span> : null}</td>
                        <td className="mono"><HorseSelection selection={w.selection} /></td>
                        <td className="num">{money(w.cost)}</td>
                        <td className="num">{pct(w.est_prob)}</td>
                        <td className="num pos">{signedPct(w.ev)}</td>
                        <td className="hide-sm muted small">{w.rationale}</td>
                        <td>{w.grade_status ? <span className={`res ${w.grade_status}`}>{w.grade_status.replace("_", " ")} {money(w.payout)}</span> : <span className="muted">Pending</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      {!p && runners.length > 0 && (
        <section className="card">
          <h2>Entries</h2>
          <div className="table-wrap entries-desktop">
            <table className="data">
              <thead><tr><th>Program</th><th>Horse</th><th>Jockey</th><th>Trainer</th><th>Weight</th><th>Morning line</th></tr></thead>
              <tbody>{runners.map((r) => (
                <tr key={r.program_number} className={r.scratched ? "scratched" : ""}><td><HorseNumber number={r.program_number} /></td><td>{r.horse_name}</td><td>{r.jockey_name ?? "—"}</td><td>{r.trainer_name ?? "—"}</td><td>{r.weight ?? "—"}</td><td>{odds(r.ml_odds)}</td></tr>
              ))}</tbody>
            </table>
          </div>
          <ul className="entries-mobile">
            {runners.map((r) => (
              <li key={r.program_number} className={r.scratched ? "scratched" : ""}>
                <HorseNumber number={r.program_number} />
                <div className="entry-info">
                  <strong>{r.horse_name}</strong>
                  <dl className="entry-connections">
                    <div><dt>Jockey</dt><dd>{r.jockey_name ?? "—"}</dd></div>
                    <div><dt>Trainer</dt><dd>{r.trainer_name ?? "—"}</dd></div>
                    {r.weight != null && <div><dt>Weight</dt><dd>{r.weight} lb</dd></div>}
                  </dl>
                </div>
                <div className="entry-price"><strong>{odds(r.ml_odds)}</strong><small>ML</small></div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {Boolean(data.docNotes?.length) && (
        <section className="card doc-notes">
          <h2>Imported Racing Data</h2>
          {data.docNotes?.map((n) => (
            <details key={n.id}>
              <summary>{n.horse_name ? <><HorseNumber number={n.program_number} /> {n.horse_name} · </> : ""}{n.kind}</summary>
              <p className="small mono pre">{n.text}</p>
              <a className="small" href={`/api/documents/${n.document_id}/file`} target="_blank" rel="noopener">{n.filename}</a>
            </details>
          ))}
        </section>
      )}

      {results.length > 0 && (
        <section className="card">
          <h2>Official Results</h2>
          {data.grade && (
            <p>
              Rein Man's top pick finished <b>{data.grade.top1_finish ? ordinal(data.grade.top1_finish) : "unplaced"}</b>
              {data.grade.winner_rank ? <> · winner was ranked <b>{ordinal(data.grade.winner_rank)}</b> by Rein Man</> : null}
              {data.grade.status !== "graded" ? <span className="muted"> ({data.grade.status.replace("_", " ")})</span> : null}
            </p>
          )}
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Fin</th><th>#</th><th>Horse</th><th className="num">Win</th><th className="num">Place</th><th className="num">Show</th></tr></thead>
              <tbody>
                {results.map((f, i) => (
                  <tr key={i} className={f.program_number && [p?.top1, p?.top2, p?.top3].includes(f.program_number) ? "is-pick" : ""}>
                    <td>{f.finish_position}{f.dead_heat ? " DH" : ""}</td><td><HorseNumber number={f.program_number} /></td><td>{f.horse_name}</td>
                    <td className="num">{money(f.win_payoff)}</td><td className="num">{money(f.place_payoff)}</td><td className="num">{money(f.show_payoff)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.payoffs.length > 0 && (
            <p className="small">
              {data.payoffs.filter((x) => x.payoff_amount).map((x, i) => (
                <span key={i} className="payoff">{x.wager_name || wagerLabel(x.wager_type)} {x.winning_numbers}: {money(x.payoff_amount)}</span>
              ))}
            </p>
          )}
        </section>
      )}

      {(data.snapshots.length > 0 || data.changes.length > 0) && (
        <section className="card">
          <h2>Audit Trail</h2>
          <p className="muted small">Every snapshot is permanent. Only the final snapshot recorded before post time is graded.</p>
          <ul className="timeline">
            {data.snapshots.map((s) => (
              <li key={s.prediction_id}>
                <button className="linkish" onClick={() => setSnap(s.prediction_id)}>{stamp(s.created_at)}</button>
                {" "}— {s.reason} · top pick <HorseNumber number={s.top1} /> · {s.confidence} · {s.decision}
                {data.lock?.prediction_id === s.prediction_id && <span className="locked"> 🔒 official</span>}
              </li>
            ))}
            {data.changes.map((c, i) => (
              <li key={`c${i}`} className="muted">{stamp(c.seen_at)} — {c.change_type ? `${c.change_type}: ` : ""}{c.text}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function Bar({ v }: { v: number }) {
  return <span className="pbar" aria-hidden="true"><span style={{ width: `${Math.min(100, v * 100 / 0.6)}%` }} /></span>;
}
