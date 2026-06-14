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

Model: opus
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

### Iteration 2 -- 2026-06-13 -- MCPOrchestrator.list_tools() returns duplicate tools

- **Ran:** `python -u -m cerebral.main` headless (PID 17144); exercised IPC
  dispatcher via WebSocket smoke client (`list_tools`, `call_tool`, `list_queue`,
  `list_permissions`, `list_settings`, etc.); ran full test suite
  (2958 passed, 4 skipped).
- **Bug:** `tools_for_llm` exposed 16 duplicate tool entries to the LLM (204 tools
  instead of 188 unique). Every tool that `google_workspace` takes over from a
  fine-grained plugin (`gmail`, `calendar`, `google_docs`, `google_drive`,
  `google_maps`, `google_sheets`) appeared twice. Root cause: `list_tools()` in
  `cerebral/mcp/orchestrator.py` iterated all plugins and called
  `plugin.list_tools()` on each; when `google_workspace` re-registers a tool
  already owned by another plugin, `_tool_index` / `_tool_lookup` are updated
  to the new owner but the superseded plugin still emits its copy when iterated.
  No test covered this because the override path was never asserted on
  `list_tools()`.
- **Fix:** Replace the loop with `return list(self._tool_lookup.values())`.
  `_tool_lookup` is already maintained with last-write-wins semantics identical
  to `_tool_index`, so its values are the authoritative, deduplicated Tool objects.
  Added regression test `test_list_tools_no_duplicates_when_plugin_takes_over`.
  Live smoke after fix: 204 → 188 tools, zero duplicates.
- **Tests:** full suite (minus Discord self-bot tests) -- 2959 passed, 4 skipped.
- **Landed:** commit `f35d721` on `fix/run-campaign`; PR #262 remains open for
  human review.

### Iteration 4 -- 2026-06-13 -- unregister of tool-taker drops prior owner's claim

- **Ran:** `python -u -m cerebral.main` headless (PID 19014, killed after
  smoke); exercised IPC via WebSocket smoke client (`list_tools`,
  `list_plugins`, `list_queue`, `list_permissions`, `list_settings`,
  `list_models`, `list_credentials`, `list_conversation_turns`,
  `list_insights`, `list_memories`, `list_profiles`, `list_voices`,
  `get_env_context`, `get_plugin_settings`, `call_tool` for git_status /
  clock get_time); ran a second deeper smoke that drove ~22 bad-payload
  edge paths (`set_setting` bad keys, `delete_insight` / `pin_insight` /
  `edit_insight` on missing ids, `dismiss_item` nonexistent,
  `set_static_token` / `clear_static_token` missing fields,
  `set_class_policy` / `set_tool_override` / `revoke_session_grant`
  empties, `consent_response` stale id, `call_tool` unknown tool); ran
  full test suite (3055 passed, 4 skipped). Reverted Alice's
  `shell_exec_unlocked` flag that the deeper smoke flipped (the dispatcher
  trusts the click — that's by design, not a bug).
- **Bug:** `MCPOrchestrator._remove_from_index` walked `_tool_index` by
  current ownership only. When plugin B took over a tool from plugin A
  and B was later unregistered, `_tool_index[tool] = B` got deleted and
  the tool vanished from `list_tools()` / `tools_for_llm` / dispatch —
  even though A was still registered and still declared the tool in its
  own `list_tools()`. Reproduced standalone: register `a` with `t1`,
  register `b` with `t1` (b takes over), `unregister("b")` → `t1` gone
  from index; `a` still in `_plugins`; `a.list_tools()` still has `t1`.
  In production the only runtime unregister path is the builder plugin's
  uninstall flow (#30) — any builder-installed plugin that took over a
  base-plugin tool would erase that base tool on removal. Not caught by
  existing tests: the takeover regression tests added in iteration 2 / 3
  exercised takeover+list/dispatch + per-plugin counts, but not the
  takeover-then-unregister round-trip.
- **Fix:** Track a per-tool registration history in
  `MCPOrchestrator._tool_registrations` (`dict[str, list[tuple[str,
  Tool]]]`) populated in `register()` alongside `_tool_index` /
  `_tool_lookup`. `_remove_from_index` walks every tool's history,
  filters out entries belonging to the leaving plugin, and — if a prior
  registrant remains — promotes the last surviving entry back into
  `_tool_index` / `_tool_lookup` as the active owner. A→B→C chain
  unwinds correctly to B then A as C then B leave. Added four regression
  tests in `test_orchestrator.py`:
  `test_unregister_taker_restores_prior_owner`,
  `test_unregister_taker_restores_routing_to_prior_owner`,
  `test_unregister_three_step_takeover_chain_restores_in_reverse`,
  `test_unregister_prior_owner_keeps_taker_active`. Verified the live
  boot still registers 188 unique tools and all 17 plugin seams wire.
- **Tests:** full suite (root `python -m pytest`) -- 3055 passed,
  4 skipped (+4 new regression tests vs iteration 3's 3051).
- **Landed:** commit `e724d78` on `fix/run-campaign`; PR #262 remains
  open for human review.

### Iteration 3 -- 2026-06-13 -- plugins_list tool_count=0 for superseded plugins

- **Ran:** `python -u -m cerebral.main` headless; exercised IPC via WebSocket
  smoke client (`list_tools`, `call_tool`, `list_plugins`, `list_settings`,
  `list_queue`, `list_permissions`, `get_time`, `git_status`); ran full test
  suite (3051 passed, 4 skipped).
- **Bug:** `_plugins_list_event()` in `cerebral/main.py` computed `tool_count`
  for each plugin by summing `_tool_index` entries with that plugin as owner.
  After `google_workspace` takes over tools from `gmail`, `calendar`,
  `google_docs`, and `google_maps`, those plugins' `_tool_index` count drops
  to 0. The tray's Plugins pane showed them as "loaded, 0 tools" even though
  they registered 2, 2, 4, and 4 tools respectively. Root cause: the count
  reflected current tool-index ownership, not registration time. The docstring
  says "number of tools the plugin registers" -- the implementation violated
  its own contract. Tests didn't catch this because the `FakeOrchestrator` in
  `test_plugin_settings_ipc.py` used `_tool_index` to compute the fake count,
  masking the takeover edge case.
- **Fix:** Added `_plugin_registration_tool_counts: dict[str, int]` to
  `MCPOrchestrator.__init__`. `register()` now stores `len(tools)` before the
  takeover loop runs (eliminating a redundant `list_tools()` call in the
  logger as a side-effect). `unregister()` pops the entry. New public method
  `registration_tool_count_for(plugin_name)` exposes it. `_plugins_list_event`
  in `main.py` changed from `sum(..._tool_index...)` to
  `_orc.registration_tool_count_for(plugin_name)`. Updated `FakeOrchestrator`
  in `test_plugin_settings_ipc.py` to expose the new method. Added regression
  test `test_registration_tool_count_for_reflects_original_count_after_takeover`
  in `test_orchestrator.py`. Live smoke (fresh process) confirmed: gmail=2,
  calendar=2, google_docs=4, google_maps=4, google_workspace=18 (all correct).
- **Tests:** full suite -- 3051 passed, 4 skipped.
- **Landed:** commit `d7c4374` on `fix/run-campaign`; PR #262 remains open for
  human review.
