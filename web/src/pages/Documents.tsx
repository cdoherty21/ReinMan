import { useEffect, useState } from "react";
import { Link } from "../main";
import { api, stamp, todayET, useApi } from "../lib";
import { expandArchive, MAX_FILE_BYTES } from "../archives";
import { extractText } from "../extract";
import { Empty, ErrorBox, Loading } from "./common";

type Summary = {
  historical_import?: boolean; winner_records?: number; rejected_rows?: number;
  races_linked?: string[]; horses_matched?: number; workouts?: number; workouts_applied_to_cards?: number;
  payouts?: number; scratch_mentions?: number; canonical_import?: boolean;
  cards?: number; official?: number; race_cards_imported?: string[]; waiting_for_race?: number; message?: string; partial_missing_races?: number[]; partial_conflicts?: number[];
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

  const awaitingScan = data?.rows.some((doc) => doc.status === "pending") ?? false;
  useEffect(() => {
    if (!awaitingScan) return;
    const timer = window.setInterval(reload, 5000);
    return () => window.clearInterval(timer);
  }, [awaitingScan, reload]);

  async function upload() {
    setBusy(true);
    const out: string[] = [];
    let uploaded = false;
    async function uploadFile(f: File) {
      try {
        if (f.size > MAX_FILE_BYTES) throw new Error("Original file exceeds 8 MB.");
        const { text, pages } = await extractText(f).catch(async (error) => {
          if (!/\.pdf$/i.test(f.name) && f.type !== "application/pdf"
            && await f.slice(0, 5).text() !== "%PDF-") throw error;
          return { text: "", pages: undefined };
        });
        const original = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onerror = () => reject(reader.error ?? new Error("Could not read the file."));
          reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
          reader.readAsDataURL(f);
        });
        const { duplicate } = await api<{ duplicate?: boolean }>("/api/documents", {
          method: "POST",
          body: JSON.stringify({
            filename: f.name, mime: f.type, size_bytes: f.size, text, original,
            race_date: raceDate || undefined, track_id: track.trim() || undefined,
          }),
        });
        uploaded = true;
        out.push(duplicate ? `↪ ${f.name} — Already uploaded` :
          `✓ ${f.name} — ${text ? `${text.length.toLocaleString()} characters${pages ? ` from ${pages} pages` : ""}` : "saved for extraction"}`);
      } catch (e) {
        out.push(`✗ ${f.name} — ${(e as Error).message}`);
      }
      setLog([...out]);
    }
    for (const file of files) {
      const archive = /\.zip$/i.test(file.name) || (!/\.(docx|xlsx)$/i.test(file.name)
        && ["application/zip", "application/x-zip-compressed"].includes(file.type));
      if (!archive) {
        await uploadFile(file);
        continue;
      }
      try {
        let found = false;
        for await (const entry of expandArchive(file)) {
          found = true;
          if (typeof entry !== "string") await uploadFile(entry);
          else {
            out.push(`✗ ${entry}`);
            setLog([...out]);
          }
        }
        if (!found) out.push(`✗ ${file.name} — No supported documents in ZIP.`);
      } catch (error) {
        out.push(`✗ ${file.name} — ${(error as Error).message}`);
      }
      setLog([...out]);
    }
    if (uploaded) {
      try {
        const { started } = await api<{ started: boolean }>("/api/documents/scan", { method: "POST", body: "{}" });
        setLog([...out, started ? "Automatic scan requested." : "The scheduled scanner will pick up these files."]);
      } catch {
        setLog([...out, "The scheduled scanner will pick up these files."]);
      }
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
          <p className="muted">Upload race programs, past performances, results and notes.</p>
        </div>
      </div>

      <CollectorStatus />
      <section className="card">
        <h2>Upload racing data</h2>
        <p className="small muted">
          Upload PDFs, images, Word or Excel files, HTML, TXT, CSV, or Rein Man JSON up to 8 MB each. You can also upload a ZIP up to 64 MB. Rein Man extracts useful racing details
          and builds race cards from supported programs. Linked evidence appears on race pages.
        </p>
        <div className="upload-form">
          <input type="file" multiple accept=".zip,.pdf,.html,.htm,.txt,.csv,.tsv,.json,.docx,.xlsx,.png,.jpg,.jpeg,.webp,.tif,.tiff,image/*,application/pdf,text/*"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))} aria-label="Choose documents" />
          <label>Race date (optional) <input type="date" value={raceDate} max={undefined} onChange={(e) => setRaceDate(e.target.value)} /></label>
          <label>Track code (optional) <input value={track} placeholder="e.g. FL…" maxLength={8} size={6} onChange={(e) => setTrack(e.target.value)} /></label>
          <button className="btn primary" disabled={busy || files.length === 0} onClick={upload}>
            {busy ? "Reading…" : `Upload ${files.length || ""} ${files.length === 1 ? "file" : "files"}`}
          </button>
        </div>
        <div aria-live="polite">{log.length > 0 && <ul className="timeline small">{log.map((l, i) => <li key={i}>{l}</li>)}</ul>}</div>
        {raceDate && raceDate > todayET() && <p className="small muted">Uploading ahead of race day is ideal: workouts reach the picks before post.</p>}
      </section>

      <section className="card">
        <h2>Document library</h2>
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
          {d.status === "pending" ? <span className="muted">Awaiting automatic scan</span> :
            d.status === "waiting" ? <span className="muted">Waiting for the race card</span> :
            d.status === "error" ? <span className="neg">{d.error || "Scan failed"}</span> : <span className="pos">{s?.historical_import ? "Historical data recognized" : "Scanned"}</span>}
        </td>
        <td className="small">{s?.message || found || "—"}</td>
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
      {races.length === 0 && !summary?.canonical_import && !summary?.historical_import && (
        <p className="small muted">No stored race matched this document's horse names. Set the race date and track when uploading, or upload once the card is loaded.</p>
      )}
      {Boolean(summary?.partial_missing_races?.length) &&
        <p className="small muted">Full cards still needed for races {summary?.partial_missing_races?.join(", ")}.</p>}
      {Boolean(summary?.partial_conflicts?.length) &&
        <p className="small muted">Some runner entries differed from the complete cards for races {summary?.partial_conflicts?.join(", ")}.</p>}
      <ul className="factors small">
        {data.notes.filter((n) => n.kind !== "passage").map((n) => (
          <li key={n.id}><b>{n.kind}</b>{n.horse_name ? ` · ${n.horse_name}` : ""}: <span className="mono">{n.text}</span></li>
        ))}
      </ul>
    </div>
  );
}



function CollectorStatus() {
  const { data, error } = useApi<{counts: {discovered: number; downloaded: number; processed: number; errors: number};
    sources: {url: string; status: string; error: string}[];
    last: {finished_at: string; details: string} | null; horses: {total: number}; credits: {firecrawl_credits_reserved?: number} | null}>("/api/free-pp");
  return <section className="card">
    <h2>Free past performance collector</h2>
    {error && <ErrorBox error={error} />}
    {!data && !error && <Loading />}
    {data && <>
      <p className="small">{data.counts.discovered} URLs discovered · {data.counts.downloaded} documents downloaded · {data.counts.processed} processed · {data.counts.errors} source errors · {data.horses.total} horses in historical records</p>
      <p className="small">Firecrawl page credits reserved last run: {data.credits?.firecrawl_credits_reserved ?? 0}</p>
      {data.last && <p className="small muted">Last collection {stamp(data.last.finished_at)}</p>}
      {data.sources.length > 0 && <ul className="small">{data.sources.map(source =>
        <li key={source.url}>{source.url} · {source.error}</li>)}</ul>}
    </>}
  </section>;
}
