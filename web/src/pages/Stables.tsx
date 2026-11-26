import { useEffect, useState } from "react";
import { api, useApi } from "../lib";
import { Link } from "../main";
import { ErrorBox, Loading } from "./common";

type Horse = {
  horse_id: string; horse_name: string; trainer_name?: string; jockey_name?: string;
  sire?: string; dam?: string; dam_sire?: string; favorite: number;
  identity: Record<string, string>; image?: {thumbnail?:string;source?:string;creator?:string;license?:string;license_url?:string}; biography?: {foaling_year?:number;source?:string}; record?: {starts:number; wins:number; places:number; shows:number; recent:number[]; classification:string; confidence:string; reason:string; weakness:string};
  next?: {race_id:string; race_date:string; track_id:string; race_number:number; post_time:string; surface:string; distance_yards:number; win_prob?:number; fair_odds?:number; live_odds?:number; ml_odds?:number; verdict:string; confidence?:string; factors?:string};
  form?:{age?:number;sex?:string;sire?:string;dam?:string;country?:string;careerPrizeMoney?:number;last20Starts?:string;form?:string;stats?:Record<string,{starts?:number;wins?:number;places?:number}>};
  evidence?:string[]; workouts?:Record<string,unknown>[]; pastPerformances?:Record<string,unknown>[];
  opponents?: {horse_name:string}[];
  preferences?: Record<string,{starts:number;itm:number}>;
  history?: {race_id:string; race_date:string; track_id:string; race_number:number; surface:string; distance_yards:number; finish_position:number|null; scratched:number; jockey_name:string; trainer_name:string; medication?:string; equipment?:string; details?:Record<string,unknown>}[];
  news?: {title:string;source:string;url:string;published_at:string;kind:string;sentence:string}[];
};
type Directory = {horses:Horse[];after:string|null};
const href = (horse: Horse) => "/stables/" + encodeURIComponent(horse.horse_id);
function Portrait({horse}:{horse:Horse}) {
  const [failed,setFailed]=useState(false);
  if(!failed && horse.image?.thumbnail?.startsWith("https://upload.wikimedia.org/")) return <figure className="stable-photo"><img loading="lazy" src={horse.image.thumbnail} alt={horse.horse_name} onError={()=>setFailed(true)}/><figcaption><a href={horse.image.source} target="_blank" rel="noreferrer">{horse.image.creator}</a> · <a href={horse.image.license_url || horse.image.source} target="_blank" rel="noreferrer">{horse.image.license}</a></figcaption></figure>;
  return <div className="stable-portrait" role="img" aria-label="Photograph unavailable"><svg aria-hidden="true" viewBox="0 0 80 80" width="72" height="72"><path fill="currentColor" d="M19 65h44v5H19zm7-4 4-21-10 3-7-8 20-21 3-10 7 10c17 3 23 19 17 35l-3 12z"/><circle cx="37" cy="23" r="2" fill="#173022"/></svg><span>Photograph unavailable</span></div>;
}
function NextRace({horse}:{horse:Horse}) {
  const next=horse.next;
  return next ? <div className="stable-next"><strong>{next.verdict}</strong><p><Link to={"/race/"+encodeURIComponent(next.race_id)}>{next.track_id} · Race {next.race_number} · {next.race_date}</Link></p><p className="small">{new Date(next.post_time).toLocaleString()} · {next.surface || "Surface unknown"}</p><p className="small">Win probability {next.win_prob == null ? "—" : (next.win_prob*100).toFixed(1)+"%"} · Fair odds {next.fair_odds?.toFixed(2) ?? "—"} · Live odds {next.live_odds?.toFixed(2) ?? "—"} {next.live_odds==null && next.ml_odds!=null ? "· Morning line "+next.ml_odds : ""}</p>
  {horse.opponents?.length ? <p className="small">Opponents: {horse.opponents.map(o=>o.horse_name).join(", ")}</p>:null}</div> : <p className="muted">Awaiting next entry.</p>;
}
function HorseCard({horse,featured=false,selected,onSelect}:{horse:Horse;featured?:boolean;selected?:boolean;onSelect?:(horse:Horse)=>void}) {
  return <article className="card stable-card"><Portrait horse={horse}/><div className="stable-card-body"><h2><Link to={href(horse)}>{horse.horse_name}</Link></h2><p className="muted small">{[horse.identity.age_sex || (horse.form?.age && horse.form?.sex ? horse.form.age+" · "+horse.form.sex : ""),horse.trainer_name].filter(Boolean).join(" · ") || "Connections unknown"}</p>
    {horse.form?.last20Starts && <p className="small">Form {horse.form.last20Starts}</p>}
    {featured && horse.record && <><p className="stable-class">{horse.record.classification}</p><p>{horse.record.reason}</p><p className="small muted">{horse.record.weakness}</p><p className="small">Recorded form {horse.record.recent.join(" – ") || "—"} · {horse.record.confidence} evidence</p><NextRace horse={horse}/></>}
    <div className="stable-controls"><Link to={href(horse)}>View profile →</Link>{onSelect && <button className="btn ghost small" onClick={()=>onSelect(horse)}>{selected?"Remove":"Compare"}</button>}</div>
  </div></article>;
}
function Profile({id}:{id:string}) {
  const {data:horse,error,reload}=useApi<Horse>("/api/stables/horse/"+encodeURIComponent(id));
  const [busy,setBusy]=useState(false),[saveError,setSaveError]=useState("");
  if(error)return <ErrorBox error={error}/>;if(!horse)return <Loading/>;
  const r=horse.record;
  return <><p className="crumb"><Link to="/stables">← The Stables</Link></p><div className="card stable-profile"><Portrait horse={horse}/><div><h1>{horse.horse_name}</h1><p className="muted">{horse.identity.age_sex || (horse.form?.age && horse.form?.sex ? horse.form.age+" · "+horse.form.sex : "Age and sex unknown")} · {horse.trainer_name || "Trainer unknown"}</p><button className="btn primary" disabled={busy} onClick={async()=>{setBusy(true);setSaveError("");try{await api("/api/stables/horse/"+encodeURIComponent(id)+"/favorite",{method:"POST",body:JSON.stringify({favorite:!horse.favorite})});reload();}catch(e){setSaveError(String(e));}finally{setBusy(false);}}}>{horse.favorite?"★ Remove from watchlist":"☆ Add to watchlist"}</button>{saveError&&<ErrorBox error={saveError}/>}</div></div>
    <section className="card"><h2>Rein Man’s Assessment</h2><p className="stable-class">{r?.classification} · {r?.confidence} evidence</p><p>{r?.reason}</p><p>{r?.weakness}</p>{horse.evidence?.length?<details><summary>Recorded model evidence</summary><ul>{horse.evidence.map((e,i)=><li key={i}>{e}</li>)}</ul></details>:null}</section>
    <section className="card"><h2>Upcoming race</h2><NextRace horse={horse}/></section>
    <div className="two-col"><section className="card"><h2>Biography</h2><dl className="stable-facts">{Object.entries({Trainer:horse.trainer_name,Jockey:horse.jockey_name,Owner:horse.identity.owner,Breeder:horse.identity.breeder,"Foaling year":horse.biography?.foaling_year,"Age / sex":horse.identity.age_sex}).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v||"Unknown"}</dd></div>)}</dl></section><section className="card"><h2>Pedigree</h2><div className="stable-pedigree"><strong>{horse.horse_name}</strong><div><span>Sire · {horse.sire||horse.form?.sire||"Unknown"}</span><span>Dam · {horse.dam||horse.form?.dam||"Unknown"}</span><span>Damsire · {horse.dam_sire||"Unknown"}</span></div></div></section></div>
    <section className="card"><h2>Recorded career statistics</h2><div className="stable-stats">{Object.entries({Starts:r?.starts,Wins:r?.wins,Places:r?.places,Shows:r?.shows,"Win rate":r?.starts?Math.round(r.wins/r.starts*100)+"%":"—","Top three":r?.starts?Math.round((r.wins+r.places+r.shows)/r.starts*100)+"%":"—"}).map(([k,v])=><div key={k}><strong>{v??"—"}</strong><span>{k}</span></div>)}</div></section>
    {horse.form?.stats?.overall && <section className="card"><h2>Published career record</h2><p>FormFav · {horse.form.stats.overall.starts??"Unknown"} starts · {horse.form.stats.overall.wins??"Unknown"} wins · {horse.form.stats.overall.places??"Unknown"} reported places</p>{horse.form.careerPrizeMoney != null && <p>Career prize money (reported): {horse.form.careerPrizeMoney.toLocaleString()}</p>}</section>}
    <section className="card"><h2>Racing preferences</h2><p className="small muted">Recorded top-three finishes by condition. Small samples are preliminary.</p>{Object.entries(horse.preferences||{}).map(([k,v])=><div className="stable-bar" key={k}><span>{k}</span><progress value={v.itm} max={v.starts}/><span>{v.itm}/{v.starts}</span></div>)}</section>
    <section className="card"><h2>Race history</h2>{horse.history?.length?<div className="stable-table"><table><thead><tr>{["Date","Track","Race","Surface","Distance","Finish","Jockey","Medication","Equipment"].map(k=><th key={k}>{k}</th>)}</tr></thead><tbody>{horse.history.map(h=><tr key={h.race_id}><td>{h.race_date}</td><td><Link to={"/race/"+encodeURIComponent(h.race_id)}>{h.track_id}</Link></td><td>{h.race_number}</td><td>{h.surface||"—"}</td><td>{h.distance_yards?h.distance_yards/220+"f":"—"}</td><td>{h.scratched?"Scratched":h.finish_position??"—"}</td><td>{h.jockey_name||"—"}</td><td>{h.medication||"Unknown"}</td><td>{h.equipment||"Unknown"}</td></tr>)}</tbody></table></div>:<p>Awaiting recorded races.</p>}</section>
    <section className="card"><h2>Imported past performances</h2>{horse.pastPerformances?.length?<ul>{horse.pastPerformances.map((p,i)=><li key={i}>{String(p.date)} · {String(p.track)} · Finish {String(p.finish)} {p.comment ? " · "+String(p.comment):""}</li>)}</ul>:<p>No additional running lines recorded.</p>}</section>
    <section className="card"><h2>Workouts and training</h2>{horse.workouts?.length?<ul>{horse.workouts.map((w,i)=><li key={i}>{[w.date,w.track,w.distance_yards?w.distance_yards+" yards":null,w.time?w.time+" sec":null,w.rank,w.bullet?"Bullet":null].filter(Boolean).map(String).join(" · ")}</li>)}</ul>:<p>Awaiting verified workouts.</p>}</section>
    <section className="card"><h2>News and intelligence</h2>{horse.news?.length?horse.news.map((n,i)=><article key={i}><h3><a href={/^https?:\/\//.test(n.url)?n.url:undefined} target="_blank" rel="noreferrer">{n.title}</a></h3><p className="small muted">{n.source} · {n.published_at} · Reported {n.kind}</p><p>{n.sentence}</p></article>):<p>Awaiting verified horse-specific reports.</p>}</section>
  </>;
}
function Compare({horse}:{horse:Horse}) {
  const {data}=useApi<Horse>("/api/stables/horse/"+encodeURIComponent(horse.horse_id));
  return <article className="card"><h2><Link to={href(horse)}>{horse.horse_name}</Link></h2>{data?.record?<><p>{data.record.starts} starts · {data.record.wins} wins</p><p>Form {data.record.recent.join(" – ")}</p><p>{data.record.reason}</p><NextRace horse={data}/></>:<Loading/>}</article>;
}
function DirectoryPage() {
  const counts=useApi<{indexed:number;enriched:number;photographs:number}>("/api/stables/counts");
  const [surface,setSurface]=useState(""),[age,setAge]=useState(""),[sex,setSex]=useState("");
  const notifications=useApi<{event_id:string;horse_id:string;horse_name:string;title:string;date:string}[]>("/api/stables/notifications");
  const [q,setQ]=useState(""),[term,setTerm]=useState(""),[favorite,setFavorite]=useState(false),[upcoming,setUpcoming]=useState(false),[after,setAfter]=useState(""),[previous,setPrevious]=useState<string[]>([]),[selected,setSelected]=useState<Horse[]>([]);
  useEffect(()=>{const t=setTimeout(()=>{setTerm(q);setAfter("");setPrevious([])},300);return()=>clearTimeout(t)},[q]);
  const {data:featured,error:featureError}=useApi<Horse[]>("/api/stables/featured");
  const {data,error}=useApi<Directory>("/api/stables?"+new URLSearchParams({q:term,after,favorites:favorite?"1":"0",upcoming:upcoming?"1":"0",surface,age,sex}));
  const toggle=(horse:Horse)=>setSelected(old=>old.some(h=>h.horse_id===horse.horse_id)?old.filter(h=>h.horse_id!==horse.horse_id):old.length<4?[...old,horse]:old);
  const reset=()=>{setAfter("");setPrevious([])};
  return <><div className="stable-hero"><h1>The Stables</h1><p>Rein Man’s thoroughbred intelligence library.</p></div><h2>Rein Man’s Horses to Watch</h2>{featureError?<ErrorBox error={featureError}/>:featured?<div className="stable-grid featured">{featured.length?featured.map(h=><HorseCard key={h.horse_id} horse={h} featured/>):<p>Awaiting confirmed upcoming entries with predictions.</p>}</div>:<Loading/>}
  {selected.length>0&&<section><h2>Compare horses ({selected.length}/4)</h2><div className="stable-grid">{selected.map(h=><Compare key={h.horse_id} horse={h}/>)}</div><button className="btn ghost" onClick={()=>setSelected([])}>Clear comparison</button></section>}
  {notifications.data?.length? <section className="card"><h2>Watchlist updates</h2>{notifications.data.map(n=><div className="stable-controls" key={n.event_id}><span><Link to={"/stables/"+encodeURIComponent(n.horse_id)}>{n.horse_name}</Link> · {n.title} · {n.date}</span><button className="btn ghost small" onClick={async()=>{await api("/api/stables/notifications/read",{method:"POST",body:JSON.stringify({eventId:n.event_id})});notifications.reload()}}>Mark read</button></div>)}</section>:null}
  <h2>Horse directory</h2>{counts.data&&<p className="small muted">{counts.data.indexed.toLocaleString()} recorded horses · {counts.data.enriched.toLocaleString()} externally verified · {counts.data.photographs.toLocaleString()} photographs</p>}<div className="card stable-search"><input aria-label="Search horses" placeholder="Horse, trainer, pedigree, track…" value={q} onChange={e=>setQ(e.target.value)}/><label><input type="checkbox" checked={favorite} onChange={e=>{setFavorite(e.target.checked);reset()}}/> Watchlist</label><label><input type="checkbox" checked={upcoming} onChange={e=>{setUpcoming(e.target.checked);reset()}}/> Upcoming races</label><select aria-label="Surface" value={surface} onChange={e=>{setSurface(e.target.value);reset()}}><option value="">All surfaces</option><option value="D">Dirt</option><option value="T">Turf</option><option value="S">Synthetic</option></select><select aria-label="Sex" value={sex} onChange={e=>{setSex(e.target.value);reset()}}><option value="">All sexes</option>{Object.entries({G:"Gelding",C:"Colt",F:"Filly",M:"Mare",H:"Horse",R:"Ridgling"}).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select><input type="number" min="1" max="30" aria-label="Age" placeholder="Age" value={age} onChange={e=>{setAge(e.target.value);reset()}}/></div>
  {error?<ErrorBox error={error}/>:data?<><div className="stable-grid">{data.horses.map(h=><HorseCard key={h.horse_id} horse={h} selected={selected.some(s=>s.horse_id===h.horse_id)} onSelect={toggle}/>)}{!data.horses.length&&<p>No matching horses.</p>}</div><div className="stable-controls"><button className="btn ghost" disabled={!previous.length} onClick={()=>{setAfter(previous[previous.length-1]||"");setPrevious(previous.slice(0,-1))}}>Previous</button><button className="btn primary" disabled={!data.after} onClick={()=>{setPrevious([...previous,after]);setAfter(data.after||"")}}>Next</button></div></>:<Loading/>}</>;
}
export function Stables({id}:{id?:string}) { return id?<Profile key={id} id={id}/>:<DirectoryPage/>; }
