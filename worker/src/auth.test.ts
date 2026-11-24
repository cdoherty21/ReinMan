import { describe, expect, it } from "vitest";
import { checkPassword, makeSession, readCookie, timingSafeEqual, verifySession } from "./auth";

describe("auth", () => {
  it("signs and verifies sessions", async () => {
    const t = await makeSession("s3cret", 1_000);
    expect(await verifySession("s3cret", t, 2_000)).toBe(true);
    expect(await verifySession("other", t, 2_000)).toBe(false);
    expect(await verifySession("s3cret", t.replace(/^\d+/, "99999999999999"), 2_000)).toBe(false);
    expect(await verifySession("s3cret", t, 1_000 + 31 * 86400_000)).toBe(false); // expired
    expect(await verifySession("", t, 2_000)).toBe(false); // fails closed
  });
  it("checks passwords and fails closed", async () => {
    expect(await checkPassword("hunter2", "hunter2")).toBe(true);
    expect(await checkPassword("hunter2", "hunter3")).toBe(false);
    expect(await checkPassword(undefined, "")).toBe(false);
    expect(timingSafeEqual("abc", "abcd")).toBe(false);
  });
  it("reads cookies", () => {
    const req = new Request("https://x", { headers: { Cookie: "a=1; reinman_session=xyz%2E1" } });
    expect(readCookie(req, "reinman_session")).toBe("xyz.1");
  });
});
