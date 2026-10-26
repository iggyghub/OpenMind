---
name: mark-kit-integrate
description: Wire mark-kit (point at a live UI, leave typed change requests with screenshots, hand them to a fixer) into an app. Use when asked to "add mark mode", "integrate mark-kit", or let someone mark up an app's UI for an AI or developer to fix. Covers web apps, Electron/Chromium, WebView2/Tauri, and what to do for native UIs, and ends with a round-trip check.
---

# Integrating mark-kit

mark-kit lets a person press **Ctrl+Shift+M** in a running app, click an element,
say what should change (Fix / Remove / Move / Add / Link / Bug / Idea, this one or
all like it), and leave it as a **mark**: a JSON object with a stable locator, the
element's text and key styles, app state, and a screenshot. A fixer (an AI with
the app's source checked out, or a person) reads the marks, changes the code, and
marks them fixed; the person reviews each fix in the app.

You are integrating it into a **host**: the app being marked. The core is one
dependency-free file. Your job is to load it and give it up to six hooks. Every
hook is optional; with none, mark mode still works and "Copy all" puts the marks
on the clipboard.

Files in this folder:

| File | What it is |
|---|---|
| `mark-kit.js` | The core. UMD: `window.MarkKit` in a page, `module.exports` in Node. No imports. |
| `mark.schema.json` | The mark format. The contract between page, store and fixer. |
| `FIX.md` | Instructions for the fixer. A store copies it into `.marks/` on the first mark. |
| `README.md` | Short overview and how to run the tests. |
| `tests/` | `node --test "tests/*.test.js"` |

## 1. Load the core and call init

```html
<script src="path/to/mark-kit.js"></script>
<script>
  var kit = MarkKit.init(document, {
    context:  function (el) { return { route: location.hash }; },  // stored as mark.app
    capture:  function (rect) { return Promise.resolve(null); },   // -> PNG data URL | null
    store:    { add: ..., list: ..., update: ... },                // see step 3
    show:     function (mark) { /* switch to mark.app's screen */ },
    shotUrl:  function (mark) { return null; },                    // URL of a stored screenshot
    fix:      { models: ..., preferred: ..., run: ... },           // see step 4
  });
  // kit.reload() re-reads the store; call it when your store changes.
</script>
```

Load it after `<body>` exists. It adds one `<style>`, one fixed bar (`#uim-bar`),
and capture-phase listeners that swallow clicks only while mark mode is on.

**`context(el)`** -- add whatever locates the mark in *your* app: the route, the
open tab, the selected record's id. Keep it to names, never user data.

## 2. Screenshots: the `capture(rect)` hook

`rect` is `{x, y, width, height}` in viewport CSS pixels (the element plus a 40 px
margin). While the promise is pending, mark-kit blacks out every input, textarea,
select and contenteditable and hides its own bar, then restores them (or after
3 s). Return a PNG data URL, or `null` for no image. Pick the recipe for your UI
technology:

| UI technology | How to capture | Notes |
|---|---|---|
| **Electron, you own it** | Main process: `win.webContents.capturePage(rect)` then `.toDataURL()`. Reach it however your renderer already talks to main (IPC via preload, or a WebSocket). | Pixel-exact even when the window is covered. Felix does this without a preload; see step 6. |
| **Web page via a browser extension** | Content script asks the background: `chrome.tabs.captureVisibleTab({format:'png'})`, then crop to `rect` scaled by `devicePixelRatio` on a canvas. | Inject `mark-kit.js` as a content script. Visible area only. |
| **Electron / Chromium app you don't own** | Launch it with `--remote-debugging-port=9222`, inject `mark-kit.js` with CDP `Runtime.evaluate`, capture with `Page.captureScreenshot({clip: {...rect, scale: 1}})`. | No change to the app. Wire the hooks over the same CDP session (`Runtime.addBinding`). |
| **WebView2 / Tauri (Windows)** | Set `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222` before launch, then as above. | Works unless the app blocks it. |
| **Plain web page, no host process** | No hook. | Marks work, no image. |
| **Native (Qt, WPF, WinForms, GTK, Flutter)** | The JS core does not apply. Build a picker on the OS accessibility tree (Windows UI Automation, macOS AX, AT-SPI) and write marks in `mark.schema.json` shape with `selector` = automation id / name path. | Partial: name, role, bounds; no styles. |
| **Canvas / games** | Screenshot region + note only. | Pixel only. |

Never send typed values anywhere: mark-kit already leaves field text out of every
mark. Keep it that way in `context`.

## 3. Storing marks: the `store` hook

```js
store: {
  add:    function (mark) { /* -> Promise<stored mark | null> */ },
  list:   function ()     { /* -> Promise<marks[] | null> */ },
  update: function (id, patch) { /* -> Promise<mark | null>; patch = {status, note?, reason?, pr?} */ },
}
```

With a store, localStorage only buffers marks the store has not confirmed; they
are re-sent the next time mark mode opens. The store must:

1. Write to **`.marks/` in the marked app's own source checkout**:
   `marks.jsonl` (one mark per line), `shots/<id>.png`, `FIX.md` (copy of this
   folder's, on the first mark). Add `.marks/` to that repo's `.gitignore`.
2. Keep the page's `id`. Re-adding an existing id returns the stored mark
   unchanged (the page re-sends after outages).
3. Take `mark.shot` (a PNG data URL) off the mark, write it to `shots/<id>.png`,
   set `shot_path`. Never put image data in the JSONL line.
4. Set `status: "open"`, `history: [{status, at}]`, and `commit` (HEAD).
5. On `update`, append to `history`; never delete a mark. Statuses:
   `open -> fixing -> fixed -> verified`, `reopened` (with a note), `declined`
   (with the fixer's reason).
6. Resolve `null` (or `{error}`) when it can't store; the page keeps the mark.

Reference implementation: `plugins/marks.py` in OpenMind (Python, ~300 lines).

**`show(mark)`** and **`shotUrl(mark)`** power the review: for each fixed mark the
bar calls `show` (switch to its screen), finds the element, outlines it green, and
shows `shotUrl(mark)` beside it: the screenshot from when it was marked.

## 4. Fixing: the `fix` hook (optional)

Adds a split **Check the marks** button: click = run with the remembered model,
the arrow = pick another (remembered in localStorage).

```js
fix: {
  models:    function () { /* -> Promise<[{id, label, where}]>, where = local | remote | cloud */ },
  preferred: function () { /* -> model id for the very first run, or null */ },
  run:       function (modelId) { /* -> Promise<{marks: n} | {error}> */ },
}
```

`run` should hand every `open`/`reopened` mark to a fixer, move them to `fixing`
at once, and later to `fixed` (with the PR) or back to `reopened` (with why).
Call `kit.reload()` when that happens. Without this hook, the fixer is whoever
reads `.marks/` (`FIX.md` tells them how).

## 5. Round-trip check (do this before you call it done)

1. Open the app, press **Ctrl+Shift+M**: the bar appears bottom-right. Drag it by
   the handle; reload; it is where you left it.
2. Click a button. Its outline turns solid. Pick **Remove**, type a note, Enter.
   The count goes to 1.
3. Pick a second element, choose **All like this**: the count of matches shows
   and the matches get dotted outlines. Save it.
4. Type something into a text field first, then mark the field. The mark's
   `text` is empty, and in its screenshot the field is black.
5. With a store: `.marks/marks.jsonl` has both lines with `status: "open"`, a
   `commit`, and `shot_path` files that exist; `.marks/` is in `.gitignore`;
   `.marks/FIX.md` exists. Without a store: **Copy all** pastes both marks.
6. Act as the fixer: set one mark to `fixed` (store `update`, or edit the line
   and append to `history`). Reopen mark mode: the bar says "1 fixed - review".
   Click it: the element is outlined green with the old screenshot beside it.
   **Reopen** with a note: the line now has `status: "reopened"` and the note
   in its last `history` entry.
7. Reload the page: open marks are outlined again, and stay outlined after the
   app re-renders that part of the page.

## 6. Worked example: Felix (OpenMind)

Felix's Main window is an Electron renderer with **no preload** and talks to its
backend (Cerebral) over one WebSocket. Its wiring, by hook:

| Hook | Felix | File |
|---|---|---|
| load | `<script src="../../tools/mark-kit/mark-kit.js">` | `tray/windows/main.html` |
| all hooks | `MarkKitHost.create(send)`; `onEvent` called from the page's WebSocket handler | `tray/lib/mark-kit-host.js` |
| context | pane `data-route` + visible active sub-tab | `mark-kit-host.js` |
| capture | renderer -> `mark_capture` -> Cerebral relay -> tray main `capturePage` -> `mark_capture_result` | `tray/lib/mark-capture.js`, `tray/main.js`, `cerebral/main.py`; ADR-0007 amendment |
| store | gated tools `marks_add` / `marks_list` / `marks_update` via `call_tool` (`record:false`) | `plugins/marks.py` |
| show / shotUrl | `location.hash = '#' + route`; `file:///` + `shot_abs` | `mark-kit-host.js` |
| fix | `marks_fix` -> `self_dev` (clone, edit with the picked model, test, PR) through the permission gate | `plugins/marks.py`, `plugins/self_dev.py`; ADR-0015 amendment |
| reload | `marks_changed` broadcast -> `kit.reload()` | `mark-kit-host.js` |
