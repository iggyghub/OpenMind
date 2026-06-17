# OpenMind — UI & Harness Overhaul (autonomous campaign)

Driver for `scripts/run-ui-overhaul.ps1`. Read `CONTEXT.md`, `CLAUDE.md`, and
**`docs/ui-overhaul-spec.md`** first, then this file. Spec = the locked design;
each issue body = that slice's detail.

---

## Next slice — start here

**UI/harness overhaul epic.** 20 tracer slices, one PR each, auto-merged to master
in order (every slice edits `tray/windows/main.html`, so unmerged parallel slices
would collide — they MUST land sequentially). Work strictly top-down through the
queue below: respect each slice's `Depends on:` (the order already satisfies it).
After landing a slice, **update this block**: tick the entry in the queue, set the
next unticked entry's `#N` + `Model:` as the active slice here, and set `Status:`
(`ready` while slices remain; `done` after S20 lands).

Active slice: **S6 — #289** (Appearance settings)

Model: sonnet
Status: ready

(`Model:`/`Status:` are read directly by `scripts/run-ui-overhaul.ps1`. Allowed:
haiku | sonnet | opus | fable. `Status: ready` = run the active slice; `blocked` =
needs a human; `done` = stop. Stop gracefully any time with
`scripts/stop-ui-overhaul.ps1`.)

### Slice queue (work top-down; spec = `docs/ui-overhaul-spec.md`; issue body = detail)

Phase 0 — Foundation (land first, in order)
1. [x] S1 — #284 Render-smoke harness + live-verify doc — Model: sonnet
2. [x] S2 — #285 Grouped sidebar nav (CHAT/MIND/TOOLS/SYSTEM) — Model: sonnet
3. [x] S3 — #286 Collapsible section headers (persisted) — Model: sonnet
4. [x] S4 — #287 Federated search shell — Model: opus

Phase 1 — Models & settings
5. [x] S5 — #288 Models tab — Model: sonnet
6. [ ] S6 — #289 Appearance settings (scale/theme/accent) — Model: sonnet
7. [ ] S7 — #290 Mic-mode control (Passive/PTT/Disabled) — Model: sonnet
8. [ ] S8 — #291 TTS controls (mute/volume + voice picker) — Model: sonnet

Phase 2 — Conversations
9.  [ ] S9 — #292 Conversation threads (thread_id + auto-title) — Model: opus
10. [ ] S10 — #293 Save/delete/search conversations — Model: sonnet
11. [ ] S11 — #294 Projects (folders) — Model: opus
12. [ ] S12 — #295 Quick Ask (ephemeral web-first chat) — Model: sonnet
13. [ ] S13 — #296 Per-conversation model override — Model: sonnet

Phase 3 — Files
14. [ ] S14 — #297 File upload (attach + extract + local store) — Model: opus

Phase 4 — Integrations / harness
15. [ ] S15 — #298 Integrations tab: harness status — Model: sonnet
16. [ ] S16 — #299 Integrations: in-UI channel config + control — Model: opus
17. [ ] S17 — #300 Integrations: service directory — Model: sonnet
18. [ ] S18 — #301 Unified inbox for incoming channel messages — Model: opus

Phase 5 — Control surfaces
19. [ ] S19 — #302 Recipes pane (list/run/delete) — Model: sonnet
20. [ ] S20 — #303 Stop / interrupt in-flight turn + TTS — Model: sonnet

### Landed PRs (append as slices merge)

- S1 — #284 → PR #304 (render-smoke harness + live-verify doc)
- S2 — #285 → PR #305 (grouped sidebar nav CHAT/MIND/TOOLS/SYSTEM)
- S3 — #286 → PR #306 (collapsible section headers with persisted state)
- S4 — #287 → PR #307 (federated search shell)
- S5 — #288 → PR #308 (Models tab: model controls moved out of Settings)
