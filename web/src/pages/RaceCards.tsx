import { Link } from "../main";
import { useApi } from "../lib";
import { DatePicker, Empty, ErrorBox, Loading, useDateParam } from "./common";
import { RaceTable, type RaceRow } from "./Today";

export function RaceCards() {
  const [date, setDate] = useDateParam();
  const track = new URLSearchParams(location.search).get("track");
  const { data, error } = useApi<{ races: RaceRow[] }>(`/api/today?date=${date}`);
  const races = data?.races ?? [];
  const tracks = [...new Map(races.map((r) => [r.track_id, r.track_name || r.track_id])).entries()].sort((a, b) =>
    a[1].localeCompare(b[1]),
  );
  const shown = track ? tracks.filter(([id]) => id === track) : tracks;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Race Cards</h1>
          <p className="muted">Every race, every horse — analyzed independently of popularity.</p>
        </div>
        <DatePicker date={date} onChange={setDate} />
      </div>
      {error && <ErrorBox error={error} />}
      {!data && !error && <Loading />}
      {data && races.length === 0 && <Empty>No cards for this date yet.</Empty>}
      {tracks.length > 1 && (
        <section className="chips">
          <Link to={`/cards?date=${date}`} className={`chip ${!track ? "on" : ""}`}>All tracks</Link>
          {tracks.map(([id, name]) => (
            <Link key={id} to={`/cards?date=${date}&track=${id}`} className={`chip ${track === id ? "on" : ""}`}>{name}</Link>
          ))}
        </section>
      )}
      {shown.map(([id, name]) => (
        <section className="card" key={id}>
          <h2>{name} <span className="muted small">({id})</span></h2>
          <RaceTable rows={races.filter((r) => r.track_id === id).sort((a, b) => a.race_number - b.race_number)} completed={false} />
        </section>
      ))}
    </>
  );
}
