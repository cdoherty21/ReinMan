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
