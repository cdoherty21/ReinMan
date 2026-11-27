// API client, formatting helpers and a tiny router.
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

export class Unauthorized extends Error {}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (r.status === 401) throw new Unauthorized();
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body as { error?: string }).error || `HTTP ${r.status}`);
  return body as T;
}

// Responses are cached per path (stale-while-revalidate, Vercel rule client-swr-dedup):
// going back to a page shows what it last showed at once, then refreshes; identical
// requests in flight are shared. A page never shows another path's data while loading.
const cache = new Map<string, unknown>();
const inflight = new Map<string, Promise<unknown>>();
const CACHE_MAX = 60;

function fetchShared<T>(path: string): Promise<T> {
  let p = inflight.get(path) as Promise<T> | undefined;
  if (!p) {
    p = api<T>(path).then((d) => {
      cache.delete(path);
      cache.set(path, d);
      if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value as string);
      return d;
    });
    p.finally(() => inflight.delete(path)).catch(() => {});
    inflight.set(path, p);
  }
  return p;
}

export function useApi<T>(path: string | null) {
  const [state, setState] = useState<{ path: string | null; data: T | null }>(() => ({
    path, data: path ? ((cache.get(path) as T | undefined) ?? null) : null,
  }));
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  // Derived during render: a new path starts from its own cache entry, never the old page's data.
  const data = state.path === path ? state.data : path ? ((cache.get(path) as T | undefined) ?? null) : null;
  useEffect(() => {
    if (!path) return;
    let live = true;
    setError(null);
    if (tick) inflight.delete(path); // an explicit reload always refetches
    fetchShared<T>(path)
      .then((d) => live && setState({ path, data: d }))
      .catch((e) => {
        if (e instanceof Unauthorized) window.dispatchEvent(new Event("reinman:logout"));
        else if (live) setError(String(e.message || e));
      });
    return () => {
      live = false;
    };
  }, [path, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, reload, loading: !data && !error };
}

// ---------- router (History API) ----------
const listeners = new Set<() => void>();
window.addEventListener("popstate", () => listeners.forEach((l) => l()));
export function navigate(to: string) {
  if (to === location.pathname + location.search) return;
  history.pushState(null, "", to);
  listeners.forEach((l) => l());
  window.scrollTo(0, 0);
}
export function useLocation() {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => location.pathname + location.search,
  );
}

// ---------- formatting ----------
export const pct = (v: number | null | undefined, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
export const money = (v: number | null | undefined) =>
  v == null ? "—" : `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const signedPct = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(1)}%`);

export function odds(dec: number | null | undefined): string {
  if (dec == null) return "—";
  if (dec < 0.1) return "1-9";
  if (dec >= 10) return `${Math.round(dec)}-1`;
  // nearest conventional fraction (denominator <= 5 below 5-1, halves from 5-1 to 10-1)
  const maxDen = dec >= 5 ? 2 : 5;
  let best = { n: Math.round(dec), d: 1, err: Math.abs(dec - Math.round(dec)) };
  for (let d = 2; d <= maxDen; d++) {
    const n = Math.round(dec * d);
    const err = Math.abs(dec - n / d);
    if (err < best.err - 1e-9) best = { n, d, err };
  }
  const g = gcd(best.n, best.d);
  return `${best.n / g}-${best.d / g}`;
}
function gcd(a: number, b: number): number {
  return b ? gcd(b, a % b) : Math.abs(a) || 1;
}

export function postTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
// Dates read month/day/year: 11/25/2026. A bare race date ("2026-11-25") is a calendar
// day, not an instant, so it is formatted from its parts and never shifted by time zone.
export function fmtDate(d: string | null | undefined, short = false): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d ?? "");
  if (!m) return d || "—";
  return `${Number(m[2])}/${Number(m[3])}/${short ? m[1].slice(2) : m[1]}`;
}
// Dates written inside stored analysis text ("2026-11-23 · PRX") read as 11/23/2026.
export const usDates = (text: string) =>
  text.replace(/\b(\d{4})-(\d{2})-(\d{2})(?![\dT:])/g, (_, y, m, d) => `${Number(m)}/${Number(d)}/${y}`);
/** "2026-12-01" -> "December 1, 2026" (a calendar day, never shifted by time zone). */
export function longDate(d: string): string {
  return new Date(`${d}T12:00:00Z`).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
}
export function dayName(d: string, style: "long" | "short" = "long"): string {
  return new Date(`${d}T12:00:00Z`).toLocaleDateString("en-US", { weekday: style, timeZone: "UTC" });
}
export function stamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return iso;
  const day = t.toLocaleDateString("en-US", { month: "numeric", day: "numeric", year: "numeric" });
  return `${day} ${t.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}`;
}
export const surfaceName = (s: string | null | undefined) => ({ D: "Dirt", T: "Turf", S: "Synthetic" })[s ?? ""] ?? "—";

export function todayET(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
}
export function shiftDate(d: string, n: number): string {
  const x = new Date(`${d}T12:00:00Z`);
  x.setUTCDate(x.getUTCDate() + n);
  return x.toISOString().slice(0, 10);
}
export const wagerLabel = (w: string) =>
  ({ DAILY_DOUBLE: "Daily Double", PICK3: "Pick 3", PICK4: "Pick 4", PICK5: "Pick 5", PICK6: "Pick 6" })[w] ??
  w.charAt(0) + w.slice(1).toLowerCase();
