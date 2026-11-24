import { describe, expect, it } from "vitest";
import { checkPassword, hashPassword, makeSession, randomToken, readCookie, sameEmail, timingSafeEqual, verifySession } from "./auth";

describe("auth", () => {
  it("signs and verifies sessions", async () => {
    const t = await makeSession("s3cret", 0, 1_000);
    expect(await verifySession("s3cret", t, 0, 2_000)).toBe(true);
    expect(await verifySession("other", t, 0, 2_000)).toBe(false);
    expect(await verifySession("s3cret", t.replace(/^\d+/, "99999999999999"), 0, 2_000)).toBe(false);
    expect(await verifySession("s3cret", t, 0, 1_000 + 31 * 86400_000)).toBe(false); // expired
    expect(await verifySession("", t, 0, 2_000)).toBe(false); // fails closed
    expect(await verifySession("s3cret", t, 1, 2_000)).toBe(false); // signed out by a reset
    const forged = t.replace(/^(\d+)\.0\./, "$1.1.");
    expect(await verifySession("s3cret", forged, 1, 2_000)).toBe(false); // epoch can't be edited
  });
  it("checks passwords and fails closed", async () => {
    expect(await checkPassword("hunter2", "hunter2")).toBe(true);
    expect(await checkPassword("hunter2", "hunter3")).toBe(false);
    expect(await checkPassword(undefined, "")).toBe(false);
    expect(timingSafeEqual("abc", "abcd")).toBe(false);
  });
  it("hashes passwords with salt and secret, and compares emails loosely", async () => {
    const h = await hashPassword("sec", "salt1", "correct horse");
    expect(h).toBe(await hashPassword("sec", "salt1", "correct horse"));
    expect(h).not.toBe(await hashPassword("sec", "salt2", "correct horse"));
    expect(h).not.toBe(await hashPassword("other", "salt1", "correct horse"));
    expect(randomToken()).not.toBe(randomToken());
    expect(sameEmail(" Me@Example.com", "me@example.com ")).toBe(true);
    expect(sameEmail(undefined, "")).toBe(false);
  });
  it("reads cookies", () => {
    const req = new Request("https://x", { headers: { Cookie: "a=1; reinman_session=xyz%2E1" } });
    expect(readCookie(req, "reinman_session")).toBe("xyz.1");
  });
});

import { scheduledJobsDue } from "./index";
describe("worker clock", () => {
  it("starts the daily cycle around 6:40am ET and learning on Monday mornings", () => {
    expect(scheduledJobsDue(new Date("2026-11-24T10:40:00Z"))).toEqual({ daily: true, learn: false });
    expect(scheduledJobsDue(new Date("2026-11-24T10:55:00Z")).daily).toBe(false);
    expect(scheduledJobsDue(new Date("2026-11-23T09:25:00Z"))).toEqual({ daily: false, learn: true }); // Monday
    expect(scheduledJobsDue(new Date("2026-11-24T09:25:00Z")).learn).toBe(false); // Tuesday
  });
});
