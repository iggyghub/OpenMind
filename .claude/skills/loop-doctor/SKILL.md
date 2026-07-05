---
name: loop-doctor
description: Diagnose and heal a stalled or failed autonomous campaign loop in this repo. Reads the loop log and per-attempt out/err logs under .claude/tmp/<name>-loop/, classifies the failure, applies the known remedy, then relaunches or escalates. Use when user says "the loop failed", "diagnose the loop", "loop-doctor", "campaign stalled", "slice N failed", or "something went wrong with the runner".
---

# loop-doctor

Diagnoses a stalled campaign loop and either fixes it automatically or tells
you exactly what to do.

## Quick start

1. Tell me which campaign stalled, or just say "loop-doctor" -- I will find the
   newest `.claude/tmp/*-loop/` dir automatically.
2. I read the loop log and the latest per-attempt out/err logs.
3. I classify the failure from the table below and apply the remedy.

## Log locations

| What | Path pattern |
|------|-------------|
| Loop log | `.claude/tmp/<name>-loop/loop-<stamp>.log` |
| Attempt stdout | `.claude/tmp/<name>-loop/<stamp>-slice<n>-attempt<n>.out.log` |
| Attempt stderr | `.claude/tmp/<name>-loop/<stamp>-slice<n>-attempt<n>.err.log` |
| Driver file | `<CAMPAIGN>.md` (e.g. `SKILLS-BUILD.md`, `SANDBOX-BUILD.md`) |

`<name>` matches the runner script: `skills-loop`, `sandbox-loop`, `ui-overhaul-loop`, etc.

## Failure taxonomy and remedies

| Pattern in logs | Classification | Remedy |
|----------------|---------------|--------|
| `usage limit` / `resets <time>` / `hit your limit` | Not a failure -- quota reset pending | Report the ETA from the log; the runner auto-resumes. Do nothing. |
| `response exceeded the NNNNN output token maximum` | Output token cap hit | Raise `CLAUDE_CODE_MAX_OUTPUT_TOKENS` in the runner script (default in runners is `64000`; raise to `128000`). Or add "write incrementally, keep responses modest" to the slice prompt. Then re-run the runner. |
| `Not logged in` | CLI not authenticated | Tell the human: open a terminal, run `claude`, type `/login`, complete the browser flow, then restart the loop. |
| pytest failures / `FAILED tests/` / `AssertionError` | Real logic bug in the slice | Summarise the failing tests; this is a code defect. Hand back to a debug attempt (the runner retries up to `$MaxAttempts` times automatically) or escalate to a human. |
| node test failures / `npm test` exit non-zero | Real logic bug (frontend) | Same as pytest: summarise, let the runner retry, then escalate. |
| `python.exe` PIDs with `cerebral.main` still alive after the attempt | Orphan Cerebral process | Run `Get-CimInstance Win32_Process -Filter "Name='python.exe'"`, find the orphan PIDs, kill them with `Stop-Process -Id <pid> -Force`. The runner normally does this automatically; if it did not, the runner itself may need inspection. |
| AppContainer profile leftover (sandbox-loop only) | Stale sandbox profile | Note the profile name from the log; delete it with `Remove-AppxPackage` or the profile cleanup path in `scripts/run-sandbox.ps1`. |
| `Status: blocked` in the driver file | Session needs a human | Read the one-line reason in the driver file. Resolve it, change `Status:` back to `ready`, then restart the runner. |
| `Status: done` in the driver file | Campaign finished normally | Nothing to do -- all slices landed. |
| Attempt exited non-zero but no recognisable pattern | Unknown failure | Read the full `.out.log` and `.err.log` for the failing attempt. Check `git status` and `git log --oneline -5` to see what the session left behind. Fix manually, then restart the runner. |

## Step-by-step diagnosis

1. Find the campaign name:
   ```
   ls .claude/tmp/
   ```
   Pick the newest `*-loop/` directory.

2. Read the loop log tail:
   ```
   tail -50 .claude/tmp/<name>-loop/loop-<stamp>.log
   ```

3. Find the failing attempt logs (last slice, last attempt):
   ```
   ls .claude/tmp/<name>-loop/ | sort | tail -6
   ```

4. Read stdout and stderr for that attempt.

5. Match against the table above. Apply remedy.

6. If the driver says `Status: blocked`, read the driver for the one-line reason
   before touching anything else.

## Relaunching

After applying a remedy, restart the runner by double-clicking the
`scripts/run-<name>.ps1` script from Explorer, or from a PowerShell terminal:

```powershell
.\scripts\run-skills.ps1       # skills campaign
.\scripts\run-sandbox.ps1      # sandbox campaign
.\scripts\run-ui-overhaul.ps1  # UI overhaul campaign
```

Runners are idempotent: they re-read the driver file and pick up at the current
`Active:` slice.

## Relationship to the built-in diagnose skill

`/diagnose` is for general bugs and performance regressions (reproduce ->
minimise -> hypothesise -> instrument -> fix). Use `loop-doctor` when the
thing that is broken is the campaign runner itself -- stalled slices, quota
hits, orphan processes, or a blocked driver.