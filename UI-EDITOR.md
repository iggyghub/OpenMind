# UI-EDITOR.md -- click-to-edit visual UI tool campaign driver

Autonomous slice loop for `tools/ui-editor/` -- a local click-to-edit overlay
that proxies a local file (rooted at this repo, e.g. Felix's own
`tray/windows/*.html`) or a remote URL you have permission to edit into an
iframe, injects a select/drag/resize/color/text overlay, and persists edits
as a JSON overrides layer. `scripts/run-ui-editor.ps1` drives this file. Each
slice = one issue = one PR, merged to master before the next starts
(successive slices edit the same `tools/ui-editor/inject.js` and `server.js`).

No ADR / spec doc for this one -- it's a standalone dev tool, not core
product surface. Each issue body is the full spec for its slice.

## Status: ready

<!-- ready = slices remain; done = S6 landed; blocked = a session needs a human -->

## Next slice -- start here

- **Active:** S1 -- #1063
- **Model:** sonnet

## Queue

- [ ] S1 -- #1063 -- verify + fix drag/resize/text/font/reset, add save-cycle tests
- [ ] S2 -- #1064 -- bake overrides into the source HTML file (local targets only)
- [ ] S3 -- #1065 -- undo/redo for edits
- [ ] S4 -- #1066 -- element outline/tree panel for selection
- [ ] S5 -- #1067 -- multi-select + bulk style edit
- [ ] S6 -- #1068 -- safety/regression pass (path traversal, input validation, README)

Per-slice model: sonnet unless the queue entry says otherwise.

## Landed PRs

## SAFETY

- Scope: touch only `tools/ui-editor/**` (server.js, inject.js, public/,
  tests/, README.md). Never touch `cerebral/`, `plugins/`, or `tray/`
  (Felix's real Electron app) -- the tool only *proxies a read* of files like
  `tray/windows/main.html` at runtime; a campaign slice must not modify
  Felix's own source. Baking (S2) writes only to whatever local HTML path a
  *user* points the running tool at during manual use, never as a side
  effect of a slice building/testing the feature.
- No new dependencies without a one-line justification in the PR body --
  default to Node's stdlib and built-in test runner (`node --test`), no
  bundler, no framework, matching the house rule against introducing one
  for the renderer (see CONTEXT.md, "Text widget").
- No security-sensitive or capability-gate code (ADR-0005) is anywhere near
  this tool -- every slice here is AFK (auto-merge on green), not HITL.
- If a slice genuinely needs a human decision, set `Status: blocked` with a
  one-line reason, commit that to master, and stop without merging.
