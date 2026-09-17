# ACTIVE-SYSTEMS.md -- pill/panel background-activity indicator campaign driver

The state pill (Passive/Active/Thinking/Speaking, top of the Main window)
only ever reacts to the conversational turn lifecycle (wake/thinking/
speaking/passive) -- it never reflects background work. Clicking it opens
the Thinking panel (`builtin:thinking`, tray/windows/main.html), which is
fed purely by discrete tool_call/tool_result pairs. Two real gaps found
2026-09-16:

1. Long-running background work with **no single call/result pair to
   bracket it** never shows anywhere: `start_batch_replay`/
   `start_cross_stock_replay` return almost instantly while the real sweep
   continues in its own background task for hours; book ingestion is the
   same shape (`upload_book` returns immediately, `_run_book_ingestion`
   keeps going per `book_id`). A `self_dev_campaign` triggered from
   somewhere other than a visible `call_tool` (e.g. an autonomous trigger)
   has the same problem.
2. Even a plain `self_dev` call, which IS a real tool_call/tool_result
   pair, only shows as a spinner buried in the Thinking panel's feed --
   the pill itself stays "Passive" the whole time.

User's own framing: "it should show something for every system felix can
use" -- assorted (grouped by system/category) and filtered (only show
what's actually active, not a static list of every possible system).

This is **Felix-built** (ADR-0015 self_dev) per explicit instruction
2026-09-16 -- not a Claude Code hand-edit, even though `tray/` is in
scope (self_dev's own test gate already runs `npm test`/jest for any
diff touching `tray/`, per SUP-0/ADR-0033 -- this isn't new ground for
it). A full working design (state shape, render function, polling,
pill-priority logic) was drafted and verified syntax-clean by a Claude
Code session before this instruction landed; kept as
`.claude/tmp/active-systems-reference-diff.patch` in that session's
scratchpad for reference **only** -- self_dev should implement its own
version from the issue specs below, not have that diff applied verbatim,
since the point is Felix doing the work.

Scoped 2026-09-16.

## Status: ready

## Next slice -- start here

- **Active:** S1 -- #1277
- **Model:** sonnet

## Queue

- [ ] S1 -- #1277 -- Active Systems section in the Thinking panel: DOM
  container, CSS, a grouped/filtered render function, and polling for the
  two subsystems with an existing status tool (Batch Replay, Cross-Stock
  sweep)
- [ ] S2 -- #1278 -- extend coverage to Self-Dev (both the
  `self_dev_campaign_status` broadcast and plain `self_dev` tool_call/
  tool_result tracking) and Book ingestion (`list_books`, filtered to
  `status === "processing"`)
- [ ] S3 -- #1279 -- wire the aggregate "is anything active" signal into
  the pill itself: a new `working` state, lower-priority than any live
  conversational state, shown only when the pill would otherwise be
  `passive`

S1 blocks S2 (the render function/state shape must exist before more
systems feed it). S2 blocks S3 (the pill needs a real
`anyActiveSystem()`-shaped signal to react to, not just the panel's own
render).

## Landed PRs

## SAFETY

- **Read-only against every subsystem it surfaces.** This campaign only
  displays existing status (`get_batch_replay_status`,
  `get_cross_stock_replay_status`, `list_books`,
  `self_dev_campaign_status`, tool_call/tool_result) -- it must never call
  `start_batch_replay`, `start_cross_stock_replay`, `self_dev`,
  `self_dev_campaign`, or any book-ingestion mutator itself.
- **Polling must stay off the tool-activity feed.** Any new `call_tool`
  polling this campaign adds must set `"record": false` (see #1260/#1261
  and its same-day reply-path regression fix in `cerebral/main.py`,
  merged 2026-09-16) -- a UI poller is not something Felix decided to do
  and must not flood the feed the way Batch Replay/Cross-Stock's own
  polling once did.
- **Hand-verify live before calling any slice done (ADR-0028 rule 6).**
  Green tests are necessary, not sufficient. Check the actual rendered
  panel and pill in the running tray, not just jest passing -- a
  `record: false` poll that silently gets no reply (exactly the class of
  bug in the reply-path regression above) would look identical to "nothing
  is active" in a screenshot without a live check.
