import { describe, expect, it } from "vitest";
import { itemsToLines } from "./extract";

describe("PDF line rebuilding", () => {
  it("groups items by row and orders them left to right, top to bottom", () => {
    const text = itemsToLines([
      { str: "Silver Comet", x: 40, y: 700 },
      { str: "1", x: 10, y: 720.5 },
      { str: "Morning Thunder", x: 40, y: 721 },
      { str: "2", x: 10, y: 700.8 },
      { str: "  ", x: 90, y: 700 },
    ]);
    expect(text).toBe("1 Morning Thunder\n2 Silver Comet");
  });
});
