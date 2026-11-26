/// <reference types="node" />
import { describe, expect, it } from "vitest";
import { assessment } from "./stables";
describe("stable assessments", () => {
  it("distinguishes insufficient data from a proven record", () => {
    expect(assessment([]).classification).toBe("Watching Closely");
    expect(assessment([]).confidence).toBe("Limited");
  });
  it("counts recorded finishes and detects improvement", () => {
    const value=assessment([1,2,3,4,5,6]);
    expect(value).toMatchObject({starts:6,wins:1,places:1,shows:1,improving:true,classification:"Rising Star"});
  });
});

import { DatabaseSync } from "node:sqlite";
import { readdirSync, readFileSync } from "node:fs";
import { stables } from "./stables";

function database() {
  const sqlite = new DatabaseSync(":memory:");
  for (const name of readdirSync("worker/migrations").sort()) {
    sqlite.exec(readFileSync("worker/migrations/"+name,"utf8"));
  }
  const db = {prepare(query:string) {
    let args: (string|number|null)[]=[];
    return {
      bind(...params:(string|number|null)[]) { args=params; return this; },
      async all() { return {results:sqlite.prepare(query).all(...args)}; },
      async run() { return sqlite.prepare(query).run(...args); },
    };
  }} as unknown as D1Database;
  const call = async (path:string,body?:unknown) => {
    const url=new URL(path,"https://test.invalid");
    const response=await stables(new Request(url,body===undefined?{}:{method:"POST",body:JSON.stringify(body)}),db,url);
    expect(response.status).toBe(200);
    return response.json() as Promise<any>;
  };
  return {sqlite,call};
}
describe("stables database integration",()=>{
  it("lists unique horses, paginates, searches, and excludes scratched entries",async()=>{
    const {sqlite,call}=database();
    sqlite.exec("INSERT INTO races(race_id, track_id,race_date,race_number,post_time,source) VALUES ('r','TEST','2099-01-01',1,'2099-01-01T00:00:00Z','test')");
    for(let i=0;i<26;i++) sqlite.prepare("INSERT INTO runners(race_id,program_number,horse_id,horse_name,scratched) VALUES ('r',?,?,?,?)").run(String(i+1),"h"+String(i).padStart(2,"0"),"Horse "+i,i===0?1:0);
    const first=await call("/api/stables");
    expect(first.horses).toHaveLength(24);
    expect((await call("/api/stables?after="+first.after)).horses).toHaveLength(2);
    expect((await call("/api/stables?q=Horse%2025")).horses).toHaveLength(1);
    expect((await call("/api/stables/horse/h00")).next).toBeNull();
    expect((await call("/api/stables/horse/h01")).next.verdict).toBe("Insufficient Data");
    expect((await call("/api/stables/horse/h01/favorite",{favorite:true})).favorite).toBe(true);
    expect((await call("/api/stables?favorites=1")).horses).toHaveLength(1);
    const notifications=await call("/api/stables/notifications");
    expect(notifications).toHaveLength(1);
    await call("/api/stables/notifications/read",{eventId:notifications[0].event_id});
    await call("/api/stables/notifications/read",{eventId:notifications[0].event_id});
    expect(await call("/api/stables/notifications")).toHaveLength(0);
  });
  it("preserves archive-only records without treating winner-only history as full career statistics",async()=>{
    const {sqlite,call}=database();
    sqlite.exec("INSERT INTO historical_winners VALUES ('TEST','2020-01-01',1,'EXAMPLE','Example','{}',1,'2026-01-01')");
    const list=await call("/api/stables?scope=archive&q=example");
    expect(list.horses[0].horse_id).toBe("archive:EXAMPLE");
    const horse=await call("/api/stables/horse/archive%3AEXAMPLE");
    expect(horse.record.starts).toBe(0);
    expect(horse.winnerRecords).toHaveLength(1);
  });
});

describe("featured and comparison",()=>{
  it("includes recent documented horses awaiting entries and reports shared race results",async()=>{
    const {sqlite,call}=database();
    sqlite.exec("INSERT INTO races(race_id,track_id,race_date,race_number,source,status) VALUES ('past','TEST',date('now','-1 day'),1,'test','official')");
    sqlite.exec("INSERT INTO runners(race_id,program_number,horse_id,horse_name) VALUES ('past','1','alpha','Alpha'),('past','2','beta','Beta')");
    sqlite.exec("INSERT INTO result_finishers(race_id,program_number,horse_name,finish_position) VALUES ('past','1','Alpha',1),('past','2','Beta',2)");
    const featured=await call("/api/stables/featured");
    expect(featured).toHaveLength(2);
    expect(featured.map((h:{recent_form:number[]})=>h.recent_form)).toEqual([[1],[2]]);
    expect(featured.every((horse:{next:unknown})=>horse.next===null)).toBe(true);
    const comparison=await call("/api/stables/compare?id=alpha&id=beta");
    expect(comparison.headToHead[0].map((h:{finish:number})=>h.finish)).toEqual([1,2]);
    const winners=await call("/api/stables?performance=winner");
    expect(winners.horses.map((h:{horse_id:string})=>h.horse_id)).toEqual(["alpha"]);
    expect(winners.horses[0].recent_form).toEqual([1]);
    expect(winners.horses[0]).toMatchObject({recorded_starts:1,recorded_wins:1,recorded_itm:1,entry:null});
    expect(winners.horses[0]).not.toHaveProperty("image");
  });
});
