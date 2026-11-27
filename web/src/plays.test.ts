import { describe, expect, it } from "vitest";
import { playsOf } from "./plays";

const NOW = Date.parse("2026-11-26T20:00:00Z");
const race = (id: string, post: string, extra: Record<string, unknown> = {}) => ({
  race_id: id, post_time: post, status: "scheduled", is_cancelled: 0, decision: "pass", locked: null, ...extra,
});

describe("plays made today", () => {
  it("keeps a play once its race has run (locked pick)", () => {
    const rows = [
      race("A", "2026-11-26T18:00:00Z", { decision: "play", locked: 1, status: "official" }),
      race("B", "2026-11-26T19:00:00Z", { decision: "play", locked: 1 }),          // run, result pending
      race("C", "2026-11-26T21:00:00Z", { decision: "play" }),                      // upcoming
      race("D", "2026-11-26T18:30:00Z", { decision: "pass", locked: 1 }),          // passed
      race("E", "2026-11-26T17:00:00Z", { decision: "play", locked: null }),       // never locked: not a play
      race("F", "2026-11-26T22:00:00Z", { decision: "play", is_cancelled: 1 }),     // cancelled
    ];
    const p = playsOf(rows, NOW);
    expect(p.all.map((r) => r.race_id)).toEqual(["A", "B", "C"]);
    expect(p.made.map((r) => r.race_id)).toEqual(["A", "B"]);
    expect(p.upcoming.map((r) => r.race_id)).toEqual(["C"]);
  });
});
