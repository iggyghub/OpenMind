---
name: campaign-scaffold
description: Generate a complete autonomous loop campaign from a slice list: DRIVER.md, scripts/run-<name>.ps1, scripts/stop-<name>.ps1, and GitHub issues parameterized from the proven runner pattern. Use when user says "scaffold a campaign", "new loop campaign", "set up a build loop", or /campaign-scaffold.
---

# campaign-scaffold

Generates the four artifacts that launch and drive an autonomous Claude Code slice loop in this repo.

## Quick start

Gather three things, then scaffold:
1. **Campaign name** (e.g. `jobs`, `sandbox`) -- becomes the DRIVER filename and log-dir key
2. **Slice list** -- each entry is a short label + description; one GitHub issue per slice
3. **Per-slice model** -- `sonnet` for most slices; `opus` for correctness-critical ones

Then generate the four artifacts below in order.

## Generation steps

**1. Create GitHub issues** -- one per slice, labelled `ready-for-agent`:
```
gh issue create --title "<campaign> S<n>: <label>" --body "<spec>" --label ready-for-agent
```
Note the issue number; it goes into the Queue.

**2. Create `<CAMPAIGN>.md`** in repo root -- use the Driver format below.

**3. Create `scripts/run-<name>.ps1`** -- copy `scripts/run-CAMPAIGN.ps1.template` and
replace three placeholders: `CAMPAIGN_DISPLAY` (human label), `campaign_name`
(lowercase, used in log paths), `DRIVER_FILE` (e.g. `SANDBOX-BUILD.md`). Keep the body ASCII-only.

**4. Create `scripts/stop-<name>.ps1`** -- copy `scripts/stop-CAMPAIGN.ps1.template`,
replace the same placeholders.

**5. Commit DRIVER to master** -- the `<CAMPAIGN>.md` is the ONLY file committed straight
to master. Runner scripts go through a normal PR.

## DRIVER.md format

```
# <CAMPAIGN>.md -- <Display Name> campaign driver

## Status: ready

## Next slice -- start here

- **Active:** S1 -- #<issue-N>
- **Model:** sonnet

## Queue

- [ ] S1 -- #<N> -- <label>
- [ ] S2 -- #<M> -- <label>

## Landed PRs

## SAFETY

<Campaign-specific safety rules here>
```

The runner parses `## Status:` (ready/done/blocked), `Model:` line, and `Active: Sx -- #N`
with `Select-String` -- those exact spellings are required.

## Proven runner conventions (bake in, do not skip)

- `$env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = "64000"` set inside `try` before `claude -p` (sessions fail silently without it -- SBX-2 failed 3x)
- `--dangerously-skip-permissions` on the `claude -p` invocation
- Per-attempt logs: `.claude/tmp/<name>-loop/<stamp>-slice<n>-attempt<n>.out.log`
- STOP-file check before each slice and each attempt (graceful stop via `stop-<name>.ps1`)
- Usage-limit auto-resume: pattern-match output, call `Get-SecondsUntilReset`, sleep with `Wait-WithStopCheck`
- Orphan Cerebral reaper: snapshot `python.exe` PIDs before attempt, kill new ones after
- 90-minute (`5400s`) per-attempt hard timeout with poll-and-kill loop
- `try/catch/finally { Read-Host "Press Enter to close" | Out-Null }` so the console stays open on double-click
- `$rules` string bakes in: branch off `origin/master`, ONE PR per issue, `Closes #N` in body, merge-before-next, DRIVER is the only direct master commit, set `Status: blocked` + stop without merging on unrecoverable failure

See `scripts/run-CAMPAIGN.ps1.template` for the full annotated runner skeleton.
