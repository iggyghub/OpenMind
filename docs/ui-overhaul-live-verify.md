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
