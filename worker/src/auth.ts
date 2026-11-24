// Private, single-user authentication: email + password -> HMAC-signed session cookie.
// Fails closed: without REINMAN_EMAIL and SESSION_SECRET nobody gets in.
// Sessions carry an "epoch"; bumping it (on password reset) signs out every browser.

const enc = new TextEncoder();
export const COOKIE = "reinman_session";
const SESSION_DAYS = 30;

async function hmac(secret: string, data: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
  ]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(data));
  return b64url(new Uint8Array(sig));
}

function b64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function timingSafeEqual(a: string, b: string): boolean {
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

export async function makeSession(secret: string, epoch = 0, now = Date.now()): Promise<string> {
  const exp = String(now + SESSION_DAYS * 86400_000);
  return `${exp}.${epoch}.${await hmac(secret, `reinman:${exp}:${epoch}`)}`;
}

export async function verifySession(secret: string, token: string | null, epoch = 0, now = Date.now()): Promise<boolean> {
  if (!secret || !token) return false;
  const [exp, ep, sig] = token.split(".");
  if (!exp || !ep || !sig || !/^\d+$/.test(exp) || !/^\d+$/.test(ep) || Number(exp) < now) return false;
  if (Number(ep) !== epoch) return false; // signed out by a password reset
  return timingSafeEqual(sig, await hmac(secret, `reinman:${exp}:${ep}`));
}

/** Keyed password hash: HMAC(SESSION_SECRET, salt:password). A stolen database alone
 *  can't be brute-forced without the secret, and it stays within the free plan's CPU budget. */
export async function hashPassword(secret: string, salt: string, password: string): Promise<string> {
  return hmac(secret, `pw:${salt}:${password}`);
}

export function randomToken(bytes = 32): string {
  return b64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

export async function sha256(s: string): Promise<string> {
  return b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(s))));
}

export function sameEmail(a: string | undefined, b: string | undefined): boolean {
  if (!a || !b) return false;
  return timingSafeEqual(a.trim().toLowerCase(), b.trim().toLowerCase());
}

export async function checkPassword(expected: string | undefined, given: string): Promise<boolean> {
  if (!expected) return false;
  // compare digests so timing does not depend on the password length
  const d = async (s: string) => b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(s))));
  return timingSafeEqual(await d(expected), await d(given));
}

export function readCookie(req: Request, name: string): string | null {
  const c = req.headers.get("Cookie") || "";
  for (const part of c.split(/;\s*/)) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i) === name) return decodeURIComponent(part.slice(i + 1));
  }
  return null;
}

export function sessionCookie(value: string, maxAgeSec: number): string {
  return `${COOKIE}=${encodeURIComponent(value)}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${maxAgeSec}`;
}
