# OpenMind -- Run-and-Fix Log

Driving doc for the autonomous **run-and-fix loop** (`scripts/run-fixes.ps1`),
the run-and-fix analogue of the build-phase slice loop (`scripts/run-slices.ps1`).

Each loop iteration is a brand-new headless `claude -p` session. This file is the
ONLY memory between sessions: read the log so you do not re-hunt a bug a previous
session already fixed, then append your own entry.

The loop boots Cerebral, smokes it for runtime bugs, fixes one, commits it to the
review branch `fix/run-campaign`, and records the result here. **It never commits
or merges to master** -- every fix accumulates on `fix/run-campaign` so a human
reviews the whole campaign and merges it deliberately afterwards. It stops when a
full smoke pass finds nothing left to fix (`Status: clean`), hits a blocker
(`Status: blocked`), or hits the iteration cap.

---

## Kickoff block -- start here

Model: sonnet
Status: hunting

(`Model:` selects the model for the NEXT session -- allowed: haiku | sonnet | opus | fable.
`Status:` controls the loop: `hunting` = keep going; `clean` = a smoke pass found no
bug, stop; `blocked` = a bug needs a human, stop. The loop reads both lines directly.)

### Scope -- what each session does

1. **Read this whole log first** so you do not repeat a past session's work
   (including fixes already on `fix/run-campaign` but not yet reviewed).
2. **Never touch master.** All work lands on the review branch `fix/run-campaign`.
   `git fetch origin`; if the branch exists (locally or as `origin/fix/run-campaign`)
   `git checkout fix/run-campaign && git pull`, otherwise
   `git checkout -b fix/run-campaign origin/master`.
3. **Boot Cerebral headless** and smoke it:
   - Run `python -m cerebral.main` (WebSocket IPC server on `ws://localhost:7766`).
   - Exercise tool dispatch / IPC the way `cerebral/tests/` rigs do (orchestrator,
     settings, conversation, queue) and watch `cerebral.err.log` + stdout for
     unhandled exceptions and tracebacks.
   - **Stop the Cerebral process you started before you finish** -- no orphan python procs.
4. **Find ONE real runtime bug.** If you find one: fix it on `fix/run-campaign`,
   run `pytest -c cerebral/pytest.ini` (or root `python -m pytest`) for the affected
   area, proceed ONLY if green. Commit the fix to `fix/run-campaign` with a clear
   message and `git push -u origin fix/run-campaign`. **Do NOT merge.** Ensure exactly
   one open PR exists for the branch (`gh pr view fix/run-campaign`, else
   `gh pr create --draft --base master --head fix/run-campaign --title 'Run-and-fix campaign'`;
   file a `needs-triage` issue if the bug warrants tracking). The PR stays OPEN for human review.
5. **Append an entry** to the log below (what you ran, the bug, the fix, the commit SHA) and
   update the kickoff block: set `Model:` for the next session and set `Status:`
   (`hunting` if you fixed something, `clean` if a full smoke pass found NOTHING,
   `blocked` + a one-line reason if you found a bug you cannot fix). Commit the
   FIX-LOG.md change to `fix/run-campaign` and push.
6. Leave the tree on `fix/run-campaign`, clean, before finishing. Master stays untouched.

### Hard exclusions (safety)

- **NEVER run `plugins/discord_user.py` or the Discord self-bot path.** Running it
  against a real Discord account risks a permanent ban (CONTEXT.md / ADR-0006). Smoke
  it only with mocked transports if at all.
- **No real credentials / live OAuth.** Do not trigger real Google OAuth consent,
  send real messages, place real calls, or hit paid APIs. Use mocked/local backends
  and offline fallbacks only. The voice/mic, browser-OAuth, and 8-hour real-time
  portions of `docs/v1-live-verify.md` are human-only and out of scope for this loop.
- **No destructive git** (no force-push, no history rewrite on master).

---

## Log (newest last)

### Iteration 0 -- 2026-06-13 -- pytest config footgun (pre-seed, human session)

- **Ran:** `python -m pytest` from repo root.
- **Bug:** 413 false failures. Root-level runs resolved to `pyproject.toml` (asyncio
  STRICT) instead of `cerebral/pytest.ini` (asyncio auto); every bare `async def`
  errored with "async def functions are not natively supported." The suite was
  actually green (3049 passed) under the right config.
- **Fix:** added `[tool.pytest.ini_options]` (asyncio_mode=auto, testpaths, markers)
  to `pyproject.toml` mirroring `cerebral/pytest.ini`. Root-level `python -m pytest`
  now reports 3049 passed, 4 skipped.
- **Landed:** committed to master as pre-loop setup (human session, not via the loop).
  The loop itself never commits to master -- this entry is the one exception.

### Iteration 1 -- 2026-06-13 -- finance plugin import of retired google_workspace

- **Ran:** `python -u -m cerebral.main` headless; watched `cerebral.smoke.err` /
  `cerebral.err.log` during boot + plugin discovery. Re-ran orchestrator
  `discover_plugins(Path('plugins'))` standalone to confirm registration errors.
- **Bug:** `[mcp] Refused plugin 'finance' ... create_failed: create() raised: No
  module named 'plugins.google_workspace'`. The B.8 commit (#249) deleted
  `plugins/google_workspace.py` and migrated `plugins/meet.py` to a try/except +
  fallback, but missed `plugins/finance.py`. `FinancePlugin.__init__` ran
  `from plugins.google_workspace import create` unconditionally when no workspace
  was injected. Both `finance_extract_receipt` and `finance_log_expense` therefore
  failed to register at runtime. Tests didn't catch this because they always
  inject a `FakeWorkspace`.
- **Fix:** mirror `meet.py` -- try the retired `plugins.google_workspace`, fall
  back to `plugins.google_workspace_fallback.create` (the module that already
  registers as the `google_workspace` plugin and provides `sheets_write_range`).
  Re-running discovery shows `finance` registers with its 2 tools and no
  registration error.
- **Tests:** `pytest -c cerebral/pytest.ini cerebral/tests/test_plugin_finance.py
  cerebral/tests/test_orchestrator.py cerebral/tests/test_main_dispatcher.py
  cerebral/tests/test_plugin_google_workspace_fallback.py` -> 333 passed.
- **Landed:** commit `56480b4` on `fix/run-campaign`; draft PR
  https://github.com/iggyghub/OpenMind/pull/262 (stays open for human review).
