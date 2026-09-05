# UI Editor

A local click-to-edit overlay tool. Proxies a local file (rooted at this repo) or a remote URL into an iframe and injects a drag/resize/color/text editing overlay. Edits persist as a JSON overrides layer and are replayed on next load; source files are never touched.

## Running

```
node tools/ui-editor/server.js
```

Open `http://localhost:4545`. Choose a target type, enter a path or URL, and click **Open**. Toggle **Edit mode** (top-right inside the loaded page), click an element, and use the toolbar to drag, resize, change colors, edit text, or reset.

## Target types

- **Local file** — a path relative to the repo root, e.g. `tray/windows/main.html`
- **New page** — seeds a blank `.html` file at the given path, then opens it as a local file
- **URL** — any reachable `https://` page (optional HTTP Basic Auth)
- **Git repo** — clones/pulls the repo and serves a file from the checkout
- **FTP** — fetches one file over plain FTP (passive mode; no STOR/upload yet)

## Tests

```
node --test "tools/ui-editor/tests/*.test.js"
```

Tests cover `sanitizeKey`, `injectIntoHtml` (base-href insertion, CSP stripping, script tag placement for local and remote targets), and the save/load/reset file round-trip. No test framework, no fixtures — plain `node:test` + `node:assert`.

## Limitations

- Element identity is a DOM-index path (stable while page structure is static; drifts if the page reorders elements before load).
- FTP: read-only (RETR only, no STOR).
- SFTP: not supported.
- Overrides baking (writing edits back to the source HTML file) is planned for S2.
