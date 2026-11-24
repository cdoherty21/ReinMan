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
