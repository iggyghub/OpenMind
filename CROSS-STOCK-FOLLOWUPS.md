# CROSS-STOCK-FOLLOWUPS.md -- Cross-Stock Followups campaign driver

Fixes the correctness and measurement defects found in a post-campaign
review of CROSS-STOCK-VALIDATION (see that file's "Follow-ups" section for
the full findings, with file:line references). That campaign's five slices
all landed and were hand-verified on 2026-09-15; this one cleans up what
the review found afterwards, before the accumulated sweep data is trusted.

Scoped 2026-09-15. Four agent-ready slices, filed as standalone issues --
there is no parent epic issue, so every PR closes its own.

## Status: ready

## Next slice -- start here

- **Active:** F1 -- #1246
- **Model:** sonnet

## Queue

- [ ] F1 -- #1246 -- make the results table the progress source of truth;
  delete the settings cursor (review findings 1, 2, 3 and most of 5).
  Net code deletion. **Must land first** -- changes the
  `cross_stock_results` primary key that F3 and F4 both build on.
- [ ] F2 -- #1247 -- bound the sweep stop path with `wait_for` + `cancel`
  so a wedged pair cannot stall the singular scheduler (finding 4,
  ADR-0028 rule 5). Independent of the other three; safe in any order.
- [ ] F3 -- #1248 -- record `benchmark_return` (buy-and-hold) per pair
  (findings 6, 10). Data collection only -- commits to no metric decision.
- [ ] F4 -- #1249 -- minimum-trade floor + cost-model sensitivity
  (findings 7, 8). Reads most naturally after F3.

**Order: F1 -> F3 -> F4.** All three touch `cross_stock_results`. F2 is
independent. Never start a later slice until the earlier one is MERGED to
`origin/master`, not merely committed -- a slice that branches off a master
missing its predecessor is how earlier campaigns here produced conflict
cascades.

## Explicitly NOT in this queue

- **#1250** (metric redesign) and **#1251** (other validation axes) are
  `needs-triage` scoping issues, not build tickets. They record decisions
  that need a human conversation first, same discipline as this campaign's
  own `check_graduation` deferral. An agent must never pick them up, and
  must never implement their content as a side effect of another slice.

## Landed PRs

## SAFETY

- **No slice may place an order, real or paper.** This reads history and
  writes results rows, nothing else. `run_gauntlet` and `auto_promote` must
  never be reachable from any replay path.
- **`cross_stock_consistency` stays informational-only.** Nothing here may
  touch `check_graduation` or `check_retirement`. Wiring it into a live
  decision is #1250's question, and #1250 is not in this queue.
- **Do not destroy an accumulated sweep.** F1 may drop and recreate the
  results table only after confirming the row count is still trivial (~22
  leftover verification rows as of 2026-09-15), and must say so explicitly
  in its PR body. If a real multi-night sweep has since accumulated, write
  a migration instead.
- **Hand-review every diff before trusting it.** self_dev failed outright
  on three of the five slices in the parent campaign -- crashed-on-boot
  code for S3, no commit at all for S5, test-file-only for S2. Green tests
  are necessary, not sufficient (ADR-0028 rule 6): exercise the real
  surface over the IPC bridge before calling a slice done.
- **The nightly sweep runs midnight-8am ET while this campaign is open.**
  A slice that restarts Cerebral or changes the results schema mid-sweep
  can interact with a live run. Check `get_cross_stock_replay_status`
  before and after any slice that touches the store.
