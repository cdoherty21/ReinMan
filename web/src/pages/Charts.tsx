import { useEffect, useRef, useState } from "react";
import { fmtDate, money, pct } from "../lib";

// Series colors validated for the cream chart surface (#fbf8f0); the pair sits in the
// CVD 6–8 ΔE band, so the second series also carries a dash pattern and direct labels.
const SERIES = [
  { color: "#2a7a4b", dash: undefined as string | undefined },
  { color: "#b07a14", dash: "6 4" },
];

type Pt = { date: string; [k: string]: number | string };

const DAY = 86_400_000;
const dayNum = (d: string) => Date.parse(`${d}T00:00:00Z`) / DAY;

// Rendered at the figure's real pixel width (not a scaled viewBox) so text keeps the same
// size on a phone and a wide screen. Points sit on a calendar axis, so a dark day shows as
// a gap rather than being squeezed out.
export function PLChart({ data, series }: { data: Pt[]; series: { key: string; label: string; short: string }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [W, setW] = useState(720);
  const ref = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [data.length >= 2]);
  if (data.length < 2) return <p className="muted">The profit/loss chart appears after two days of graded races.</p>;

  const narrow = W < 520;
  const H = narrow ? 220 : 260, L = narrow ? 50 : 60, R = narrow ? 12 : 128, T = 12, B = 26;
  const vals = data.flatMap((d) => series.map((s) => Number(d[s.key])));
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  if (hi - lo < 2) { hi += 1; lo -= 1; }
  const ticks = niceTicks(lo, hi, narrow ? 4 : 5);
  lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
  const pad = (hi - lo) * 0.04;
  lo -= pad; hi += pad;
  const d0 = dayNum(data[0].date), span = Math.max(1, dayNum(data[data.length - 1].date) - d0);
  const x = (i: number) => L + ((dayNum(data[i].date) - d0) * (W - L - R)) / span;
  const y = (v: number) => T + ((hi - v) * (H - T - B)) / (hi - lo);
  const path = (k: string) => data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(Number(d[k])).toFixed(1)}`).join("");
  const last = data[data.length - 1];

  // Date labels: as many as fit without crowding, always the first and the last.
  const xTicks: number[] = [];
  const gap = 72;
  const n = data.length - 1;
  const at = (i: number) => x(i) + (i === 0 ? 24 : i === n ? -24 : 0); // first and last labels are edge-aligned
  data.forEach((_, i) => {
    if (i === n) {
      while (xTicks.length > 1 && at(i) - at(xTicks[xTicks.length - 1]) < gap) xTicks.pop();
      xTicks.push(i);
    } else if (!xTicks.length || at(i) - at(xTicks[xTicks.length - 1]) >= gap) xTicks.push(i);
  });

  const labelY = series.map((s) => y(Number(last[s.key])) + 4);
  if (labelY.length === 2 && Math.abs(labelY[0] - labelY[1]) < 15) {
    const up = labelY[0] <= labelY[1] ? 0 : 1;
    const mid = (labelY[0] + labelY[1]) / 2;
    labelY[up] = mid - 7.5;
    labelY[1 - up] = mid + 7.5;
  }
  const money0 = (v: number) => money(v).replace(".00", "");
  const pick = (clientX: number, el: Element) => {
    const r = el.getBoundingClientRect();
    const px = clientX - r.left;
    let best = 0;
    data.forEach((_, i) => { if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i; });
    setHover(best);
  };
  // Tooltip beside the crosshair, on the side with more room, so it never hides the point.
  const tip = hover == null ? {} : x(hover) > (L + W - R) / 2 ? { right: `${W - x(hover) + 10}px` } : { left: `${x(hover) + 10}px` };
  return (
    <figure className="chart" ref={ref}>
      <div className="legend">
        {series.map((s, i) => (
          <span key={s.key}><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke={SERIES[i].color} strokeWidth="2" strokeDasharray={SERIES[i].dash} /></svg>
            {s.label}{narrow && <b className="legend-val">{money(Number(last[s.key]))}</b>}</span>
        ))}
      </div>
      <svg width={W} height={H} role="img" aria-label="Cumulative profit and loss by race date" style={{ touchAction: "pan-y" }}
        onPointerLeave={() => setHover(null)}
        onPointerDown={(e) => pick(e.clientX, e.currentTarget)}
        onPointerMove={(e) => pick(e.clientX, e.currentTarget)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className={t === 0 ? "zero" : "grid"} />
            <text x={L - 8} y={y(t) + 4} textAnchor="end" className="axis">{money0(t)}</text>
          </g>
        ))}
        {xTicks.map((i, k) => (
          <text key={i} x={x(i)} y={H - 6} className="axis"
            textAnchor={k === 0 ? "start" : k === xTicks.length - 1 ? "end" : "middle"}>{fmtDate(data[i].date, true)}</text>
        ))}
        {series.map((s, i) => (
          <g key={s.key}>
            <path d={path(s.key)} fill="none" stroke={SERIES[i].color} strokeWidth="2" strokeDasharray={SERIES[i].dash} strokeLinejoin="round" strokeLinecap="round" />
            {data.length <= 12 && data.map((d, j) => (
              <circle key={j} cx={x(j)} cy={y(Number(d[s.key]))} r="2.5" fill={SERIES[i].color} />
            ))}
            {!narrow && (
              <text x={x(data.length - 1) + 8} y={labelY[i]} className="direct">
                {s.short} {money(Number(last[s.key]))}
              </text>
            )}
          </g>
        ))}
        {hover != null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} className="crosshair" />
            {series.map((s, i) => (
              <circle key={s.key} cx={x(hover)} cy={y(Number(data[hover][s.key]))} r="4.5" fill={SERIES[i].color} stroke="#fbf8f0" strokeWidth="2" />
            ))}
          </g>
        )}
      </svg>
      {hover != null && (
        <div className="tooltip side" style={tip}>
          <b>{fmtDate(data[hover].date)}</b>
          {series.map((s) => <div key={s.key}>{s.label}: {money(Number(data[hover][s.key]))}</div>)}
        </div>
      )}
    </figure>
  );
}

export function CalibrationChart({ bins }: { bins: { predicted: number; actual: number; n: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!bins.length) return <p className="muted">Calibration appears once predictions are graded.</p>;
  const S = 260, P = 40;
  const max = 0.6;
  const sx = (v: number) => P + (Math.min(v, max) / max) * (S - P - 10);
  const sy = (v: number) => S - P - (Math.min(v, max) / max) * (S - P - 10);
  return (
    <figure className="chart square">
      <svg viewBox={`0 0 ${S} ${S}`} role="img" aria-label="Predicted versus actual win rate">
        {[0, 0.2, 0.4, 0.6].map((t) => (
          <g key={t}>
            <line x1={sx(0)} x2={sx(max)} y1={sy(t)} y2={sy(t)} className="grid" />
            <text x={P - 6} y={sy(t) + 4} textAnchor="end" className="axis">{t * 100}%</text>
            <text x={sx(t)} y={S - P + 16} textAnchor="middle" className="axis">{t * 100}%</text>
          </g>
        ))}
        <line x1={sx(0)} y1={sy(0)} x2={sx(max)} y2={sy(max)} className="diag" />
        <text x={sx(0.36)} y={sy(0.42)} className="axis" transform={`rotate(-45 ${sx(0.36)} ${sy(0.42)})`}>perfect calibration</text>
        {bins.map((b, i) => (
          <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <circle cx={sx(b.predicted)} cy={sy(b.actual)} r="14" fill="transparent" />
            <circle cx={sx(b.predicted)} cy={sy(b.actual)} r={Math.max(4, Math.min(9, Math.sqrt(b.n) / 3))} fill="#2a7a4b" stroke="#fbf8f0" strokeWidth="2" />
          </g>
        ))}
        <text x={S / 2 + 10} y={S - 4} textAnchor="middle" className="axis">Predicted win %</text>
      </svg>
      {hover != null && (
        <div className="tooltip static">
          Predicted {pct(bins[hover].predicted)} · Actual {pct(bins[hover].actual)} · {bins[hover].n} runners
        </div>
      )}
    </figure>
  );
}

function niceTicks(lo: number, hi: number, n: number): number[] {
  const span = hi - lo;
  const step0 = span / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => span / s <= n) ?? 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 100) / 100);
  return out;
}
