import { useState } from "react";
import { money, pct } from "../lib";

// Series colors validated for the cream chart surface (#fbf8f0); the pair sits in the
// CVD 6–8 ΔE band, so the second series also carries a dash pattern and direct labels.
const SERIES = [
  { color: "#2a7a4b", dash: undefined as string | undefined },
  { color: "#b07a14", dash: "6 4" },
];

type Pt = { date: string; [k: string]: number | string };

export function PLChart({ data, series }: { data: Pt[]; series: { key: string; label: string }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (data.length < 2) return <p className="muted">The profit/loss chart appears after two days of graded races.</p>;
  const W = 720, H = 260, L = 64, R = 120, T = 14, B = 28;
  const vals = data.flatMap((d) => series.map((s) => Number(d[s.key])));
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  if (hi - lo < 1) { hi += 1; lo -= 1; }
  const x = (i: number) => L + (i * (W - L - R)) / (data.length - 1);
  const y = (v: number) => T + ((hi - v) * (H - T - B)) / (hi - lo);
  const ticks = niceTicks(lo, hi, 5);
  const path = (k: string) => data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(Number(d[k])).toFixed(1)}`).join("");
  const last = data[data.length - 1];
  const labelY = series.map((s) => y(Number(last[s.key])) + 4);
  if (labelY.length === 2 && Math.abs(labelY[0] - labelY[1]) < 14) {
    const up = labelY[0] <= labelY[1] ? 0 : 1;
    labelY[up] -= 7;
    labelY[1 - up] += 7;
  }
  const xTicks = [0, Math.floor((data.length - 1) / 2), data.length - 1];
  return (
    <figure className="chart">
      <div className="legend">
        {series.map((s, i) => (
          <span key={s.key}><svg width="22" height="8"><line x1="0" y1="4" x2="22" y2="4" stroke={SERIES[i].color} strokeWidth="2" strokeDasharray={SERIES[i].dash} /></svg>{s.label}</span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cumulative profit and loss by race date"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          const i = Math.round(((px - L) / (W - L - R)) * (data.length - 1));
          setHover(Math.max(0, Math.min(data.length - 1, i)));
        }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className={t === 0 ? "zero" : "grid"} />
            <text x={L - 8} y={y(t) + 4} textAnchor="end" className="axis">{money(t).replace(".00", "")}</text>
          </g>
        ))}
        {xTicks.map((i) => (
          <text key={i} x={x(i)} y={H - 8} textAnchor="middle" className="axis">{data[i].date.slice(5)}</text>
        ))}
        {series.map((s, i) => (
          <g key={s.key}>
            <path d={path(s.key)} fill="none" stroke={SERIES[i].color} strokeWidth="2" strokeDasharray={SERIES[i].dash} strokeLinejoin="round" />
            <text x={x(data.length - 1) + 8} y={labelY[i]} className="direct">
              {s.label.split(" ")[0]} {money(Number(data[data.length - 1][s.key]))}
            </text>
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
        <rect x={L} y={T} width={W - L - R} height={H - T - B} fill="transparent" />
      </svg>
      {hover != null && (
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%` }}>
          <b>{data[hover].date}</b>
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
