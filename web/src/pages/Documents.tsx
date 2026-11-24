import { useState } from "react";
import { Link } from "../main";
import { api, stamp, todayET, useApi } from "../lib";
import { extractText } from "../extract";
import { Empty, ErrorBox, Loading } from "./common";

type Summary = {
  races_linked?: string[]; horses_matched?: number; workouts?: number; workouts_applied_to_cards?: number;
  payouts?: number; scratch_mentions?: number; canonical_import?: boolean;
  cards?: number; official?: number; race_cards_imported?: string[]; waiting_for_race?: number;
};
type Doc = {
  id: number; uploaded_at: string; filename: string; chars: number; race_date: string | null; track_id: string | null;
  status: string; processed_at: string | null; summary: Summary | null; error: string | null;
};
type Note = { id: number; race_id: string | null; program_number: string | null; horse_name: string | null; kind: string; text: string };

export function Documents() {
  const { data, error, reload } = useApi<{ rows: Doc[] }>("/api/documents");
  const [files, setFiles] = useState<File[]>([]);
  const [raceDate, setRaceDate] = useState("");
  const [track, setTrack] = useState("");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [open, setOpen] = useState<number | null>(null);

  async function upload() {
    setBusy(true);
    const out: string[] = [];
    for (const f of files) {
      try {
        if (f.size > 8 * 1024 * 1024) throw new Error("Original file exceeds 8 MB.");
        const { text, pages } = await extractText(f);
        const original = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onerror = () => reject(reader.error ?? new Error("Could not read the file."));
          reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
          reader.readAsDataURL(f);
        });
        await api("/api/documents", {
          method: "POST",
          body: JSON.stringify({
            filename: f.name, mime: f.type, size_bytes: f.size, text, original,
            race_date: raceDate || undefined, track_id: track.trim() || undefined,
          }),
        });
        out.push(`✓ ${f.name} — ${text.length.toLocaleString()} characters${pages ? ` from ${pages} pages` : ""}`);
      } catch (e) {
        out.push(`✗ ${f.name} — ${(e as Error).message}`);
      }
      setLog([...out]);
    }
    setFiles([]);
    setBusy(false);
    reload();
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Documents</h1>
          <p className="muted">Give Rein Man more to read: programs, past performances, result pages, notes.</p>
        </div>
      </div>

      <section className="card">
        <h2>Upload</h2>
        <p className="small muted">
          Upload text-based PDFs, HTML, TXT, CSV, or Rein Man JSON up to 8 MB. Rein Man extracts useful racing details
          and builds race cards from supported programs. Linked evidence appears on race pages.
        </p>
        <div className="upload-form">
          <input type="file" multiple accept=".pdf,.html,.htm,.txt,.csv,.json,application/pdf,text/*"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))} aria-label="Choose documents" />
          <label>Race date (optional) <input type="date" value={raceDate} max={undefined} onChange={(e) => setRaceDate(e.target.value)} /></label>
          <label>Track code (optional) <input value={track} placeholder="e.g. FL" maxLength={8} size={6} onChange={(e) => setTrack(e.target.value)} /></label>
          <button className="btn primary" disabled={busy || files.length === 0} onClick={upload}>
            {busy ? "Reading…" : `Upload ${files.length || ""} ${files.length === 1 ? "file" : "files"}`}
          </button>
        </div>
        {log.length > 0 && <ul className="timeline small">{log.map((l, i) => <li key={i}>{l}</li>)}</ul>}
        {raceDate && raceDate > todayET() && <p className="small muted">Uploading ahead of race day is ideal: workouts reach the picks before post.</p>}
      </section>

      <section className="card">
        <h2>Uploaded</h2>
        {error && <ErrorBox error={error} />}
        {!data && !error && <Loading />}
        {data && data.rows.length === 0 && <Empty>No documents yet.</Empty>}
        {data && data.rows.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Uploaded</th><th>File</th><th>Status</th><th>Found</th><th></th></tr></thead>
              <tbody>
                {data.rows.map((d) => (
                  <DocRow key={d.id} d={d} open={open === d.id} onToggle={() => setOpen(open === d.id ? null : d.id)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function DocRow({ d, open, onToggle }: { d: Doc; open: boolean; onToggle: () => void }) {
  const s = d.summary;
  const found = !s || s.waiting_for_race ? null : s.canonical_import
    ? `Imported ${s.cards} card(s), ${s.official} official result(s)`
    : `${s.races_linked?.length ?? 0} race(s), ${s.race_cards_imported?.length ?? 0} imported, ${s.horses_matched ?? 0} horses, ${s.workouts ?? 0} workouts, ${s.payouts ?? 0} payouts`;
  return (
    <>
      <tr>
        <td className="small">{stamp(d.uploaded_at)}</td>
        <td><a href={`/api/documents/${d.id}/file`} target="_blank" rel="noopener">{d.filename}</a></td>
        <td>
          {d.status === "pending" ? <span className="muted">Waiting for next scan</span> :
            d.status === "waiting" ? <span className="muted">Waiting for the race card</span> :
            d.status === "error" ? <span className="neg" title={d.error ?? ""}>Error</span> : <span className="pos">Scanned</span>}
        </td>
        <td className="small">{found ?? "—"}</td>
        <td><a className="btn ghost small" href={`/api/documents/${d.id}/file?download`}>Download</a>{d.status === "processed" && <button className="btn ghost small" aria-expanded={open} onClick={onToggle}>{open ? "Hide" : "Details"}</button>}</td>
      </tr>
      {open && <tr className="analysis"><td colSpan={5}><DocDetail id={d.id} summary={s} /></td></tr>}
    </>
  );
}

function DocDetail({ id, summary }: { id: number; summary: Summary | null }) {
  const { data, error } = useApi<{ notes: Note[] }>(`/api/documents/${id}`);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const races = summary?.races_linked ?? [];
  return (
    <div>
      {races.length > 0 && (
        <p className="small">Linked races: {races.map((r) => <Link key={r} to={`/race/${r}`} className="chip">{r}</Link>)}</p>
      )}
      {races.length === 0 && !summary?.canonical_import && (
        <p className="small muted">No stored race matched this document's horse names. Set the race date and track when uploading, or upload once the card is loaded.</p>
      )}
      <ul className="factors small">
        {data.notes.filter((n) => n.kind !== "passage").map((n) => (
          <li key={n.id}><b>{n.kind}</b>{n.horse_name ? ` · ${n.horse_name}` : ""}: <span className="mono">{n.text}</span></li>
        ))}
      </ul>
    </div>
  );
}
