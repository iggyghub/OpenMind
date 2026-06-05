# ADR-0007: Main window UI architecture

**Date:** 2026-06-01
**Status:** Accepted

## Context

OpenMind's user interface today is a fragmented set of nine tray-launched Electron `BrowserWindow`s (`tray/windows/queue.html`, `insights.html`, `memory.html`, `permissions.html`, `credentials.html`, `consent.html`, `irreversible-modal.html`, `visualiser.html`, `profile-setup.html`) wired into a 14-item tray menu. Each window is its own renderer, its own IPC handshake, its own state cache on the `tray/main.js` side (six separate cached snapshots — `permissionsState`, `credentialsState`, `toolsList`, `pluginsList`, `insightsList`, `memoryList`). The pattern shipped incrementally as new surfaces were added (Permissions in #53, Credentials in #114, irreversible modals in #49, etc.) and is internally consistent, but several v1-blocking gaps surfaced:

- **No conversation surface.** Felix is voice-only on input and TTS-only on output; nothing chronicles what Felix heard, said, or called. Reading Felix's answer at reading speed (rather than listening at TTS speed) is impossible.
- **No text-command lane.** A user cannot type to Felix — meeting situations, late-night silent use, URL-pasting, and dictation-friction cases have no path.
- **No first-class plugin surface.** 164 tools register; their liveness is invisible. The Permissions panel's Tools tab lists names but no health status, and per-plugin settings (notably the Discord allowlist for the ToS-risky `discord_user.py`) have no home — CLI scripts (`scripts/discord_user_allowlist.py`) are the only path.
- **No persistent transcript.** Every restart erases the conversation; ChromaDB stores extracted facts (Long-term memory tier) not raw turns.
- **Settings are scattered across a tray submenu.** Models, notifications, camera, visualiser toggle, reminder interval all live in different submenu branches with no canonical "Settings" home.

The pre-existing windows themselves are not the gap — the surfaces they cover are largely right (ADR-0005's Permissions UI design holds; the Credentials window's two-section OAuth+API-keys shape from amendments #114/#148 holds). The gap is the **shell**: there is no single canonical Felix UI; there is a tray menu that *launches* surfaces, and that pattern doesn't scale to "I want to touch everything Felix can do."

A separate constraint anchors the rest of the decision: the **Visualiser** (today an orb, future themes per CONTEXT.md including a fully embodied "body" that moves around the screen) cannot be embedded inside a normal window — it has to be a floating overlay by definition. So the visualiser is a process-level concept, not a Main-window concept, regardless of which Main-window shape we pick.

## Decision

Add a **Main window** to Felix. Chat-primary, persistent left sidebar, Electron-only for v1, shipped as a tracer-bullet first slice that proves the novel pieces end-to-end before the migration work follows.

### Surface architecture

- **One Main window** (Electron `BrowserWindow`) replaces the fragmented tray-launched-window pattern as the canonical Felix UI. Layout: persistent left sidebar nav + main content pane on the right. The content pane defaults to the **Conversation**.
- **Nine sidebar items** in v1 (eventually):
  1. **Conversation** (default landing view) — chat-primary canvas, text input + transcript + state pill
  2. **Queue** — pending 5W1H candidates and Discord drafts; count badge in chat header
  3. **Insights** — per-profile learned model (existing)
  4. **Memory** — per-profile ChromaDB browse/edit (existing)
  5. **Permissions** — 16-class ACL + per-tool overrides (existing two-tab ADR-0005 shape, unchanged)
  6. **Credentials** — OAuth + API keys (existing two-section shape from #114/#148, unchanged)
  7. **Plugins** — first-class status + per-plugin settings (new; hosts the Discord allowlist editor as plugin-specific config)
  8. **Profiles** — list, switch, create, edit per-profile state
  9. **Settings** — system-wide preferences: notifications, reminder interval, camera, visualiser toggle, active model + per-task model assignments
- **Conversation as canvas.** The Main window is *chat-primary* — typing and voice both feed the orchestrator's existing active-mode pipeline; the transcript renders both lanes interleaved. Voice + wake remains the fast lane (CONTEXT.md principle 5 "Passive by default, active on wake" unchanged); typing is the slow-but-silent lane for meetings, late nights, URL pasting, dictation-friction cases. The text-command IPC envelope reuses Cerebral's existing orchestrator entrypoint (`call_tool` with capability) — backend changes are minimal.
- **Visualiser stays a separate floating window**, exactly as today (200×200 transparent click-through always-on-top). The Main window adds its own in-header **State pill** to cover state-signalling inside the chat without forcing the user to glance at the floating orb. Different surfaces, different attention contexts, same state machine.
- **Tray collapses to four jobs** post-Main-window: status line, `Open Felix`, `Switch profile` submenu, `Quit`. All other current tray controls move into the Main window's sidebar. Single source of truth per setting, no tray⇄Main sync.

### Consent surface routing (ADR-0005 amendment, captured separately)

Split by class, not by context:

- **Ask-class gates** render as **inline cards** in the Conversation pane (Once / Session / Persistent / Deny + Why? expander). OS notification fires when the Main window is not focused, reusing the existing `NotificationManager`. The standalone `tray/windows/consent.html` window is retired.
- **Irreversible-flagged gates** continue as a **separate alwaysOnTop modal** (`tray/windows/irreversible-modal.html`), regardless of where the user is. The deliberate friction is the feature — the 2026-05-20 amendment leans on it.
- Voice consent path and fail-closed semantics are unchanged.

See the 2026-06-01 amendment in ADR-0005 for the full rationale.

### Conversation store

A new structured-memory tier — fifth alongside Short-term (RAM rolling buffer) / Environmental (RAM per-session) / Long-term (ChromaDB extracted facts) / Structured (SQLite profiles/queue/ACL/credentials).

- **Schema:** `conversation_turns(id, profile_id, ts, kind, content_json)` where `kind ∈ {user_voice, user_text, felix_speech, tool_call, tool_result, system_event}`.
- **Per-profile**, never global (consent belongs with identity; same rule as Memory and ACL).
- **Storage location:** same SQLite database as profiles + queue + ACL, in `cerebral/data/`.
- **Retention:** infinite in v1 — manual purge UX is a v2 deepening. Matches the existing memory-tier posture (ChromaDB memories are also infinite-retention today). Privacy debt is acknowledged and paid down via the future purge surface.
- **Stored:** post-Whisper text of voice turns, typed input, Felix's spoken text, tool calls (name + args + capability), tool results (status + summary), system events (model switch, profile switch, consent grant/deny, irreversible accept/cancel).
- **Not stored:** raw audio (the RAM rolling buffer stays unwritten per CONTEXT.md), dropped 5W1H candidates (those stay in the queue table).
- **Initial Main-window load:** last 50 turns of the active profile, scrolled to bottom, with a "load older" affordance at the top.
- **Encryption at rest:** unencrypted on disk — same posture as profiles + queue + memories today. Disk encryption is the user's OS responsibility.

### Window lifecycle

- Main window does **not** autostart with Cerebral. Cerebral + tray + visualiser autostart (when the OS-service mode lands); the Main window opens on demand from the tray.
- Closing the Main window **hides** it; Felix stays alive. Quit is reachable only from the tray.
- Tray click → opens or focuses the Main window (today: tray click opens the Queue window; that changes).

### Distribution and bundling

- **Electron-only for v1.** Main window is a new `BrowserWindow` next to the existing tray windows. PWA serving from a Cerebral HTTP server is **out of v1 scope**.
- **Renderer code stays stack-agnostic.** No `require('electron')` from renderers. All backend calls go through the existing WebSocket IPC on `ws://localhost:7766`. A future PWA mirror is a v2 deepening — same renderer, different shell.
- **v1 floor: user starts both processes manually.** `python -m cerebral.main` + the Electron app shortcut. Today's pattern.
- **Post-v1 target:** Cerebral runs as an OS service (Windows service / macOS launchd / Linux systemd), autostarts at boot. The Electron app becomes a normal user app that connects to the running service. The v1 architecture must not preclude this — chiefly, Cerebral must not assume the Electron process is its parent (today it doesn't; this is just guarding against future tight coupling).

### Slicing

Tracer-bullet, three slices. **The MVP is Slice 1.** Slices 2 and 3 are follow-on issues in the same epic, not v1-blocking.

**Slice 1 — Window + Chat (the MVP).** Validates the novel pieces end-to-end. Ships:
- Main window (Electron `BrowserWindow`, frame + menu, resizable, default 1200×800).
- Conversation pane: text input box + transcript + state pill in header.
- Conversation store: new SQLite table, structured kinds, per-profile, load-last-50.
- Text-input wiring: new WebSocket event `user_text_command` → orchestrator's existing `call_tool` path.
- Tray gains an `Open Felix` menu item (no collapse yet — runs alongside the existing 14-item tray menu).
- Visualiser unchanged.

**Slice 2 — Sidebar + migration.** Mostly moves, not new design. Ships:
- Sidebar nav with the eight non-Conversation items.
- Each existing tray window's HTML lifted into a sidebar tab (Queue, Insights, Memory, Permissions, Credentials migrate as-is; Profiles consolidates the existing submenu + `profile-setup.html`; Settings consolidates scattered tray submenu items incl. Models picker + Visualiser toggle).
- Plugins panel promoted to first-class: per-plugin status (loaded / error / disabled), capabilities, tool count, click-through to per-plugin settings including the **Discord allowlist editor** for `discord_user.py`.
- Tray collapses to status + Open Felix + Switch profile + Quit.
- Window lifecycle: close=hide, quit=tray-only.

**Slice 3 — Consent surface re-route.** ADR-0005 amendment in code. Ships:
- Inline consent card component in Conversation pane.
- `consent_request` events route to the card surface; `NotificationManager` fires when Main window unfocused.
- `tray/windows/consent.html` removed.
- Irreversible modal unchanged.

## Considered and rejected

**Pure consolidation, no new surfaces (Q1 option A).** Collapsing the existing nine windows into one shell without adding the Conversation pane / text input / plugin liveness / Discord allowlist editor. Rejected — pure refactor with no new user-visible value can't be justified as a slice on its own. The Conversation is the load-bearing new surface.

**Net-new surfaces without consolidating (Q1 option B).** Adding standalone windows for the missing surfaces (conversation history, plugin status, etc.) while keeping the existing tray-launched pattern for the old ones. Rejected — compounds the fragmentation that's already costly (six cached snapshots in `main.js`, per-window IPC handshakes). The two changes naturally bundle.

**Inspection + settings only, no text-command input (Q2 option A).** A "console for a voice assistant" rather than an interaction surface. Rejected — fails the user's stated goal ("touch everything the AI can do"), leaves Felix unusable in meetings/late-night/silent contexts.

**Full chat-window primacy with voice demoted (Q2 option C).** Felix's main UI *is* a chat; voice becomes one of two equal lanes. Rejected — demotes the wake+voice interaction model that is the product's identity (CONTEXT.md principle 5). Chat-window AIs are commodity; voice + wake is the moat. Chat-primary with voice preserved as fast lane is the right balance.

**Chat canvas + persistent right inspector (Q3 option C).** Always-visible inspector pane on the right (~30–40% of width). Rejected — pays a constant screen tax for surfaces the user isn't actively watching most of the time. Only the Queue earns persistent visibility; the header badge covers that without an inspector pane.

**Chat canvas + bottom drawer (Q3 option D).** Rejected — good for "chat-heavy, panels are afterthought" products, not when panels are first-class control surfaces (which they are here).

**Pure chat canvas + hamburger menu (Q3 option A).** Cheapest to build, weakest discoverability. Rejected — principle 6 "transparent intelligence" wants surfaces visible, not buried.

**Visualiser embedded in Main window header (Q4 option B).** Cleaner mental model but kills voice-state signalling when Main window is closed/minimized — breaks the ambient model that principle 5 depends on. Also incompatible with the post-v1 "body" theme that has to move around the screen by definition.

**Both consent classes inline in Conversation (Q5 option B).** Rejected — weakens the irreversible-gate's deliberate friction, which is a security regression. ADR-0005's irreversible flag exists precisely so an undo-impossible call cannot be ratified by a scroll-then-click.

**Context-routed consent for both classes (Q5 option D).** Modal when Main window unfocused, inline when focused. Rejected — "will this prompt as modal or card?" becomes unpredictable for the user. Stable mental model beats clever routing.

**Discord allowlist as top-level sidebar item (Q7 option A).** First-class billing for one plugin; sets a precedent that doesn't scale to other future high-risk user-account plugins (Twitter/X, Reddit). Rejected.

**Discord allowlist stays CLI-only (Q7 option D).** Too conservative — the friction-as-safety memory applies to *token setting* (where mistakes leak credentials), not *allowlist editing* (where mistakes change who Felix replies to — bad but recoverable). Different blast radius warrants different friction policy.

**Discord allowlist inside Permissions panel (Q7 option C).** Semantically defensible (it's an authorization decision) but conflated with class-level ACL. Plugins panel won as the per-plugin settings home, which keeps Permissions focused on ADR-0005's two-tab shape.

**Tray keeps full menu alongside Main window (Q8 option B).** Permanent two-surface sync tax — every Settings change has two render paths that must stay aligned. Rejected.

**Tray goes away entirely (Q8 option C).** Drops the ambient "Felix is up" signal outside the orb (which signals voice state, not backend health — different signal). Quit-from-window-close becomes the default, which is a worse fail-mode. Rejected.

**Electron spawns Cerebral or vice versa (Q10 bundling ii/iii).** Couples the two processes' lifecycles wrongly — UI crash → backend dies, or vice versa. (iv) OS-service treats the brain as a service and the UI as an app that connects to it, which is how Cortana / Siri / Alexa work.

**Conversation in ChromaDB (Q9 option C).** Pollutes the long-term semantic store with verbatim noise that's already fact-extracted into proper memories. Conflates two memory tiers that CONTEXT.md keeps distinct.

**Conversation in-memory only (Q9 option A).** Blank chat on every restart breaks the "Felix is continuous" mental model the chat pane is supposed to embody. Defeats the chat-primary product purpose.

**Rolling retention window in v1 (Q9 follow-up B/C/D).** Picks a default cutoff before anyone has lived with the feature. Infinite retention with manual-purge-as-v2-deepening preserves optionality and matches the existing memory-tier posture.

**Big-bang single-PR ship (Q11 option A).** Long unmerged branch risks v1 ship-criterion stability. Rejected in favour of three slices.

**Six-plus tiny slices (Q11 option C).** Months of half-migrated UI; surfaces split between Main window and tray popups. Rejected — each interim state must be coherent.

## Consequences

- **Renderer-code portability becomes a tested invariant**, not aspirational. No `require('electron')` from renderers, all IPC via WebSocket. A v2 PWA shell can swap in without renderer changes.
- **ADR-0005 gains a 2026-06-01 amendment** for consent surface routing (split by class). The 16-class vocabulary, the two cross-cutting flags, the IPC envelope, and the fail-closed posture are all unchanged — only the renderer changes.
- **The fragmented tray-window pattern is deprecated.** Slice 2 retires `queue.html`, `insights.html`, `memory.html`, `permissions.html`, `credentials.html`, `profile-setup.html` as standalone windows (their renderer content lifts into sidebar tabs). `irreversible-modal.html` and `visualiser.html` survive as the two intentional carve-outs. `consent.html` is retired by Slice 3.
- **Cerebral gains a new structured-memory tier** (`Conversation store`). New `ConversationStore` Python class mirroring the existing `MemoryStore` / `CredentialStore` patterns. New IPC events: `conversation_turn_emitted` (push from Cerebral), `list_conversation_turns` / `purge_conversation` (request from UI). Schema migration ships in Slice 1.
- **Text-command input becomes a first-class entrypoint** to the orchestrator alongside the voice pipeline. New WebSocket event `user_text_command`; the orchestrator's `call_tool` path is unchanged — the text just feeds it the same way faster-whisper output does. `passive` flag is False for text commands (typed input is a deliberate active-mode wake equivalent). Tool execution semantics (capability gates, ACL, modal routing for irreversibles) are unchanged.
- **The Plugins surface becomes first-class.** Per-plugin status (loaded / error / disabled), declared capabilities, tool count, and per-plugin settings (notably the Discord allowlist editor for `discord_user.py`). The existing Permissions panel's Tools tab keeps its class+tool ACL focus; per-plugin settings move out of Permissions and into Plugins.
- **Privacy debt on conversation retention is acknowledged and deferred** to the v2 purge surface. Users can always manually delete `conversation_turns` rows via SQL; the UI affordance is the v2 deepening.
- **Tray-window-state caches in `tray/main.js` collapse to fewer surfaces** as Slice 2 lands. The six cached snapshots (`permissionsState`, `credentialsState`, `toolsList`, `pluginsList`, `insightsList`, `memoryList`) consolidate into the Main window's renderer state.
- **The post-v1 "body" visualiser theme** has a clean migration path — the visualiser is a process-level floating window, independent of the Main window. Replacing the orb renderer with a body renderer is a v2 issue with no Main-window architectural blockers.
- **Daily-driver stability becomes the slice gate.** Each of the three slices must hit the CONTEXT.md v1 ship criterion ("the core wake → queue → approve loop does not break for daily-use stretches") before the next slice merges. Slice 1 alone proves "Felix has a window" — the MVP.
