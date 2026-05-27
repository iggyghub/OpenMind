# OpenMind v1 Roadmap

**Status:** ✅ decisions locked. Ready for a `/to-issues` pass to file the slices. Read alongside [`../CONTEXT.md` § v1 ship criteria](../CONTEXT.md).

**v1 floor:** A (daily-driver for the author) + D (feature complete against PRD #1's 45 stories). B (installable for a friend) and C (public release) are post-v1.

**At a glance.** 35/45 PRD stories ✅, 9 🟡, 1 ❌-deferred. Remaining v1 work fits in **~18 slices** across 4 buckets:

| Order | Bucket | Slices | Unblocks |
|:-:|---|:-:|---|
| **▶ 1st** | **A — OpenClaw harness running end-to-end** | **2** | **stories 40, 41, 42** |
| 2nd | B — Real Google plugins + PRD amendment + cleanup | 8 | stories 30, 31 |
| 3rd | B-fallback — OSS fallback parity (direct calls) | 5 | story 32 |
| 4th | C — Profile auto-detect | 1 | story 19 |
| running | D — Daily-driver stability + tray-IPC gate fix | 2 | DoD criterion 2 |

**Current phase: Bucket A.** Once A.1 (install + auto-start) and A.2 (live-verify Discord + WhatsApp/Telegram + phone) land, stories 40/41/42 tick ✅ and the channel bridge becomes a real surface that Bucket D's stability run can exercise.

Story 45 (native mobile/smartglasses) is **not** in v1 — see § 4 Post-v1.

---

## 1. PRD #1 story triage

Legend: ✅ delivered · 🟡 partial · ❌ not started

| # | Story (one-line) | State | Evidence |
|---|---|---|---|
| 1 | Passive listening | ✅ | #3 audio_pipeline |
| 2 | "Felix" wakes assistant | ✅ | #3 |
| 3 | Wake name configurable per profile | ✅ | #4 (wake_name override) |
| 4 | Wake awaits command, no queue read | ✅ | #3 + #11 |
| 5 | Candidate actions in tray pulldown | ✅ | #8 tray + #11 5W1H |
| 6 | Approve/dismiss from tray | ✅ | #8 |
| 7 | Configurable periodic reminders | ✅ | #10 |
| 8 | Opt-in notifications | ✅ | #10 |
| 9 | Decompose command into tasks | ✅ | #6 + #7 (intent flow inside model_router / orchestrator) |
| 10 | Auto tool selection | ✅ | #7 |
| 11 | Local model default (Ollama/Gemma 4) | ✅ | #6 |
| 12 | Switch local/cloud per task | ✅ | #29 |
| 13 | Browse/switch models from UI | ✅ | #29 + #37 |
| 14 | Kokoro TTS with voices | ✅ | #5 |
| 15 | Per-profile voice | ✅ | #5 + #4 |
| 16 | Tray as primary presence | ✅ | #8 |
| 17 | Dark animated visualiser | ✅ | #9 |
| 18 | Visualiser state-reactive | ✅ | #9 |
| 19 | Profile at launch / auto-detect | 🟡 | #4 covers launch select; auto-detect after first use — **needs verification** |
| 20 | Profile fields incl. connected accounts | ✅ | #4 + #112 |
| 21 | Per-profile long-term memory | ✅ | #12 + #79 + #85 |
| 22 | Camera + GPS/IP ambient context | ✅ | #14 env_context |
| 23 | Rolling buffer RAM-only | ✅ | #3 |
| 24 | Passive learning from approve/dismiss | ✅ | #13 |
| 25 | Insights view | ✅ | #13 + #88 |
| 26 | Edit/delete/pin Insights entries | ✅ | #13 + #88 |
| 27 | NL → MCP server plugin | ✅ | #30 + #51 |
| 28 | Generated code in readable /plugins | ✅ | #30 |
| 29 | Full computer access | ✅ | #15 + #24 |
| 30 | Fully offline operable | 🟡 | #21 fallback exists for Gmail/Sheets/Drive only; Calendar/Docs/Slides/Contacts/Maps/Tasks have **no fallback** |
| 31 | Google Workspace (9 services) | 🟡 | Gmail (#115/116) + Calendar (#117) on real OAuth. Drive/Sheets on n8n bridge (#20). **Docs/Slides/Contacts/Maps/Tasks have no implementation — neither real nor n8n.** |
| 32 | Local OSS fallbacks for every Google service | 🟡 | Same as #30 — fallback covers 3 of 9 services |
| 33 | Dev tools (Git/GH/Docker/SSH/pkg/HTTP) | ✅ | #24 |
| 34 | Info (Wikipedia/Weather/News/Markets) | ✅ | #25 |
| 35 | Security (Bitwarden/VPN/Net scan) | ✅ | #26 |
| 36 | Printer/Scanner | ✅ | #27 |
| 37 | Steam | ✅ | #27 |
| 38 | OCR invoice/receipt → Sheets/Grist | ✅ | #28 |
| 39 | Zoom + Meet | ✅ | #23 |
| 40 | Phone calls via OpenClaw | 🟡 | plugins/phone.py exists; OpenClaw daemon not running — bridge unreachable |
| 41 | WhatsApp/Telegram via OpenClaw | 🟡 | MCP-client plugin wired (#168, plugins/openclaw_channels.py); live-verify pending (#163) |
| 42 | OpenClaw handles all channels | 🟡 | Same as #41 — code path live, daemon not connected |
| 43 | Fully open source | ✅ | All in this repo |
| 44 | MCP service-agnostic | ✅ | ADR-0001 + #7 |
| 45 | Felix on phone/smartglasses, Cerebral as brain | ❌ | Explicitly deferred — see § 4 Post-v1. OpenClaw bridge (stories 40–42) covers remote access via messaging in the meantime; native mobile/smartglasses is its own phase, user-triggered after v1 ships. |

**Tally:** 35 ✅ · 9 🟡 · 1 ❌

---

## 2. Incomplete buckets → roadmap slices

The 10 🟡 stories collapse into a small number of work buckets. Each bucket below is sized to be one issue unless noted.

### Bucket A — OpenClaw harness running end-to-end *(unblocks stories 40, 41, 42, 45)*

The single largest gap. Code is wired; daemon isn't running. Without this, four PRD stories cannot tick to ✅.

- **A.1** Install OpenClaw and wire it to auto-start alongside Cerebral on the dev box. Document the install in `SETUP.md` so the daily-driver path is reproducible.
- **A.2** Live-verify the channel bridge end-to-end: one inbound message via Discord → Cerebral → response back through Discord. (Discord called out as the test vehicle because the user named it.)
- **A.3** Live-verify a second messaging channel (WhatsApp **or** Telegram — pick one) so the harness isn't single-channel-tested.
- **A.4** Live-verify `phone.py` placing one outbound call via OpenClaw.

*Notes.* A.2–A.4 could collapse into one slice if all three channels share the same OpenClaw config step. Likely 2 slices total: install (A.1) + live verify (A.2 + A.3 + A.4 together).

### Bucket B — Real Google Workspace plugins *(unblocks stories 30, 31)*

**Architectural note.** Real plugins talk directly to Google APIs (per-profile OAuth from the keyring, #112). The n8n bridge is reserved for OSS fallback orchestration, not for Google. The current `plugins/google_workspace.py` (n8n→Google) is legacy stand-in code superseded by the real plugins as they land — delete its tools as each real plugin replaces them.

Six real-plugin slices, ranked by daily-use priority:

- B.1 Docs *(daily, nothing exists)*
- B.2 Sheets *(daily, currently n8n→Google legacy)*
- B.3 Maps *(daily, nothing exists)*
- B.4 Tasks *(daily, nothing exists)*
- B.5 Drive *(occasional, currently n8n→Google legacy)*
- B.6 Contacts *(occasional, nothing exists)*

Plus:

- B.7 PRD amendment — drop Slides from story 31 (user does not use)
- B.8 Cleanup — once B.1–B.6 land, retire the corresponding tools from `plugins/google_workspace.py`

Each B.1–B.6 is a #115/#117-sized slice: OAuth scope addition → real plugin file → AST capability declaration → live-verify against the user's account.

### Bucket B-fallback — OSS fallback parity *(unblocks stories 30, 32)*

Story 32 says every Google service has a local OSS fallback. Currently `google_workspace_fallback.py` covers Gmail (IMAP/SMTP), Sheets (Grist), Drive (Nextcloud) — direct calls, not through n8n. The remaining 5 (Calendar, Docs, Maps, Tasks, Contacts) have no fallback. Slides drops via B.7.

**Decision (Q3 resolved):** fallback plugins call OSS tools **directly**, not through n8n. Rationale: offline mode must have the fewest moving parts. Making n8n a required dependency for offline contradicts "local-first, cloud fallback." n8n earns its keep when Cerebral has no other client, not as an extra hop on the most-critical path.

Five fallback slices, all direct-call shape (matching the existing Gmail/Sheets/Drive fallbacks):
- Calendar offline (local SQLite scheduler — already named in CONTEXT.md)
- Docs offline (LibreOffice Writer)
- Maps offline (OpenStreetMap / Nominatim)
- Tasks offline (local scheduler)
- Contacts offline (local SQLite)

### Bucket C — Profile auto-detect *(unblocks story 19)*

Story 19: "selected at launch **or auto-detected after first use**". Launch-time selection ships (#4). Auto-detect-after-first-use is not in the closed-issue history. One slice, small.

### Bucket D — Daily-driver stability gate *(unblocks DoD criterion 2)*

Not an issue per se — a run-and-fix campaign. Until the daily-driver bar holds, v1 doesn't ship even if every story ticks to ✅.

- D.1 8-hour continuous passive-mode run, no crash, no memory growth.
- D.2 Daily wake → queue → approve cycle exercised, all breakages filed as bugs and fixed.
- D.3 Close the tray-IPC capability-gate skip noted in ADR-0005 Amendment 2 (`cerebral/main.py:1331` calls `_orc.call_tool(tool_name, tool_args)` with no capability arg, bypassing the gate ladder). Carries the irreversible-modal gap with it — left explicitly to a separate slice by #139.

---

## 3. Decisions log

All shape decisions are resolved.

- **Q1 — v1 floor.** A (daily-driver for author) + D (feature complete against PRD #1's 45 stories). B (friend-installable) and C (public release) are post-v1. See [CONTEXT.md § v1 ship criteria](../CONTEXT.md).
- **Q2 — out-of-PRD plugins.** Plugins built ahead of plan (Notion, Obsidian, Todoist, Toggl, Clockify, YouTube, Reddit, RSS, Sports, Home Assistant) are **done** by definition — not gated on by v1. Their existence is bonus; their absence wouldn't have blocked v1.
- **Q3 — OSS fallback orchestration.** Direct OSS calls from the fallback plugin, no n8n hop. Offline mode minimises moving parts.
- **Q4 — PRD #1 lifecycle.** Stays open as the v1 tracking issue. Comment links to this file as canonical source of truth. Closes when v1 ships and DoD is met.
- **Q5 — Story 45 (mobile/smartglasses).** ❌-deferred to § 4 Post-v1. Triggered by the user explicitly when v1 is complete; a new session reviews this file as its primary briefing.

## 4. Post-v1 phases (do NOT start until v1 ships)

Captured here so the next session knows where to pick up. **Not part of v1.** Each phase begins only when the user says "let's start the X portion."

- **Phase 2 — Native mobile client (story 45).** Build the phone-side companion that connects to Cerebral as the central brain. OpenClaw bridge stays as the messaging-channel layer; the mobile client is the native voice + UI surface. Will need its own PRD.
- **Phase 3 — Smartglasses client (story 45).** Same shape as Phase 2, glasses form factor. Likely shares the mobile client's protocol.
- **Phase 4 — B (friend-installable) and C (public release).** Installer / packaged artefact, SETUP.md tuned for a stranger, license review, v1.0.0 tag.

When triggered, the new session's first action is to read this file (especially § 1 to confirm v1 actually shipped, and § 4 for which phase is starting).
