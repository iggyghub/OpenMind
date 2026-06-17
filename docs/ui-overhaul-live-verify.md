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
