# WATCH.md -- self_dev campaign driver: the runtime watch (ADR-0005 amendment 2026-10-11)

Source: the r/AI_Agents read of 2026-10-11 (Library > Reddit, collection "AI Agents"). Its
"Your AI agent is already compromised" thread named two things Felix does not do: treat outside
content as untrusted, and notice unusual behaviour. The user decided the design on 2026-10-11:

1. Anomaly (first-ever use of a tool, or > 10x its 30-day daily volume) -> **pause and ask me**.
2. After a turn reads outside content (web, Reddit, email, search) -> outward/destructive calls
   **ask first** for the rest of the turn; outside content is labelled untrusted for the planner.
3. Baseline **learned from the last 30 days** of Activity Log tool calls.

Design, decisions and rejected alternatives: `docs/adr/0005-security-model.md`, amendment
"Runtime watch: untrusted-content taint and anomaly escalation". Help topic updated in W4.

Each slice is one GitHub issue whose body is the complete instruction self_dev receives: exact
files, exact SEARCH/REPLACE text (or a whole new file), acceptance checks. The bodies were
generated from a reference tree (local branch `ref/runtime-watch`, full suite 6,100 pass / 0 fail)
and dry-run in order against master e2eaa0d: each slice applied on its own, tests passed after
each, and W1..W4 together reproduce the reference byte-for-byte.

## Status: ready

## Next slice -- start here

- **Active:** W1 -- #1409
- **Model:** self_dev-default

## Queue

Strictly in order: each slice builds on the previous one being merged.

- [ ] W1 -- #1409 -- `cerebral/security/watch.py` + `test_security_watch.py` (new files, nothing wired)
- [ ] W2 -- #1410 -- `consent.py` reason arg + orchestrator `set_watch` / escalation / `after_call` + `test_watch_integration.py`
- [ ] W3 -- #1411 -- `chain_engine.py`: `watch.chain()` per run + untrusted wrapper in the planner view (+1 test)
- [ ] W4 -- #1412 -- `main.py` wiring + 30-day baseline + Help topic

## Landed PRs

## Live verification (after W4, by hand)

- Restart Felix; `cerebral.err.log` shows `[watch] baseline: N tool calls over 30 days`.
- Ask Felix to read a webpage and then save a file in the same request: the save shows a consent
  card with a "why" line naming the outside read. A plain "save a file" request does not.
- Background jobs (trading scheduler, Reddit crawl) keep running with no cards.

**Running it.** `python .campaign-scratch/fire_campaign.py WATCH.md 1 <timeout>` per slice, then
hand-verify the PR diff against the issue body (self_dev squash-diff trap: check the sandbox
clone's own git log), and reset `Status:` by hand if self_dev wrote `blocked`.
