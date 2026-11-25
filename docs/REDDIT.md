# Reddit discussion lookup

Rein Man can look up recent public subreddit discussions mentioning a horse in its
race analysis. The lookup is read-only, limited to configured communities, and runs
when the user selects Search recent discussions. It uses Reddit's approved OAuth
Data API and returns linked titles and publication dates.

## Activation

1. Request access for the exact personal-use horse-racing discussion lookup:
   https://support.reddithelp.com/hc/en-us/requests/new
2. After Reddit approves the use case, configure an OAuth application according to its
   approval and create Worker secrets in Cloudflare for the existing rein-man Worker.
   Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET to the approved application credentials,
   REDDIT_USER_AGENT to the registered descriptive agent (for example
   web:reinman:1.0 (by /u/YOUR_REDDIT_USERNAME)), and REDDIT_APPROVED to true only after approval.
3. Optionally set REDDIT_SUBREDDITS to the comma-separated communities covered by the
   approval. The default is horseracing. The lookup is inactive without credentials.

Reference: https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki

The Worker does not persist Reddit post titles, bodies, author identifiers, or
discussion-derived features. The lookup is independent of prediction snapshots and
model training. The shared request limiter allows one lookup every ten seconds and
the request returns at most ten matches. Reddit content is community discussion
and can be verified by opening the original thread.

Any automated polling, model-training or commercial use must be covered by
Reddit's explicit approval before enabling those use cases.
https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy
