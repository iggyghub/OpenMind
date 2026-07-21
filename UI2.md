# UI2.md -- Felix UI Round 2 campaign driver

Autonomous slice loop for Felix UI Round 2. Design: `docs/adr/0012-workspace-and-plugin-contributed-panels.md`
+ `docs/adr/0013-proposal-queue-and-learning-triggers.md` + CONTEXT.md
("Workspace", "Panel", "Panel spec", "Text widget", "Proposal", "Insight signal").
Grilled 2026-07-20.

Each session reads this file + the active slice's issue, implements that ONE
slice as its issue specifies, opens a per-issue PR, merges it, then rewrites the
"Next slice" block below. This file is the only memory between sessions.

## Status: ready

## Next slice -- start here

- **Active:** S5 -- #484
- **Model:** sonnet

## Queue

Track A is the workspace (ADR-0012); track B is the proposal queue (ADR-0013).
Order below already satisfies every dependency -- do not reorder.

- [x] S1 -- #480 -- A1: sidebar collapses to an icon rail, hotkey + persisted (Model: sonnet)
- [x] S2 -- #481 -- A2a: workspace shell, primary + secondary slot with tab strip (Model: opus)
- [x] S3 -- #482 -- A2b: drag splitter between the slots, width persisted (Model: sonnet)
- [x] S4 -- #483 -- A3: panel spec v1 end-to-end, plugin declares a panel (Model: opus)
- [ ] S5 -- #484 -- A4: text widget, edit plain/Markdown and save back (Model: sonnet)
- [ ] S6 -- #485 -- A5: detach a panel into its own window (Model: opus)
- [ ] S7 -- #486 -- B1: queue gains a kind; insight signals only for tool-bearing proposals (Model: opus)
- [ ] S8 -- #487 -- B2: memory proposals, prompt guidance + user confirms (Model: sonnet)
- [ ] S9 -- #488 -- B3: recipe proposals, offer to save a chain after it repeats (Model: sonnet)

Per-slice model: sonnet unless the queue entry says otherwise. When ticking a
slice, set the next entry's model on the `Model:` line above.

## Landed PRs

- S1 #480 -- PR #490 -- sidebar collapse to icon rail, Ctrl+B + persisted
- S2 #481 -- PR #491 -- workspace shell, primary + secondary slot with tab strip
- S3 #482 -- PR #492 -- drag splitter between workspace slots, width persisted
- S4 #483 -- PR #493 -- panel spec v1 end-to-end, Documents declares a list/detail panel

## SAFETY

Highest priority; overrides the issue if they ever conflict.

1. **NEVER touch the resume / Dutchie apply path.** A live submit is parked in
   another session, and a pending resume correction lives on the user's Desktop.
   Do not read, move or edit anything on the Desktop. Do not run the apply
   pipeline, fetch a live job board, drive a real ATS, or submit anything.
2. **Main window stays node-free**: `nodeIntegration:false`, `contextIsolation:true`,
   WS only (ADR-0007). This includes any new window added by S6 -- a detached
   panel window gets the same posture as the Main window.
3. **No plugin-authored HTML or JS may reach a renderer** (ADR-0012 decision 3).
   Plugins contribute declarative panel specs; the renderer owns all drawing.
   Plugin-supplied strings are escaped, never `innerHTML`'d raw. S4 must keep a
   test proving an HTML-bearing spec value is escaped rather than executed.
4. **No secret ever leaves the keyring.** No WS message may carry a secret value
   -- metadata only. Keep the harness-campaign acceptance check on any payload
   you touch.
5. **Never install software** (no winget, no downloads) and **add no npm
   dependency**. CodeMirror, TipTap and every split-pane framework are
   explicitly rejected by ADR-0012 -- they need a bundler this project does not
   have. `tray/package.json` gains nothing.
6. **Renderer logic goes in UMD-ish dual-mode `tray/lib/*.js` modules** (PR #203
   pattern; 9 existing examples). Register every new lib in
   `tray/tests/renderer-script-globals.test.js` -- an unguarded top-level `const`
   in a `<script src>` lib is a page-killing redeclaration that silently kills
   the whole renderer (#263/#264).
7. **Layout state persists in `localStorage`**, keyed like `section-collapse.js`.
   NOT `position-store.js` -- that module is `require('fs')` and main-process
   only, so it cannot reach the renderer at all.
8. **`.docx` editing keeps ADR-0011's LibreOffice Writer path.** ADR-0012
   narrowed it, it did not reverse it. The text widget is plain text and
   Markdown only. LibreOffice is not installed on this box: never invoke a real
   `soffice.exe`, and keep all conversion behind the existing injectable seams.
9. Seam rule (#153/#385): no `from plugins.<x> import ...` inside `cerebral/` --
   wire through `_wire_plugin_seams` against `_orc.get_plugin_module`. Keep
   `cerebral/tests/test_jobs_seam_wiring.py` passing.
10. S7 migrates a **live** database (311 dismissed rows, `tool_name` NULL).
    The migration must be additive and idempotent, and must not drop or rewrite
    existing rows.
11. If you launch Cerebral to smoke IPC, launch it in the BACKGROUND and ALWAYS
    terminate it before finishing -- leave no orphan `python -m cerebral.main`.
12. Behaviour only verifiable by eye in the live Electron window (visual layout,
    real drag interaction, whether Felix remembers the *right* facts) -> APPEND
    an item to `docs/harness-ui-live-verify.md` (or `docs/documents-live-verify.md`
    for S5), do NOT perform it. Logic must still be covered by jest/pytest.
13. Gate on tests: `python -m pytest cerebral/tests -q` for backend slices, plus
    `npx jest` in `tray/` for renderer slices. Proceed only if ALL pass.
14. Operator `.ps1` scripts: ASCII-only bodies, pause-on-exit + `-NoPause` switch
    (CLAUDE.md rules).
