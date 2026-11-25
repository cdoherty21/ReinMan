/** Read-only, on-demand Reddit discussion lookup. No Reddit content is persisted. */
export interface RedditConfig {
  REDDIT_APPROVED?: string;
  REDDIT_CLIENT_ID?: string;
  REDDIT_CLIENT_SECRET?: string;
  REDDIT_USER_AGENT?: string;
  REDDIT_SUBREDDITS?: string;
}

export type Discussion = { title: string; url: string; subreddit: string; published_at: string };
type Listing = { data?: { children?: { data?: {
  title?: string; selftext?: string; permalink?: string; subreddit?: string;
  created_utc?: number; removed_by_category?: string | null;
} }[] } };

export function redditConfigured(env: RedditConfig): boolean {
  return env.REDDIT_APPROVED === "true" && Boolean(env.REDDIT_CLIENT_ID && env.REDDIT_CLIENT_SECRET &&
    env.REDDIT_USER_AGENT?.includes("(by /u/"));
}

function normalized(value: string): string {
  return value.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

export function parseDiscussions(payload: Listing, horse: string, communities: string[]): Discussion[] {
  const name = normalized(horse);
  if (!name) return [];
  const allowed = new Set(communities.map((s) => s.toLowerCase()));
  const seen = new Set<string>();
  const posts: Discussion[] = [];
  for (const child of payload.data?.children ?? []) {
    const p = child.data;
    if (!p?.title || !p.subreddit || !allowed.has(p.subreddit.toLowerCase()) || p.removed_by_category ||
      p.selftext === "[deleted]" || p.selftext === "[removed]" ||
      !p.permalink || !/^\/r\/[A-Za-z0-9_]+\/comments\/[A-Za-z0-9]+(?:\/[^?#]*)?$/.test(p.permalink) ||
      !Number.isFinite(p.created_utc) || seen.has(p.permalink)) continue;
    const content = " " + normalized(p.title + " " + (p.selftext ?? "")) + " ";
    if (!content.includes(" " + name + " ")) continue;
    const date = new Date(p.created_utc! * 1000);
    if (!Number.isFinite(date.getTime()) || date.getTime() > Date.now()) continue;
    seen.add(p.permalink);
    posts.push({ title: p.title.slice(0, 300), url: "https://www.reddit.com" + p.permalink,
      subreddit: p.subreddit, published_at: date.toISOString() });
  }
  return posts.sort((a, b) => b.published_at.localeCompare(a.published_at)).slice(0, 10);
}

export async function searchDiscussions(env: RedditConfig, horse: string): Promise<Discussion[]> {
  if (!redditConfigured(env)) throw new Error("Reddit API is awaiting approved configuration.");
  const communities = (env.REDDIT_SUBREDDITS || "horseracing").split(",")
    .map((s) => s.trim()).filter((s) => /^[A-Za-z0-9_]{2,21}$/.test(s)).slice(0, 3);
  if (!communities.length) throw new Error("No approved Reddit communities configured.");
  const auth = btoa(env.REDDIT_CLIENT_ID! + ":" + env.REDDIT_CLIENT_SECRET!);
  const tokenRes = await fetch("https://www.reddit.com/api/v1/access_token", {
    method: "POST", redirect: "error", signal: AbortSignal.timeout(10000),
    headers: { Authorization: "Basic " + auth, "User-Agent": env.REDDIT_USER_AGENT!,
      "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "client_credentials" }),
  });
  if (!tokenRes.ok) throw new Error("Reddit OAuth request failed (" + tokenRes.status + ").");
  const token = await tokenRes.json() as { access_token?: string };
  if (!token.access_token) throw new Error("Reddit OAuth did not return an access token.");
  const url = new URL("https://oauth.reddit.com/r/" + communities.join("+") + "/search");
  url.search = new URLSearchParams({ q: '"' + horse.replace(/["\\]/g, "") + '"', restrict_sr: "on",
    sort: "new", t: "month", limit: "25", raw_json: "1" }).toString();
  const res = await fetch(url, { redirect: "error", signal: AbortSignal.timeout(10000),
    headers: { Authorization: "Bearer " + token.access_token, "User-Agent": env.REDDIT_USER_AGENT! } });
  if (!res.ok) throw new Error("Reddit search unavailable (" + res.status + ").");
  return parseDiscussions(await res.json() as Listing, horse, communities);
}
