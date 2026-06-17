# UI & Harness Overhaul — Human Live-Verify Checklist

Each slice below lists the visual/interactive checks that a headless run cannot
make. Run these in the live app after merging the slice's PR.

---

## S1 — Render-smoke harness + live-verify doc (#284)

S1 introduces no visible UI change — it only builds the headless smoke harness
and creates this document. Verify the harness itself works:

- [ ] Run `npm test` inside `tray/` and confirm all tests pass (including the
      new `render-smoke` suite).
- [ ] Confirm `.claude/tmp/render-smoke/last-run.json` is written after the
      run and lists all 9 expected routes in `panes_found` and `nav_items_found`.
- [ ] Open the live Felix window and confirm the existing UI is unchanged: all
      9 sidebar nav items (Conversation, Queue, Insights, Memory, Permissions,
      Credentials, Plugins, Profiles, Settings) are present and their panes
      activate on click.

---

## S2 — Grouped sidebar nav (#285)

- [ ] Open the live Felix window and confirm the sidebar nav is now grouped into
      four sections with headers: CHAT, MIND, TOOLS, SYSTEM.
- [ ] CHAT section contains: Conversation, Queue, Conversations.
- [ ] MIND section contains: Insights, Memory, Recipes.
- [ ] TOOLS section contains: Plugins, Integrations, Credentials, Permissions.
- [ ] SYSTEM section contains: Models, Settings, Profiles.
- [ ] Each section header label is visible and styled (small caps, muted colour).
- [ ] All pre-existing panes still activate on click (Conversation, Queue,
      Insights, Memory, Credentials, Permissions, Plugins, Profiles, Settings).
- [ ] Clicking Conversations, Recipes, Integrations, Models each shows a stub
      placeholder pane with a title, description, and "Coming in issue #N" label.
- [ ] Hash routing still works: navigating to e.g. `#memory` activates the
      Memory pane and highlights the Memory nav item.
- [ ] The active nav item retains the accent left-border highlight.

---

## S3 — Collapsible section headers (#286)

- [ ] Open Settings. Confirm each section header (Active model, Switch model,
      Per-task models, Notifications, System) shows a right-aligned chevron (▾).
- [ ] Click "Notifications" header — its rows collapse (hidden) and the chevron
      changes to ▸.
- [ ] Click "Notifications" again — rows expand and chevron returns to ▾.
- [ ] Reload the page. Navigate back to Settings. Confirm "Notifications" is
      still collapsed (state survived reload).
- [ ] Open Permissions → Capabilities tab. Confirm its three section headers
      (Capability classes, Session grants, New plugins to review) each have a
      chevron and are click-to-collapse.
- [ ] Open Plugins. Confirm "Registered plugins" header has a chevron and
      collapses/expands its plugin list.
- [ ] Open Profiles. Confirm "Profiles" header has a chevron and collapses the
      profile list and New profile button when clicked.
- [ ] Confirm Queue and Insights item-level collapse is unchanged (rows still
      start collapsed as before; section headers in those panes are unaffected).

---

## S4 — Federated search shell (#287)

- [ ] Open the live Felix window. Confirm the header has a search input
      between the "Felix" title and the health/state pill (placeholder text:
      "Search this pane and everywhere else...").
- [ ] Confirm the search bar is visible from EVERY pane (switch through
      Conversation, Queue, Insights, Memory, Permissions, Credentials,
      Plugins, Profiles, Settings, Models, Conversations, Integrations,
      Recipes) -- it lives in the static header, not in any single pane.
- [ ] Open the Plugins pane. Type a substring of a plugin name (e.g. `gmail`).
      Confirm only plugin rows whose name contains the substring stay visible;
      other rows are hidden. Clear the input -- all rows return.
- [ ] Still on Plugins, type a query that matches NOTHING in plugins but
      matches a setting (e.g. `notifications`). Confirm the "Found elsewhere"
      dropdown opens under the search bar with at least one Settings hit
      tagged with the route `settings`.
- [ ] Click the "Notifications" jump link. Confirm the active pane switches
      to Settings and the Notifications row is scrolled into view.
- [ ] Open Credentials and set at least one API key (or just observe an
      existing setup). Then type the FIRST FEW CHARS of any saved API token
      value (NOT the label, the actual secret). Confirm NO credentials row
      appears anywhere -- not in current pane, not in "Found elsewhere".
- [ ] Type a credential's label (e.g. `Google`, `OpenAI`, `Todoist`).
      Confirm the matching row appears with a status word ("connected",
      "set", "not set", etc.) -- never with the actual token value.
- [ ] Open Permissions -> Tools tab. Type into the header search and confirm
      tools matching the query appear in "Found elsewhere" with route
      `permissions`. Clicking one navigates to the Permissions pane.
- [ ] Press the header search input, then press Escape or click outside the
      search column. Confirm the "Found elsewhere" dropdown closes.
- [ ] Confirm that switching panes while the search box still has text
      re-applies the in-pane filter on the new pane (e.g. switching from
      Plugins with `gmail` typed to Permissions filters Permissions tools
      live without re-typing).

---

## S5 — Models tab (#288)

- [ ] Open the live Felix window. Click **Models** in the SYSTEM section of the
      sidebar. Confirm the pane shows three section headers: "Active model",
      "Switch model", and "Per-task models", plus a "Refresh installed models"
      button.
- [ ] Confirm **Settings** (SYSTEM section) no longer shows any model controls
      — only "Notifications" and "System" sections remain.
- [ ] With Cerebral running: confirm the Models pane shows the currently active
      model name and a local/cloud badge under "Active model".
- [ ] Click a different model in the "Switch model" list. Confirm the active
      model updates (radio dot moves, name in the header updates).
- [ ] Expand a task card in "Per-task models" and assign a model to one task.
      Confirm the card's current model label updates.
- [ ] Click "Refresh installed models". Confirm the button briefly shows
      "Refreshing..." then re-enables; the model list updates if Ollama is
      running.
- [ ] Type "active model" in the header search bar from any pane other than
      Models. Confirm the "Found elsewhere" dropdown shows an "Active model"
      hit with route `models`. Click it — confirm navigation to the Models pane.
- [ ] Confirm the S3 collapsible chevrons appear on each section header in the
      Models pane (Active model, Switch model, Per-task models) and that
      collapse/expand works.

---

## S6 — Appearance settings (#289)

- [ ] Open the live Felix window and navigate to **Settings** (SYSTEM section).
      Confirm an **Appearance** section is present at the top of the Settings
      pane, above Notifications. Confirm the Appearance section has a collapsible
      chevron (S3 behaviour).
- [ ] Under Appearance, confirm three controls are visible: a **UI scale**
      dropdown (90% / 100% / 110% / 125%), a **Theme** chip group (Midnight /
      Light / High Contrast), and an **Accent colour** picker.
- [ ] The **Midnight** theme chip should appear active (highlighted in coral)
      by default.
- [ ] Change UI scale to **110%**. Confirm the entire Felix window zooms in
      immediately. Reload the page — confirm it opens at 110%.
- [ ] Click the **Light** theme button. Confirm the background, sidebar, and
      text colours all change immediately to a light palette. Reload — confirm
      the Light theme is still applied.
- [ ] Click **High Contrast**. Confirm the UI switches to a high-contrast
      black-and-white palette. Reload — still HC.
- [ ] Click **Midnight** to restore the default dark theme.
- [ ] Open the **Accent colour** picker and choose a different colour (e.g.
      green or red). Confirm the accent changes live: the active nav item
      left-border, the orb glow, the state pill border (when speaking), and
      other accent-coloured elements update immediately. Reload — accent persists.
- [ ] Set the scale back to **100%** and accent back to the default purple
      (#7c5cfc). Reload and confirm both are restored.
- [ ] From a pane other than Settings, type **"theme"** in the header search
      bar. Confirm a hit for "Theme" appears in the "Found elsewhere" list
      with route `settings`. Click it — confirm navigation to Settings.
- [ ] Type **"accent"** in the search bar from any other pane. Confirm an
      "Accent colour" hit appears. Type **"ui scale"** — confirm a "UI scale"
      hit appears.
