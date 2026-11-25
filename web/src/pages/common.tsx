import { shiftDate, todayET } from "../lib";

export function Confidence({ level }: { level?: string | null }) {
  if (!level) return <span className="muted">—</span>;
  return <span className={`conf conf-${level.toLowerCase()}`}>{level}</span>;
}

export function Decision({ d }: { d?: string | null }) {
  if (!d) return null;
  return <span className={`decision ${d}`}>{d === "play" ? "Play" : "Pass"}</span>;
}

/** Rein Man's mark: used wherever one of his selections appears. */
export function RM({ title = "Rein Man's pick" }: { title?: string }) {
  return (
    <span className="rm-mark" title={title} aria-label={title}>
      <img src="/gentleman-64.png" alt="" width={24} height={24} />
    </span>
  );
}

export function DatePicker({ date, onChange }: { date: string; onChange: (d: string) => void }) {
  return (
    <div className="datepicker">
      <button className="btn ghost" onClick={() => onChange(shiftDate(date, -1))} aria-label="Previous day">‹</button>
      <input type="date" value={date} onChange={(e) => e.target.value && onChange(e.target.value)} aria-label="Race date" />
      <button className="btn ghost" onClick={() => onChange(shiftDate(date, 1))} aria-label="Next day">›</button>
      {date !== todayET() && (
        <button className="btn ghost" onClick={() => onChange(todayET())}>Today</button>
      )}
    </div>
  );
}

export function useDateParam(): [string, (d: string) => void] {
  const sp = new URLSearchParams(location.search);
  const date = sp.get("date") || todayET();
  return [
    date,
    (d: string) => {
      const u = new URL(location.href);
      u.searchParams.set("date", d);
      history.replaceState(null, "", u.pathname + u.search);
      window.dispatchEvent(new PopStateEvent("popstate"));
    },
  ];
}

export function Loading() {
  return <p className="muted">Studying the form…</p>;
}

export function ErrorBox({ error }: { error: string }) {
  return <p className="error" role="alert">Could not load: {error}</p>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}
