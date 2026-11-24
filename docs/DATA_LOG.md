# Data Log

Changes made directly to the live database outside the normal jobs. Each entry says
what ran, from which commit, and why, so every number in Rein Man can be traced.

**Rule from 2026-11-24 on:** a data fix is merged to `main` (or committed in a branch
listed here) *before* it is run against the live database, and recorded below.

## 2026-11-24 — entries reconstructed in the audit (runs from unmerged branches)

| When (UTC) | Branch @ commit | Workflow run | What it did |
|---|---|---|---|
| 05:27 | `codex/recover-finger-lakes` @ `9219d35` | reinman-documents #4 | "Restore verified original for the existing Finger Lakes upload": re-attached an original file to an earlier upload and rescanned it. |
| 05:31–05:33 | `codex/inspect-text-uploads` @ `3e78432`, `1b11e70`, `792c926` | reinman-documents #5 (cancelled), #6, #7 | Read-only inspection of unmatched text uploads (per the commit messages). |
| 05:36 | `codex/restore-nov24-race-cards` @ `aed0e92` | reinman-documents #8 | Imported 51 complete race cards for 2026-11-24 from `engine/verified_2026_11_24_cards.json` (DED 8, FL 8, MNR 8, MVR 8, PRX 9, ZIA 10 races: runners, morning lines, jockeys, trainers, weights, medication, first post time), reprocessed six partial uploads (`finger-lakes.txt`, `parx-racing.txt`, `delta-downs.txt`, `mountaineer.txt`, `mahoning-valley.txt`, `zia-park.txt`), and wrote predictions for changed upcoming races. |

The origin of `verified_2026_11_24_cards.json` (described as a "verified handoff") is
not recorded in the repository. The cards are consistent with official race programs and
with the six uploaded files above. **Owner: confirm the source here.**

These branches are kept (not deleted) because they are the only record of what ran.

## 2026-11-24 — past-performance reader rollout (from `main`)

| When (UTC) | Commit | Workflow | What it did |
|---|---|---|---|
| ~15:00 | past-performance reader merge | reinman-documents (rescan) | Rescanned all uploads so existing programs (FingerLakes1124.pdf, PRX 4x9PPs) attach past performances (speed figures, running positions, finishes, odds, trip comments) to their runners. Gap-filling only; existing cards are not overwritten. |
