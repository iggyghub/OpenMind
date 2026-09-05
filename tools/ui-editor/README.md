# UI Editor

A local click-to-edit overlay tool. Proxies a local file (rooted at this repo) or a remote URL into an iframe and injects a drag/resize/color/text editing overlay. Edits persist as a JSON overrides layer and are replayed on next load; source files are never touched until you explicitly bake.

## Running

```
node tools/ui-editor/server.js
```

Open `http://localhost:4545`. Choose a target type, enter a path or URL, and click **Open**. Toggle **Edit mode** (top-right inside the loaded page) to start editing.

## Controls

| Control | How |
|---|---|
| **Select** | Click any element in Edit mode |
| **Multi-select** | Shift-click additional elements; Escape or plain click to clear |
| **Drag** | Mouse-down inside the highlight box and drag (single selection only) |
| **Resize** | Drag the four corner handles (single selection only) |
| **Background color** | BG color picker in toolbar (applies to all selected elements) |
| **Text color** | Text color picker in toolbar (applies to all selected elements) |
| **Font size** | Font size number input in toolbar (applies to all selected elements) |
| **Edit text** | "Edit text" button — makes the element contenteditable (single selection only) |
| **Undo / Redo** | Undo/Redo buttons or Ctrl+Z / Ctrl+Y (up to 50 steps) |
| **Bake** | "Commit to file" button — writes all overrides inline into the source HTML file (local targets only) |
| **Reset** | "Reset this page" button — clears all overrides and reloads |
| **Element tree** | Expandable "Elements" panel in the toolbar for tree-based selection |

## Target types

- **Local file** — a path relative to the repo root, e.g. `tray/windows/main.html`
- **New page** — seeds a blank `.html` file at the given path, then opens it as a local file
- **URL** — any reachable `https://` page (optional HTTP Basic Auth)
- **Git repo** — clones/pulls the repo and serves a file from the checkout
- **FTP** — fetches one file over plain FTP (passive mode; no STOR/upload yet)

## What this can't do

- **Remote sites can't be baked.** The "Commit to file" button is only available for local targets. Remote pages (URL, Git, FTP) get a live-preview overlay only — the edits persist as a local JSON layer and are re-applied on each proxy load, but the remote source is never modified.
- **Element identity drifts on dynamic pages.** Elements are identified by a DOM-index path (tag name + child index per ancestor). If the page reorders, inserts, or conditionally renders elements before load, the path may point to the wrong element and overrides will misfire or silently no-op.

## Tests

```
node --test "tools/ui-editor/tests/*.test.js"
```

Tests cover `sanitizeKey`, `injectIntoHtml` (base-href insertion, CSP stripping, script tag placement), save/load/reset round-trip, bake/findByPath/mergeStyleAttr, undo/redo stack logic, element-tree helpers, and path-traversal containment + input validation. No test framework, no fixtures — plain `node:test` + `node:assert`.
