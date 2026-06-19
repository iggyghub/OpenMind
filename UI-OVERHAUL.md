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

Active slice: **F2 — #325** (Window-resize layout: content detaches/floats at odd sizes)

Model: opus
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
6. [x] S6 — #289 Appearance settings (scale/theme/accent) — Model: sonnet
7. [x] S7 — #290 Mic-mode control (Passive/PTT/Disabled) — Model: sonnet
8. [x] S8 — #291 TTS controls (mute/volume + voice picker) — Model: sonnet

Phase 2 — Conversations
9.  [x] S9 — #292 Conversation threads (thread_id + auto-title) — Model: opus
10. [x] S10 — #293 Save/delete/search conversations — Model: sonnet
11. [x] S11 — #294 Projects (folders) — Model: opus
12. [x] S12 — #295 Quick Ask (ephemeral web-first chat) — Model: sonnet
13. [x] S13 — #296 Per-conversation model override — Model: sonnet

Phase 3 — Files
14. [x] S14 — #297 File upload (attach + extract + local store) — Model: opus

Phase 4 — Integrations / harness
15. [x] S15 — #298 Integrations tab: harness status — Model: sonnet
16. [x] S16 — #299 Integrations: in-UI channel config + control — Model: opus
17. [x] S17 — #300 Integrations: service directory — Model: sonnet
18. [x] S18 — #301 Unified inbox for incoming channel messages — Model: opus

Phase 5 — Control surfaces
19. [x] S19 — #302 Recipes pane (list/run/delete) — Model: sonnet
20. [x] S20 — #303 Stop / interrupt in-flight turn + TTS — Model: sonnet

Phase 6 — Fixes round 2 (post-test author feedback)
21. [x] F1 — #324 App/taskbar/titlebar icon: orb not Electron atom — Model: sonnet
22. [ ] F2 — #325 Window-resize layout: content detaches/floats at odd sizes — Model: opus
23. [ ] F3 — #326 Microphone input device selection — Model: sonnet
24. [ ] F4 — #327 Voice/typed control of settings, ADR-0005 gated — Model: opus
25. [ ] F5 — #328 In-conversation collapsible chat backlog panel — Model: sonnet

### Landed PRs (append as slices merge)

- S1 — #284 → PR #304 (render-smoke harness + live-verify doc)
- S2 — #285 → PR #305 (grouped sidebar nav CHAT/MIND/TOOLS/SYSTEM)
- S3 — #286 → PR #306 (collapsible section headers with persisted state)
- S4 — #287 → PR #307 (federated search shell)
- S5 — #288 → PR #308 (Models tab: model controls moved out of Settings)
- S6 — #289 → PR #309 (Appearance settings: UI scale + theme presets + accent colour)
- S7 — #290 → PR #310 (Mic-mode control: Passive/PTT/Disabled in header + Settings)
- S8 — #291 → PR #311 (TTS controls: inline mute/volume + voice picker per profile)
- S9 — #292 → PR #312 (Conversation threads: thread_id schema + auto-title + New conversation button)
- S10 — #293 → PR #313 (Save/delete/search conversations: list pane + delete IPC + search provider)
- S11 — #294 → PR #314 (Projects: project folders, group threads, Unfiled default, delete-leaves-unfiled)
- S12 — #295 → PR #315 (Quick Ask: ephemeral web-first scratch chat, not saved to Conversations)
- S13 — #296 → PR #316 (Per-conversation model override: thread-pinned model, strip select + row badge)
- S14 — #297 → PR #317 (File upload: attach + extract + per-profile local store + attachment chips)
- S15 — #298 → PR #318 (Integrations tab: HARNESS section with daemon + per-channel status)
- S16 — #299 → PR #319 (Integrations: in-UI channel config + control — daemon start/stop/restart, enable toggle, write-only secret)
- S17 — #300 → PR #320 (Integrations: service directory — CONTEXT.md registry grouped by category, Connect deep-links to Credentials)
- S18 — #301 → PR #321 (Unified channel inbox in Integrations pane — inbound surface + reply textarea wired to openclaw_messages_send)
- S19 — #302 → PR #322 (Recipes pane — list/run/delete saved tool-chains, run_recipe IPC wired to _replay_recipe gate path)
- S20 — #303 → PR #323 (Stop / interrupt in-flight turn + TTS — stop button in composer, interrupt_turn IPC, CancelledError handling in _process_command)
- F1 — #324 → PR #329 (Orb window icon: multi-res icon.ico generated by create-icon.js, icon: set on all BrowserWindows)
