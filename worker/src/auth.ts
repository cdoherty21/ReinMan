// Private, single-user authentication: password -> HMAC-signed session cookie.
// Fails closed: if REINMAN_PASSWORD or SESSION_SECRET is missing, nobody gets in.

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

export async function makeSession(secret: string, now = Date.now()): Promise<string> {
  const exp = String(now + SESSION_DAYS * 86400_000);
  return `${exp}.${await hmac(secret, `reinman:${exp}`)}`;
}

export async function verifySession(secret: string, token: string | null, now = Date.now()): Promise<boolean> {
  if (!secret || !token) return false;
  const [exp, sig] = token.split(".");
  if (!exp || !sig || !/^\d+$/.test(exp) || Number(exp) < now) return false;
  return timingSafeEqual(sig, await hmac(secret, `reinman:${exp}`));
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
