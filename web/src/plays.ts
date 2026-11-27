// Which races count as Rein Man's plays for a day.
// A race still to run counts when its current decision is "play". Once it has run, the
// locked pick (the final one recorded before post) is the record: it counts if that pick
// was a play. A race that ran with no locked pick never had a recorded play.
export type PlayRow = {
  race_id: string; post_time: string | null; status: string; is_cancelled: number;
  decision: string | null; locked: number | null;
};

export function isUpcoming(r: PlayRow, now: number): boolean {
  return r.status === "scheduled" && !r.is_cancelled && !!r.post_time && Date.parse(r.post_time) > now;
}

export function playsOf<T extends PlayRow>(races: T[], now: number) {
  const byPost = (a: T, b: T) => ((a.post_time ?? "") < (b.post_time ?? "") ? -1 : 1);
  const upcoming = races.filter((r) => isUpcoming(r, now) && r.decision === "play").sort(byPost);
  const made = races.filter((r) => !isUpcoming(r, now) && !r.is_cancelled && r.locked && r.decision === "play").sort(byPost);
  return { upcoming, made, all: [...made, ...upcoming].sort(byPost) };
}
