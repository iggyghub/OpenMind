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

## Status: done

<!-- ready = slices remain; done = S12 landed; blocked = a session needs a human -->

## Next slice -- start here

- **Active:** none (S12 is the last slice)
- **Model:** sonnet

## Queue

- [x] S1 -- #1063 -- verify + fix drag/resize/text/font/reset, add save-cycle tests (PR #1079)
- [x] S2 -- #1064 -- bake overrides into the source HTML file (PR #1080) (local targets only)
- [x] S3 -- #1065 -- undo/redo for edits (PR #1081)
- [x] S4 -- #1066 -- element outline/tree panel for selection (PR #1082)
- [x] S5 -- #1067 -- multi-select + bulk style edit (PR #1083)
- [x] S6 -- #1068 -- safety/regression pass (path traversal, input validation, README)
- [x] S7 -- #1073 -- block palette: insert new elements (not just edit existing ones)
- [x] S8 -- #1074 -- section template library (navbar, hero, feature grid, pricing, footer, contact form, gallery)
- [x] S9 -- #1075 -- expanded style panel (spacing box model, flex/grid layout, border/radius/shadow)
- [x] S10 -- #1076 -- responsive breakpoints (mobile/tablet/desktop preview + per-breakpoint overrides)
- [ ] S11 -- #1077 -- asset manager (image upload + insert/replace)
- [x] S12 -- #1078 -- code view / HTML export panel (PR #1090)

Per-slice model: sonnet unless the queue entry says otherwise.

### Why S7-S12 (added 2026-09-04)

Self-grilled against GrapesJS/Webflow/Wix-style builders: S1-S6 make the tool
a solid *element editor* (move/resize/recolor/retext what's already on the
page) but it still can't originate a page -- there's no way to add anything
new. "Build a large variety of websites" needs: a block palette to insert
elements (S7), prebuilt multi-element section blocks so a whole site doesn't
require placing every `<div>` by hand (S8), a real style panel beyond
bg/fg/font-size (S9), responsive breakpoints since every reference builder
has them (S10), an asset manager for images (S11), and a code/export view
so the result is portable (S12). Same SAFETY rules apply; S7 is the
prerequisite for S8 (blocks are built from the same insert primitive).

## Landed PRs

- S1 -- #1063 -- PR #1079
- S2 -- #1064 -- PR #1080
- S3 -- #1065 -- PR #1081
- S4 -- #1066 -- PR #1082
- S5 -- #1067 -- PR #1083
- S6 -- #1068 -- PR #1084
- S7 -- #1073 -- PR #1085
- S8 -- #1074 -- PR #1086
- S9 -- #1075 -- PR #1087
- S10 -- #1076 -- PR #1088
- S12 -- #1078 -- PR #1090

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
