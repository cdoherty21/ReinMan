// When to start the race-day monitor. Decided fresh on every 5-minute cron tick from the
// post times currently in the database, so a card that changes (posts moved, a race
// delayed, an added or cancelled race, a dark day) changes the plan on the next tick.

export type RaceTime = { post: number; official: boolean };

export type MonitorPlan = { due: boolean; reason: string };

const MIN = 60_000;

export const PLAN = {
  preFrom: 4, // start a check when a race goes off in 4–14 minutes: odds are read and the
  preTo: 14, //  pick re-priced just before post, which is the pick that gets locked
  warmup: 90, // first post of the day within 90 minutes: cards, scratches, opening picks
  warmupGap: 60,
  resultsAfter: 10, // a race off at least 10 minutes without an official result
  resultsWindow: 180,
  resultsGap: 30,
  idleGap: 180, // no races known: look for cards every 3 hours, 8am–10pm ET
};

export function monitorPlan(now: number, races: RaceTime[], lastDispatch: number | null, etHour: number,
                            minGap = 20): MonitorPlan {
  const since = lastDispatch == null ? Infinity : (now - lastDispatch) / MIN;
  const ahead = (r: RaceTime) => (r.post - now) / MIN;
  const open = races.filter((r) => !r.official);

  const soon = open.filter((r) => ahead(r) > PLAN.preFrom && ahead(r) <= PLAN.preTo);
  if (soon.length && since >= minGap) {
    const first = Math.round(Math.min(...soon.map(ahead)));
    return { due: true, reason: `${soon.length} race(s) off within ${PLAN.preTo} min (next in ${first} min)` };
  }
  const upcoming = open.filter((r) => ahead(r) > 0).map(ahead).sort((a, b) => a - b);
  // Warm-up only before a session's first race: nothing run in the last few hours.
  const session = races.some((r) => ahead(r) <= 0 && ahead(r) >= -PLAN.resultsWindow);
  if (upcoming.length && !session && upcoming[0] <= PLAN.warmup && since >= PLAN.warmupGap) {
    return { due: true, reason: `warm-up: next post in ${Math.round(upcoming[0])} min` };
  }
  const unsettled = open.filter((r) => -ahead(r) >= PLAN.resultsAfter && -ahead(r) <= PLAN.resultsWindow);
  if (unsettled.length && since >= PLAN.resultsGap) {
    return { due: true, reason: `results: ${unsettled.length} race(s) run without an official result` };
  }
  const known = races.some((r) => ahead(r) >= -PLAN.resultsWindow);
  if (!known && etHour >= 8 && etHour < 22 && since >= PLAN.idleGap) {
    return { due: true, reason: "no races on file: looking for cards" };
  }
  if (soon.length) return { due: false, reason: `races posting soon; last check ${Math.round(since)} min ago` };
  if (upcoming.length) return { due: false, reason: `next post in ${Math.round(upcoming[0])} min` };
  return { due: false, reason: known ? "waiting on results" : "no racing scheduled" };
}

export function etHour(d: Date): number {
  return Number(new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", hourCycle: "h23" })
    .format(d));
}
