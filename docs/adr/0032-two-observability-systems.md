# ADR-0032: Two observability systems, and the boundary between them

**Date:** 2026-09-05
**Status:** Accepted (grill session)
**Relates:** ADR-0007 (conversation store + closed kind vocabulary), ADR-0021
(compaction summaries as turns), ADR-0015 (boot self-check), ADR-0030 (`run_id`).

## Context

Felix has two records of what happened, built at different times, unaware of each
other.

| | Conversation store | Process logs |
|---|---|---|
| Where | `openmind.db` | repo root: `cerebral.log`, `cerebral.err.log`, `launcher.log`, `tray.*.log` |
| Shape | structured, closed kind vocabulary -- `tool_call`, `tool_result`, `activity`, `summary` | unstructured text |
| At rest | Fernet-encrypted, per-profile | plaintext, machine-global |
| Lifetime | durable | **truncated on every launch**, no rotation |
| UI | the Log nav tab, Trading's Activity section | "Show Logs" opens the file |
| Answers | *what Felix did* | *what the process did* |

Having two is not the defect -- an OS keeps an audit log and a syslog for good
reasons. The defect is that the boundary was never designed, so nothing protects
the half that matters most when things go wrong.

`launch-felix.ps1` spawns Cerebral with `Start-Process -RedirectStandardOutput` /
`-RedirectStandardError`, which **truncates** rather than appends. Every restart
destroys the previous run's crash evidence -- and restarts are frequent and often
automatic (self-dev, boot-check rollback, Restart Felix). `CLAUDE.md` already
carries the workaround as standing instruction: *"Copy `cerebral.err.log` aside
before restarting anything you're actively debugging."*

The cost of that instruction is visible in the repo root: **37 hand-made copies of
`cerebral.err.log`**, dated 2026-08-26 to 2026-09-02. Five a day for a week. Four
files named `cerebral.err.<timestamp>.log` show the fix was reached for ad hoc and
never landed.

The two records also cannot be joined. A traceback in `cerebral.err.log` and the
`tool_call` turn that caused it are two accounts of one event with no shared
identifier, even though `run_id` already exists for the step ledger.

## Decision

1. **The two systems stay separate, and the boundary is stated:** the
   **conversation store** records *what Felix did* and may depend on Felix
   working; the **process log** records *what the process did* and must survive
   the process failing.

2. **Process logs are never truncated.** One timestamped file per launch,
   retaining the last 10. This deletes the manual `.bak` ritual and the standing
   instruction in `CLAUDE.md` that encodes it.

3. **The chain's `run_id` appears in process log lines**, so a traceback joins to
   the `tool_call` turn that produced it. The identifier already exists (ADR-0030
   / StepLedger); this only propagates it.

4. **Process logs are NOT routed into `openmind.db`.** Unifying them into one
   queryable timeline was the alternative and is rejected on the same principle
   `boot-check.js` states for recovery: a broken brain cannot rescue itself.
   Reading a crash log out of the conversation store would require a running
   Cerebral, a loaded profile, and a Fernet key -- precisely the three things
   unavailable when a crash log is what you need. Encryption, structure, and a
   nicer UI are the wrong trade for a stack trace.

5. **No new log UI.** "Show Logs" opening the file stays; the Log nav tab already
   covers the semantic timeline. Retention and correlation are the whole fix.

## Consequences

- The 37 existing `.bak` files become deletable, and the `CLAUDE.md` warning about
  copying logs aside can be removed once decision 2 lands (not before).
- Disk use becomes bounded and predictable instead of zero-then-manual.
- Diagnosing a crash stops requiring foresight. Today the evidence survives only
  if someone remembered to save it *before* the restart that destroyed it.
- Correlation makes ADR-0030's failure contract debuggable: a chain that retried a
  tool twice and gave up leaves a traceable pair of records rather than two
  unrelated ones.
- Two records of one event remain, with a stated reason. Anyone proposing to merge
  them later has to answer decision 4.
