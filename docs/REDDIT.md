# Reddit: closed

**Status: closed on 2026-11-26.** Rein Man does not use Reddit.

Rein Man applied to Reddit for Data API access for a personal, read-only,
on-demand lookup of public discussions about a horse (support request 18784057). On
2026-11-26 Reddit replied that it has stopped accepting public Data API access requests
and that apps must now run on its Developer Platform. That platform hosts apps inside
Reddit, such as moderator-installed community tools. It does not give an outside
service like Rein Man a way to read posts, so there is no supported route.

The race page's "Search recent discussions" button, the Worker endpoint behind it and
its Reddit client were removed when the case was closed.

Do not substitute unofficial access:
* Reddit's policy forbids automated reading or storing of posts;
* its public RSS feeds block cloud servers like the ones Rein Man runs on.

News and rumours come from the racing news feeds (`engine/reinman/news.py`,
`news_events.py`).

To reopen: only if Reddit offers sanctioned Data API access again. The previous
implementation is in git history (`worker/src/reddit.ts`, before this change).
