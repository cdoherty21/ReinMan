# Grading Rules

* **What is graded:** the locked prediction for each race — the latest snapshot recorded
  before scheduled post time. Locks are written by the Worker cron every 5 minutes (and by
  the Python job as a backup). Nothing can be locked after post.
* **Picks:** top-1/2/3 finishing positions, rank Rein Man gave the winner, log-loss and
  Brier score of the win probabilities (renormalized over runners that actually ran).
* **Statuses:** `graded`; `no_contest` (cancelled / no official winner); `top1_scratched`
  (top pick scratched after lock — shown in its own count, never hidden).
* **Payouts:** official payoff × (ticket base ÷ payoff base). Dead heats: official payoffs
  already split; a dead-heated runner counts in that position (and either slot for
  exotics where both orders are paid).
* **Coupled entries** (1, 1A, 1X) are one betting interest.
* **Scratches after lock:** win/place/show refunded; exotic combinations containing the
  scratched runner refunded per combination; multi-race combinations with a scratched
  selection refunded (some tracks substitute the post-time favorite — that information is
  not in the data, so Rein Man refunds and notes it on the grade).
* **Winning combination with no published payoff:** wait 2 days, then grade `unpriced`
  with the stake returned and a note. It still appears in every count.
* **Actual (real-money) wagers** are entered manually and stored in `actual_wagers`,
  completely separate from hypothetical performance.
