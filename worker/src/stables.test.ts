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
