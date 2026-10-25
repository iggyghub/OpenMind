# Mark mode -- what UI editing needs, and what to add

Output of `UI-EDIT-NEEDS-PROMPT.md` run against `tray/lib/ui-marks.js` as of
2026-10-24 (branch ui-mark-mode). Grounded in the real marks from the first four
cleanup batches, not guesses. Input for the pending grill.

**Today a mark records:** CSS selector, pane (`data-route`), first 80 chars of
text, ~11 computed styles, a typed note. Stored in localStorage; delivered by
"Copy all" -> paste into Claude Code.

## 1-8: what the editing loop needs, vs. mark mode today

| # | Need | Example from this project | Mark mode today |
|---|---|---|---|
| 1 | Point at one element | "this closes when I press Recheck" | **covered** |
| 1 | Point at *every element like this* | "all deletes should be a red x"; "same with the other sub groups" | missing -- said in the note, I had to guess the set |
| 1 | Point at a gap / region / nothing | "add a delete button here" (no element exists yet) | partly -- you mark the neighbour |
| 1 | Point at a state | hover, loading, the "Loading..." stuck state | missing -- hover state is lost the moment you click |
| 1 | Point at a size | header wraps at narrow widths | missing -- window width not recorded |
| 2 | Intent kind | remove / change / bug / add / design-idea | missing -- free text only; "remove this" vs "this is broken" read the same |
| 2 | "Make it like that one" | "use the same red as X" | missing -- needs a second pick linked to the first |
| 2 | Design spec beyond words | the line-morphs-into-tile mark (mark #3, 70 words) | missing -- a sketch or a picked reference would say it faster |
| 3 | Screenshot of what you saw | every mark -- I took separate window captures to understand them | missing -- **the biggest gap** |
| 3 | App state | which sub-tab, scroll, data on screen, window size | partly -- pane only |
| 3 | Version | which commit the mark was made on | missing -- matters once fixes land mid-session |
| 3 | Errors at the time | the Log pane's silent "Loading..." | missing -- console errors not captured |
| 4 | Stable locator | `...section.pane:nth-of-type(10) > div.set-subpane...:nth-of-type(8)` broke-prone; I actually located marks by *text* | partly -- brittle nth-of-type chains |
| 4 | Source location | which file/function renders it | missing -- I grep every time |
| 5 | Status lifecycle | marks stay until Clear, so a re-paste resends fixed ones | missing -- no open/fixed/verified |
| 5 | Verify the fix | "Ctrl+R and check" -- you hunt for each change yourself | missing -- no "show me what changed" |
| 6 | Batch + group | 3 Settings marks became 1 commit | **covered** by Copy all |
| 6 | Survive re-render | outline vanished after the pane redrew | missing -- the mark is kept, its highlight is not |
| 7 | Typed note | | **covered** |
| 7 | Voice note | your idea | missing |
| 7 | Direct manipulation as a spec | "a bit smaller", "less padding" -- drag it and record the *target value* | missing -- this is what the UI Editor got wrong (it baked output) |
| 8 | Delivery without paste | marks land where the fixer reads them | missing -- clipboard only |

## 9: recommendations -- best value for effort, build order

1. **Screenshot per mark.** Crop of the element plus some margin, outline drawn
   in. Felix: Electron's `webContents.capturePage(rect)`; a browser extension:
   `chrome.tabs.captureVisibleTab`. *Done:* every pasted/sent mark has an image the
   fixer can open.
2. **Intent chips + "all like this".** One-click chips under the note box:
   Fix / Remove / Move / Add here / Bug / Idea, plus a scope toggle *This one* /
   *All like this* (the tool shows how many matches it found, using the class list,
   so you see "7 matched"). *Done:* the red-x request would arrive as
   `scope: all, matched: 7`.
3. **Better locator + state.** Record a short, stable locator (nearest id + role +
   text), the active sub-tab, window size, scroll position, the git commit, and any
   console errors since the last mark. Drop the `nth-of-type` chains unless needed.
   *Done:* a mark still resolves after an unrelated pane re-render.
4. **Status loop, no paste.** Marks go to a file the fixer reads directly
   (`.marks/marks.jsonl` in the repo, through Felix's own gate). The fixer flips
   them `fixed` with a commit; your bar shows "3 fixed - review" and clicking one
   jumps to the element and highlights it (green) so you confirm or reopen.
   *Done:* you never paste, and nothing gets re-sent twice.
5. **Re-attach highlights after re-render.** Re-find each open mark by its locator
   whenever the DOM changes (MutationObserver, same trick as `watchDetails`).
   *Done:* marked things stay outlined across refreshes.

**Bigger bets**

- **Nudge-to-spec.** Select an element, drag its padding/size, scroll its font
  size, pick a colour -- previewed live, recorded as `want: {padding: 8px}`, never
  baked. Precise where words are vague. Risk: medium (a small style-panel UI).
- **Voice notes.** Hold a key while hovering, speak, release -> the note is the
  transcript. Felix already runs faster-whisper, so this is a wiring job inside
  Felix; for other apps it needs a local STT. Risk: low inside Felix.
- **Reference picks.** "Like that one": after marking, pick a second element;
  the mark carries both locators and their style diff. Risk: low.
- **Sketch layer.** Draw a box/arrow on the screenshot for spatial ideas (the
  tile-morph mark). Risk: low-medium.
- **Source mapping.** In dev builds, stamp `data-src="file:line"` on elements, or
  use source maps / framework devtools hooks (React fiber `_debugSource`, Vue
  `__file`). Risk: per-framework; skip for hand-written HTML like Felix's main.html,
  where text + id grep already works.

## 10: can it attach to any open-source app?

**Mostly yes, in three tiers.** The trick is to split mark mode into a portable
*mark format* plus a different *capture backend* per UI technology.

| UI technology | Examples | How to attach | Quality of marks |
|---|---|---|---|
| Web apps | Any OSS web UI, self-hosted dashboards | Browser extension injects the same script | Full: DOM, styles, screenshot |
| Electron / Chromium apps | VS Code, Joplin, Element, Logseq, Signal Desktop | Launch with `--remote-debugging-port`, inject over the Chrome DevTools Protocol; no change to the app | Full |
| WebView2 / Tauri (Windows) | many newer Tauri apps | `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=...` then CDP, as above | Full, if the app doesn't block it |
| Native desktop | Qt, WPF, WinForms, GTK, Flutter | OS accessibility tree (Windows UI Automation; AX on macOS; AT-SPI on Linux) -- pick by bounding box, get role, name, automation id | Partial: name, role, position, no styles. Source found by searching the repo for the label or object name |
| Canvas / GPU / games | Blender viewport, game menus | Screenshot region + OCR | Pixel only: a box and a note |

What is **not** portable: the *fixing*. A mark only tells someone precisely what to
change. For an app you don't own, the change lands in your fork, built your way.
The fixer (Claude Code with that repo checked out) is what makes it useful.

**Suggested shape:** a `mark-kit` -- (1) the current ui-marks.js core, framework-free;
(2) injectors: Felix (built in), browser extension, CDP launcher, UIA picker;
(3) one JSON mark schema all of them write; (4) a resolver that turns a mark into
"likely file:line" by text/id/class search in the checked-out repo.

Felix already has the pieces for the native tier: the UI Automation work from the
computer-use campaign (ADR-0016) is exactly the element-picking needed there.

## Grill decisions (2026-10-24)

1. **Identity: both from day one.** A shared `mark-kit` core + Felix as its host.
2. **Home:** `tools/mark-kit/` in this repo, own README/tests/schema, no imports
   from `tray/` or `cerebral/` (test-enforced). Felix loads it by path. Split to
   its own repo (`git subtree split`) once an outside user exists.
3. **Store:** `.marks/marks.jsonl` + `.marks/shots/` in the marked app's source
   checkout, always gitignored. Felix writes via a gated Cerebral tool;
   standalone via a tiny local server; Copy all stays as the fallback.
4. **The bar:** draggable (position remembered). Buttons: Copy all (kept) and a
   split **Check the marks** button -- click = fix with the remembered model,
   side arrow = pick another model (first-ever default: coding model, else chat).
   Menu shows local vs cloud per model.
5. **Fix engine (Felix):** generalize `self_dev` into a fix pipeline with a
   target-repo setting: clone -> edit -> test -> PR, restart only when the
   target is Felix. Built via self_dev itself (it's Felix's core).
6. **Open contract for other AIs:** mark-kit is a package someone hands to any AI
   to integrate: core JS + `mark.schema.json` + `INTEGRATE.md` (also a valid
   SKILL.md; per-UI-technology recipes, ends with a round-trip check) + `FIX.md`
   (copied into `.marks/` on first mark). Felix is customer #1 and must be wired
   by following INTEGRATE.md. MCP server later, on demand.
7. **Privacy:** inputs blacked out in screenshots, input values never recorded,
   `.marks/` always gitignored.
8. **Screenshots (decided technically):** core asks the host for a capture --
   Electron capturePage (Felix), captureVisibleTab (extension), CDP
   Page.captureScreenshot (attached Electron apps), OS capture (native); plain
   web with no host hook = no image, mark still works.

9. **Mark types:** Fix, Remove, Move, **Add** (kind: page/tab/section/control/
   field, with an optional wireframe sketch on the screenshot), **Link** (pick A,
   pick B, relation: opens/navigates to, shows data from, stays in sync with,
   looks like), Bug, Idea. Scope toggle This one / All like this (match by class
   list, live highlight + count before Enter). Mark-kit is a request layer on an
   existing codebase -- not a website builder (owns output) or UI builder
   (generates toolkit layout files).
10. **Voice: later.** Design on file: hold-to-talk on a pending mark, reusing
    Felix's PTT/faster-whisper (Cerebral routes the transcript to the bar), leading
    chip words auto-select chips; "Felix, mark this" hands-free after that; other
    hosts: Web Speech API where it works (Chrome, not Electron), else no mic.
11. **Lifecycle:** open -> fixing -> fixed (commit/PR) -> verified | reopened (with
    note); declined (fixer's reason, overridable). Never deleted. Bar shows counts;
    clicking jumps to the element, green outline, before-shot vs now, Looks good /
    Reopen.

12. **Order: working in Felix first.** A shippable mark-kit (second host, packaging,
    INTEGRATE.md polish) is a separate later session. Keep the core host-agnostic
    while building so that session is extraction, not rewrite.
13. **Locator/state (technical, decided in build):** short stable locator (nearest
    id + role + text, nth-of-type only when needed) plus route, sub-tab, window
    size, scroll, git commit, console errors since the last mark.

## Build plan -- Felix first (one PR per slice)

Start from branch `ui-delete-x` (has mark mode + the cleanup fixes). Each slice:
jest green, verify in the preview with sendEvent stubbed (it talks to LIVE
Cerebral), Help topic updated in the same PR.

- **M1 Core + bar.** Move `tray/lib/ui-marks.js` -> `tools/mark-kit/mark-kit.js`
  (UMD, no Felix imports; a test greps for tray/cerebral imports). Felix loads it by
  path. Draggable bar, position remembered. Stable locator + state fields (13).
  Highlights re-attach after re-render (MutationObserver). Draft `mark.schema.json`.
- **M2 Mark types.** Chips Fix/Remove/Move/Add/Link/Bug/Idea; Add kind picker;
  Link = second pick + relation; This one / All like this with live match count.
- **M3 Screenshots + privacy.** Host capture hook. Open question for this slice:
  Felix's main window has no preload (renderer-portability rule, ADR-0007), so
  capture goes either via a minimal capture-only preload or Cerebral's OS
  screenshot of the window rect -- pick one, ADR note if it's the preload. Inputs
  blacked out before capture, values never recorded. Add-mark wireframe sketch.
- **M4 Store.** Gated Cerebral tools (marks append/list/update) writing
  `.marks/marks.jsonl` + `shots/` in the target repo; auto-add `.marks/` to
  .gitignore; copy `FIX.md` in on first mark. localStorage becomes the offline
  buffer; Copy all stays. Needs `cerebral/tests/test_plugin_<name>.py` or it boots
  silently disabled (CLAUDE.md).
- **M5 Review loop.** Statuses (11), bar counts, jump + green outline + before
  shot vs now, Looks good / Reopen, declined reasons.
- **M6 Check the marks.** Split button + model menu (remembered pick, local/cloud
  label). Generalize self_dev's clone->edit->test->PR to a target repo, restart
  only when the target is Felix. **Built through self_dev** (Felix's core).
- **M7 Docs.** `INTEGRATE.md` (valid SKILL.md, per-tier recipes, round-trip
  check), `FIX.md`, schema docs. Check Felix's wiring matches INTEGRATE.md.

Later (own sessions): voice (10), second host + shippable package.
