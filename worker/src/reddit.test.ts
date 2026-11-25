import { describe, it, expect } from "vitest";
import { parseDiscussions, redditConfigured } from "./reddit";

describe("Reddit discussion lookup", () => {
  it("requires approval and OAuth configuration", () => {
    const credentials = { REDDIT_CLIENT_ID: "id", REDDIT_CLIENT_SECRET: "secret",
      REDDIT_USER_AGENT: "web:reinman:1.0 (by /u/example)" };
    expect(redditConfigured(credentials)).toBe(false);
    expect(redditConfigured({ ...credentials, REDDIT_APPROVED: "true" })).toBe(true);
  });
  it("filters unrelated, removed, duplicate and unsafe discussion links", () => {
    const post = { title: "Blue Ribbon ran well", subreddit: "horseracing",
      permalink: "/r/horseracing/comments/abc/blue_ribbon/", created_utc: 1700000000 };
    const payload = { data: { children: [post, post,
      { ...post, title: "Blue Ribboner", permalink: "/r/horseracing/comments/def/" },
      { ...post, removed_by_category: "moderator", permalink: "/r/horseracing/comments/ghi/" },
      { ...post, subreddit: "other", permalink: "/r/other/comments/xyz/" },
      { ...post, permalink: "https://example.com" },
    ].map(data => ({ data })) } };
    expect(parseDiscussions(payload, "Blue Ribbon", ["horseracing"])).toEqual([{
      title: post.title, url: "https://www.reddit.com" + post.permalink,
      subreddit: post.subreddit, published_at: new Date(post.created_utc * 1000).toISOString(),
    }]);
  });
});
