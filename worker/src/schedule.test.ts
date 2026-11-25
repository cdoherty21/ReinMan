import { describe, expect, it } from "vitest";
import { etHour, monitorPlan } from "./schedule";

const MIN = 60_000;
const now = Date.parse("2026-11-25T18:00:00Z");
const race = (minutesAhead: number, official = false) => ({ post: now + minutesAhead * MIN, official });

describe("monitorPlan", () => {
  it("starts a check just before a post, then waits the minimum gap", () => {
    expect(monitorPlan(now, [race(10)], null, 13)).toMatchObject({ due: true });
    expect(monitorPlan(now, [race(10)], now - 5 * MIN, 13).due).toBe(false);
    expect(monitorPlan(now, [race(10)], now - 25 * MIN, 13).due).toBe(true);
  });

  it("follows the card when a post time moves", () => {
    expect(monitorPlan(now, [race(-40, true), race(30)], now - 60 * MIN, 13).due).toBe(false); // not yet
    expect(monitorPlan(now, [race(-40, true), race(12)], now - 60 * MIN, 13).due).toBe(true);  // moved up
  });

  it("warms up before the first race and sweeps results after races", () => {
    expect(monitorPlan(now, [race(80)], null, 11).reason).toMatch(/warm-up/);
    expect(monitorPlan(now, [race(80)], now - 30 * MIN, 11).due).toBe(false);
    expect(monitorPlan(now, [race(-30, true), race(60)], now - 90 * MIN, 13).due).toBe(false); // mid-session
    expect(monitorPlan(now, [race(-20)], now - 40 * MIN, 13).reason).toMatch(/results/);
    expect(monitorPlan(now, [race(-20, true)], now - 40 * MIN, 13).due).toBe(false);
  });

  it("stays quiet on dark days at night and looks for cards in the daytime", () => {
    expect(monitorPlan(now, [], null, 23).due).toBe(false);
    expect(monitorPlan(now, [], null, 13).reason).toMatch(/looking for cards/);
    expect(monitorPlan(now, [], now - 60 * MIN, 13).due).toBe(false);
  });

  it("reads the Eastern hour across daylight saving", () => {
    expect(etHour(new Date("2026-11-25T18:00:00Z"))).toBe(13);
    expect(etHour(new Date("2026-07-01T18:00:00Z"))).toBe(14);
  });
});

import { cronWorthLogging } from "./index";

describe("cron logging (D1 write budget)", () => {
  it("writes nothing for a quiet tick", () => {
    expect(cronWorthLogging(0, { monitor: "next post in 40 min", documents: "no pending documents", daily: "not due", learn: "not due" })).toBe(false);
    expect(cronWorthLogging(0, { monitor: "2 race(s) off within 14 min: dispatch 204" })).toBe(false);
  });
  it("logs locks and failures", () => {
    expect(cronWorthLogging(2, {})).toBe(true);
    expect(cronWorthLogging(0, { monitor: "results: dispatch 422" })).toBe(true);
    expect(cronWorthLogging(0, { documents: "dispatch failed" })).toBe(true);
  });
});
