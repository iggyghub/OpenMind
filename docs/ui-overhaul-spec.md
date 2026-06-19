# UI & Harness Overhaul — Spec

Single source of truth for the 20-issue UI/harness overhaul campaign. Each runner
session reads this file plus the specific issue body it is implementing. Grounded
in `CONTEXT.md` (domain language) and the existing `tray/windows/main.html` Main
window. Driver: `UI-OVERHAUL.md`. Runner: `scripts/run-ui-overhaul.ps1`.

> Decisions below were locked in a grill-me session 2026-06-17. Do not relitigate
> them in a slice — implement them. If a decision is genuinely unworkable, set
> `Status: blocked` in `UI-OVERHAUL.md` with a one-line reason and stop.

---

## Where things live today (baseline)

- **Main window:** `tray/windows/main.html` (~4,600 lines, single file: inline
  `<style>` + markup + inline `<script>`). Renderer runs with
  `nodeIntegration:false` — **no `require('electron')`**; all backend calls go
  through the WebSocket IPC to Cerebral at `ws://localhost:7766`.
- **Sidebar nav** (flat, 9 items): Conversation, Queue, Insights, Memory,
  Permissions, Credentials, Plugins, Profiles, Settings. Routing in
  `tray/lib/sidebar-router.js` (`VALID_ROUTES`, dual-mode export). `recipes` is a
  valid route with no pane yet.
- **Settings pane** holds: Active model / Switch model / Per-task models / Refresh;
  Notifications + reminder interval; Camera toggle; Visualiser toggle.
- **Conversation store:** SQLite, `conversation_turns(id, profile_id, ts, kind,
  content_json)` — a single rolling per-profile stream, **no thread/project
  concept**.
- **Plugins** (~50) in `/plugins`; OpenClaw messaging via
  `plugins/openclaw_channels.py`. No harness/integrations UI surface exists.

## Cross-cutting rules (apply to EVERY slice)

1. **Renderer is stack-agnostic.** No `require('electron')` in renderer code; all
   backend I/O over the existing WebSocket IPC. New shared logic that needs Node
   tests uses the dual-mode `<script src>` + `module.exports` wrapper pattern
   established in `tray/lib/sidebar-router.js` / `permissions-store.js` (see PR
   #203). Wrap module bodies in an IIFE so classic `<script src>` tags don't
   collide on top-level `const`.
2. **Search must never surface credential/secret values.** The federated index
   excludes the Credentials pane's secret values entirely (labels/status only).
3. **Write-only secrets.** Any in-UI secret entry (channel tokens, API keys)
   follows the existing Credentials contract: cleared from the DOM on send, never
   echoed back in a state broadcast.
4. **Voice = profile-scoped; appearance/volume/mic-mode = system settings.** Per
   `CONTEXT.md`: Kokoro voice choice persists per-profile; UI scale, theme, accent,
   TTS volume/mute, mic-mode are system settings in `cerebral/data/felix-settings.json`.
5. **Theme via CSS variables.** All colour/scale work drives the existing `:root`
   CSS variables in `main.html`. No hard-coded hex in new rules where a variable exists.
6. **PowerShell scripts are ASCII-only** in string literals and use the
   pause-on-exit pattern — see `CLAUDE.md`. (Applies if a slice touches `.ps1`.)
7. **One PR per issue, `Closes #N` in the body.** Branch off latest `origin/master`.
8. **Tests + render-smoke gate every PR.** Run the relevant suite
   (`pytest -c cerebral/pytest.ini` and/or root `python -m pytest`, plus any Node
   lib tests) AND the render-smoke harness (slice 1). Proceed only if green.
9. **Per-issue live-verify.** Append the issue's human live-verify steps to
   `docs/ui-overhaul-live-verify.md` (created in slice 1) — the visual checks a
   headless run can't make.

## Render-smoke harness (built in slice 1, reused by all)

A headless Node check (`tray/test/render-smoke.js` or similar) that loads
`main.html` in a headless Electron/JSDOM context, asserts every `VALID_ROUTES`
pane element and nav item exists and the inline script parses without throwing,
and writes a screenshot/serialised-DOM artifact to `.claude/tmp/render-smoke/`.
Each later slice adds an assertion for the element(s) it introduced.

---

## Target information architecture

Grouped sidebar (single column, section headers, collapsible):

- **CHAT** — Conversation, Conversations (threads+projects)
- **MIND** — Insights, Memory, Recipes
- **TOOLS** — Plugins, Integrations, Credentials, Permissions
- **SYSTEM** — Models, Settings, Profiles

Persistent header (every pane): federated **search bar** (scoped-first +
"found elsewhere"), **mic-mode** control (Passive/PTT/Disabled), **TTS** speaker
mute + volume, state pill, queue badge.

---

## Per-slice specs

### Phase 0 — Foundation (land first, in order)

**S1 — Render-smoke harness.** Build the headless render check above + create
`docs/ui-overhaul-live-verify.md` with a header and an empty per-issue checklist.
AC: `npm test` (or a documented command) runs the smoke check; it fails if a known
route's pane is missing; artifact written. No UI change to ship behaviour.

**S2 — Grouped sidebar nav.** Restructure the flat nav into CHAT/MIND/TOOLS/SYSTEM
with section headers. Add `models`, `conversations`, `integrations` to
`VALID_ROUTES` (recipes already valid) and add nav entries + **stub panes**
(placeholder copy + issue ref) for Models, Conversations, Integrations, Recipes.
Existing routing/hash behaviour preserved. AC: all four new nav items route to
their (stub) pane; router unit tests updated; render-smoke asserts the new items.

**S3 — Collapsible section headers.** A generic accordion behaviour: every
`*-section` header across panes becomes click-to-collapse with a chevron; collapse
state persists per pane+section (localStorage or settings). Item-level collapse
(queue/insights) stays. AC: clicking a section header toggles its body; state
survives reload; covered by a small Node test on the persisted-state helper.

**S4 — Federated search shell.** Static header search bar. A search registry where
each pane registers a provider `(query) -> [{label, route, anchor}]`. Typing
filters the CURRENT pane live and renders a "Found elsewhere" list of other panes'
hits as jump links. Credentials provider returns labels/status only — never secret
values (rule 2). AC: provider registry + ranking covered by Node tests; current-tab
filter + cross-tab jump works; Plugins' existing filter is reframed onto the shell.

### Phase 1 — Models & settings

**S5 — Models tab.** Move Active model / Switch model / Per-task models / Refresh
out of Settings into a new `models` pane under SYSTEM. Settings keeps
Notifications/System. AC: model switching + per-task assignment work from the new
pane (same IPC); Settings no longer shows model controls; render-smoke updated.

**S6 — Appearance settings.** In Settings: a UI-scale control (e.g. 90/100/110/125%),
theme presets (Midnight=current default, a Light, a High-contrast), and an accent
colour picker. Drives `:root` CSS variables; persists as system settings; applied
on load. AC: changing scale/theme/accent updates the UI live and survives reload.

**S7 — Mic-mode control.** A 3-state control (Passive / Push-to-talk / Disabled) in
the chat header AND Settings, reflecting live state. PTT opens the mic only while a
hotkey/button is held; Disabled fully closes it; Passive = today's wake-word
default. Wire to Cerebral mic control over IPC. AC: switching modes changes
listening behaviour; header + Settings stay in sync (single source of truth).

**S8 — TTS controls.** Conversation header: speaker mute/unmute + a volume slider.
Settings: master TTS on/off, the same volume, and a Kokoro **voice-model picker**
saved **per-profile**. AC: mute/volume affect spoken output live; voice choice
persists per profile; volume/mute persist as system settings.

### Phase 2 — Conversations

**S9 — Conversation threads.** Add `thread_id` to the conversation schema
(migration; back-fill existing turns into one legacy thread). A "New conversation"
button starts a thread; turns attach to the active thread. Auto-title from the first
exchange (editable); long idle gap may start a new thread. AC: turns carry a
thread_id; new-conversation works; auto-title set; migration is non-destructive.

**S10 — Save / delete / search conversations.** The Conversations pane lists saved
threads (title, model, turn count, timestamp) with delete (removes the thread's
turns). Register a conversations search provider into the S4 shell (titles + turn
text). AC: list/open/delete work; deleting purges turns; search finds threads by
title and content.

**S11 — Projects.** Renameable project folders; assign a thread to at most one
project; "Unfiled" default; `project_id` FK on the thread. Conversations pane groups
threads by project (collapsible per S3). AC: create/rename/delete project; move a
thread; deleting a project leaves its threads Unfiled (not deleted).

**S12 — Quick Ask.** A pinned ephemeral, web-first scratch chat at the top of CHAT,
separate from saved threads. Routes turns to the Browser/web-search plugin by
default; not saved into projects; keeps only the last N (or clears on close). AC:
Quick Ask answers via web search; its turns never appear in the saved
Conversations list or any project.

**S13 — Per-conversation model override.** A thread may pin its own model, overriding
the global/per-task default for that thread's turns. AC: a thread with an override
uses that model; clearing it falls back to the default; persisted on the thread.

### Phase 3 — Files

**S14 — File upload.** Composer paperclip + drag-drop; accept arbitrary file types.
Copy the file into a per-profile local store and attach a reference to the
conversation turn. On send: text/PDF/docs → extract text (PDF.js/Readability
already bundled) and include for the LLM; images → pass to a vision-capable model;
other → store as a file reference plugins (Files etc.) can act on. No cloud upload.
AC: upload + attach renders on the turn; a PDF can be summarised in one turn; an
image is described; an arbitrary binary is stored and path-referenced.

### Phase 4 — Integrations / harness

**S15 — Integrations tab shell.** New `integrations` pane, HARNESS section:
OpenClaw daemon status (running/down) + each messaging channel
(WhatsApp/Telegram/Discord/Slack/Teams) with connection state. Status read over
IPC (or via the openclaw_channels plugin). AC: pane shows live harness + per-channel
status; render-smoke asserts it; Plugins pane unchanged (stays the technical view).

**S16 — Channel config in-UI.** In the HARNESS section: start/stop/restart the
OpenClaw daemon, enable/disable a channel, and configure a channel's credentials via
write-only secret inputs (rule 3). AC: start/stop reflects in status; enabling a
channel persists; secrets are write-only and never echoed back.

**S17 — Service directory.** Second section of the Integrations pane: the
`CONTEXT.md` integration registry grouped by category (Google, Dev, Info, Security,
etc.), each row showing connected/available + a "Connect" action that deep-links to
the Credentials pane. AC: directory renders from a registry source grouped by
category; Connect navigates to the right Credentials entry.

**S18 — Unified channel inbox.** Incoming messages from configured OpenClaw channels
surface in the UI — either a dedicated inbox surface or routed into Conversations as
channel-tagged threads (implementer's call; document which). AC: an inbound channel
message becomes visible in the UI without restarting; replying routes back out
through the channel.

### Phase 5 — Control surfaces

**S19 — Recipes pane.** Build the `recipes` pane (route already valid): list saved
Recipes (named tool-chains, per `CONTEXT.md`), run one (re-fires per-step ADR-0005
gates), and delete one. AC: list/run/delete saved Recipes; running replays the
chain through the normal gate path.

**S20 — Stop / interrupt.** A Stop control in the composer/header that cancels the
in-flight turn (planner/chain) and any active TTS. AC: pressing Stop halts
generation and silences TTS promptly; the thread records the interruption; state
pill returns to passive/active.

---

## Verification per slice

1. Relevant automated tests pass (pytest and/or Node lib tests).
2. Render-smoke (S1) passes, including the assertion the slice added.
3. The slice's human live-verify steps are appended to
   `docs/ui-overhaul-live-verify.md` for the author to run in the real app.

---

# Phase 6 — Fixes round 2 (F1-F5)

Post-testing fixes/features reported by the author after the 20-slice campaign
landed. Same cross-cutting rules and verification gate as above. Issues #324-#328.

**F1 — Orb window icon (#324).** `BrowserWindow`s in `tray/main.js` set no `icon:`,
so Windows shows Electron's default atom logo. Set `icon:` on every window (main,
visualiser, modal) to the orb; generate a multi-res `.ico` in `create-icon.js`.

**F2 — Window-resize layout (#325).** Content detaches/floats at narrow/odd sizes.
Fix the flex column chain (`body` row -> `.content` -> `.pane` -> `.transcript` +
`.composer`) so header/thread-strip/transcript/composer stay anchored down to the
720x480 min; transcript scrolls internally. Add a render-smoke assertion.

**F3 — Microphone device picker (#326).** Settings dropdown of input devices
(`enumerateDevices`), persisted as a system setting; Cerebral captures from it (or
document the wiring gap honestly if backend plumbing is out of this slice).

**F4 — Voice/typed settings control, gated (#327).** A settings-control MCP tool
exposing changeable **system settings** only; planner selects it (ADR-0008) and it
dispatches through the ADR-0005 ask-class gate (consent card) with `passive=False` —
applies only on approval, reflected live. No profile-scoped state via this tool.

**F5 — In-conversation backlog panel (#328).** A collapsible thread-history panel
docked in the Conversation pane, grouped by project (each group collapsible, reusing
the S3 collapse helper + S10/S11 store), active thread highlighted, panel itself
collapsible. The Conversations tab stays the full manager; no store duplication.
