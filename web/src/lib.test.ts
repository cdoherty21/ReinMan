import { describe, expect, it, vi } from "vitest";

vi.stubGlobal("window", { addEventListener: () => {} });
const { odds, pct, money, shiftDate, wagerLabel } = await import("./lib");

describe("formatting", () => {
  it("formats odds conventionally", () => {
    expect(odds(2.5)).toBe("5-2");
    expect(odds(8)).toBe("8-1");
    expect(odds(1.8)).toBe("9-5");
    expect(odds(0.4)).toBe("2-5");
    expect(odds(null)).toBe("—");
    expect(odds(9.2)).toBe("9-1");
    expect(odds(43.2)).toBe("43-1");
  });
  it("formats money and percentages", () => {
    expect(money(-12.5)).toBe("−$12.50");
    expect(pct(0.256)).toBe("25.6%");
  });
  it("shifts dates and labels wagers", () => {
    expect(shiftDate("2026-02-28", 1)).toBe("2026-03-01");
    expect(wagerLabel("PICK4")).toBe("Pick 4");
    expect(wagerLabel("EXACTA")).toBe("Exacta");
  });
});

describe("dates", () => {
  it("reads month/day/year with the year last", async () => {
    const { fmtDate, usDates, dayName } = await import("./lib");
    expect(fmtDate("2026-11-05")).toBe("11/5/2026");
    expect(fmtDate("2026-11-05", true)).toBe("11/5/26");
    expect(fmtDate(null)).toBe("—");
    expect(dayName("2026-11-25")).toBe("Wednesday");
    expect(usDates("2026-11-23 · PRX R1 · finished 1")).toBe("11/23/2026 · PRX R1 · finished 1");
    expect(usDates("read at 2026-11-23T10:00:00Z")).toBe("read at 2026-11-23T10:00:00Z");
  });
});

describe("long dates", () => {
  it("spells the month with the year last", async () => {
    const { longDate } = await import("./lib");
    expect(longDate("2026-12-01")).toBe("December 1, 2026");
    expect(longDate("2027-05-02")).toBe("May 2, 2027");
  });
});
