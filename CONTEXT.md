# OpenMind — Domain Context

## Glossary

**OpenMind** — the platform. The installable software product as a whole.

**Cerebral** — the local brain. The central Python backend process that runs on the desktop/home server. All devices connect to it. The name is internal/architectural; users don't speak it.

**Felix** — the default wake name. The word a user speaks to activate the assistant. Phonetically distinct, short, reliably detected by Vosk. Customisable per profile. What OpenMind calls itself in conversation.

**Profile** — a user identity container. Stores who someone is (name, voice preference, wake name override, pronunciation guide, **connected accounts**) and their scoped long-term memory. Selected on launch or auto-detected after first use.

**System setting** — a non-identity machine-wide preference. Includes: notifications on/off, reminder interval, camera enabled, visualiser visibility, **active model + per-task model assignments**. Global, shared by every profile, not part of a profile. Lives in the Main window's **Settings** sidebar panel (v1) and persists to `cerebral/data/felix-settings.json`. _Avoid_: calling credentials or connected accounts "settings"; avoid putting profile-scoped state (ACL, memory, connected accounts, wake name) here.

**Connected account** — an external account (e.g. Google) a profile has authorized Felix to act as, plus the stored credential that authorizes it. Belongs to one **Profile** (consent belongs with identity, like memory and ACL), never global. _Avoid_: "linked account", "integration login".

**Wake** — the moment a user speaks Felix's name. Triggers: mic opens, system awaits a command. Does not read the queue aloud. Does not interrupt.

**Passive mode** — the default always-on state. Vosk listens continuously for the wake name and actionable signals. No full transcription, no LLM calls, minimal CPU. The system is observing, not acting.

**Active mode** — entered after a wake. faster-whisper transcribes, the LLM processes, tools execute.

**5W1H extraction** — the passive pattern-matching process. When Vosk detects a potentially actionable signal in ambient audio, faster-whisper transcribes the last ~60 seconds and the LLM extracts: Who, What, When, Where, Why, How. Output is a candidate action queued for the user.

**Rolling buffer** — the last ~60 seconds of ambient audio held in RAM. Never written to disk. Discarded continuously. Used only when Vosk triggers a full transcription pass.

**The queue** — the list of candidate actions Felix has identified but not yet executed. Visible in the tray pulldown. Grows silently in passive mode. Acted on only when the user wakes Felix or approves via notification.

**The harness** — OpenClaw. The master command and communication gateway. All external messaging channels (WhatsApp, Telegram, Slack, Discord, Teams, etc.) flow through it. Also serves as the remote access point for Felix before native mobile clients exist. Felix talks to one thing; OpenClaw talks to the services.

**MCP server** — a Model Context Protocol server. The standard unit of capability in Felix. Each tool (Clock, Browser, Files, Shell, etc.) is an MCP server. The LLM calls tools via MCP regardless of what's underneath. Adding a capability = adding an MCP server.

**Plugin** — an MCP server built for Felix. Lives in `/plugins`. The unit of capability — **every** tool Felix uses is a plugin, regardless of what's underneath. A plugin's implementation may call an external API directly (e.g. `gmail.py` → Google), proxy through n8n (e.g. `google_workspace.py` → n8n → Google), call a local OSS tool (e.g. `notes.py` → SQLite), or wrap an OpenClaw integration. Whether the backed service is open-source or proprietary is **orthogonal** to whether something is a plugin. Generated from natural language description by the builder, or hand-authored. Code is always inspectable and editable. _Avoid_: using "plugin" to mean "proprietary integration" — the OSS plugins are still plugins.

**Direct plugin** — a plugin whose implementation calls the target service directly (no n8n hop). Per-profile credentials, one HTTP hop. Preferred for daily-used services.

**n8n-backed plugin** — a plugin whose implementation posts to a local n8n workflow, which then calls the target. Shared credentials across profiles, two hops, plus n8n daemon as a runtime dependency. Acceptable for occasional-use services or where Cerebral has no first-class client for the target.

**The core loop** — the fundamental operation: user speaks → LLM decomposes intent into tasks → selects available tools → executes via MCP. If no tool exists, the growth loop begins.

**The growth loop** — when Felix lacks a tool: identify the gap → run /grill-me to design it → build it as an MCP server → register it → Felix has it permanently.

**Insights view** — the UI panel showing Felix's learned model of a user. Displays detected preferences, patterns, and behavioural adjustments per profile. Every entry is editable, deletable, or pinnable. Full transparency into what Felix has inferred.

**Visualiser** — the floating on-screen representation of Felix. A 200x200 transparent, click-through, always-on-top window that mirrors Felix's voice/system state (idle / listening / thinking / speaking / switching model). Architecturally a separate window from the **Main window** so it survives the Main window being closed and so a future **body** can move around the screen (which a window-embedded visualiser could not). Runs independently of the Main window's own in-header state pill (which signals the same state to a user already inside the chat).

**Visualiser theme** — what the visualiser renders. v1 default: the **orb** (dark, animated abstract form, waveform-style). Future themes: 2D avatars, 3D models, animal characters, abstract theme packs — user-selectable per profile. The v1 visualiser's renderer is theme-pluggable in shape so the orb is one option among many, not a single hard-coded form. Post-v1: a fully embodied **body** that walks around the screen as a theme.

**State pill** — a one-line indicator in the Main window's chat header ("Felix is listening…" / "thinking…" / "speaking…"). Covers state-signalling *inside* the chat so the user doesn't have to glance at the floating Visualiser. Same state machine as the Visualiser, different render surface, different attention context.

**Plugins panel** — the Main window sidebar item that lists every registered plugin with: name / status (loaded / error / disabled) / declared capabilities / tool count. Click a row → plugin-detail view: per-tool list (read-only) and per-plugin settings (where applicable — e.g. the **Discord allowlist editor** for `discord_user.py` lives here). The Plugins panel hosts plugin-specific configuration; the **Permissions panel** keeps its ADR-0005 two-tab shape (Capabilities / Tools) for class+tool ACL only. _Avoid_: putting per-plugin settings inside Permissions, or putting class-level ACL inside Plugins.

**Main window** — the primary Felix UI surface. A chat/interaction canvas where the user converses with Felix by voice (the fast lane — wake + speak) or by typing (the slow-but-silent lane). The transcript renders both lanes interleaved. Layout: a persistent left **sidebar nav** lists the inspection/control surfaces (Queue, Insights, Memory, Permissions, Credentials, Plugins, Settings, Profiles); the right pane defaults to the **Conversation** and swaps to the selected panel when a nav item is clicked. The Queue earns a count badge in the chat header (the only time-sensitive surface) so the user sees pending items without leaving the conversation. Distinct from the **Visualiser** (ambient overlay) and from the **tray** (always-on launcher + quick-actions). Lifecycle: the Main window does **not** autostart with Cerebral — the user opens it on demand from the tray. Closing the window **hides** it; it does not quit Felix. Quit is reachable only from the tray. _Avoid_: calling it "the chat window" — it is the chat *and* the control surface.

**Tray (post-Main-window)** — the always-on launcher and escape hatch. After the Main window ships, the tray menu collapses from its previous fragmented-control role (~14 items) to four jobs: a status line ("Felix — Running" / "ACTIVE — listening"), `Open Felix` (focus or open Main window), `Switch profile` submenu (fast multi-profile action that doesn't justify drilling into Profiles), and `Quit`. All other controls (model picker, notifications, camera, reminder interval, visualiser toggle, Queue/Insights/Memory/Permissions/Credentials/Plugins/Profiles open-window items) move into the Main window's sidebar. Single source of truth per setting; no tray⇄Main sync.

### Deployment topology (post-Main-window)

Two processes, one binary on each side. Both stay local.

- **Cerebral (Python).** AI pipeline, memory, MCP execution, WebSocket IPC server on `ws://localhost:7766`. v1 floor: user starts manually (`python -m cerebral.main`). Post-v1: registered as an OS service (Windows service / macOS launchd / Linux systemd) and autostarts at boot. The v1 architecture must not preclude the service shape.
- **Felix (Electron).** Hosts the tray, the Main window, the Visualiser, and the irreversible-modal popup. Started on demand from a desktop shortcut. Connects to Cerebral over WebSocket. The renderer code (HTML/CSS/JS in `tray/windows/`) is **stack-agnostic** — no `require('electron')` from renderers, all backend calls go through the existing WebSocket IPC — so a future PWA mirror is a v2 deepening, not a v1 design choice.

PWA serving from Cerebral (a local HTTP server + service-worker shell) is **out of v1 scope**. Thin clients (phone, glasses) continue to ride OpenClaw bridges per CONTEXT.md "Deployment topology" until native clients ship.

**Conversation** — a turn-by-turn record of what Felix heard, said, was typed at, and called. Lives in the Main window's chat canvas. Per-profile. The canonical transcript surface — replaces the tray's previous fragmented "what just happened?" surfaces (queue results, model-switch notifications, tool-call logs).

**Conversation store** — the SQLite-backed persistent transcript. A new structured-memory tier alongside profiles / queue / ACL / credentials. Schema: `conversation_turns(id, profile_id, ts, kind, content_json)` where `kind ∈ {user_voice, user_text, felix_speech, tool_call, tool_result, system_event}`. Per-profile (consent belongs with identity). Stored unencrypted in the user's local SQLite (disk encryption is the user's OS responsibility, same posture as profiles + queue + memories). Retention is infinite in v1 — purge UX is a deepening, not a blocker. The rolling RAM buffer's raw audio stays unwritten per the existing memory-model rule; only the post-Whisper text of voice turns is persisted. Dropped 5W1H candidates stay in the queue table; the Conversation store records only acted-upon turns and system events.

**Distinct from Long-term memory.** The Conversation store keeps raw turns. ChromaDB keeps *extracted facts* learned from those turns. Conflating the two pollutes the semantic store with verbatim noise — Felix recalls "you live in Berlin" from the extraction pipeline, not by re-reading last Tuesday's transcript.

**Initial Main-window load.** On open, the Conversation pane shows the most recent ~50 turns of the active profile, scrolled to bottom, with a "load older" affordance at the top.

---

## Architecture

### Stack

| Layer | Technology |
|-------|-----------|
| Backend brain | Python |
| Frontend / tray | Node.js + web (HTML/CSS/JS) |
| Local LLM | Ollama (default model: Gemma 4) |
| Cloud LLM | Claude (Anthropic) |
| Model router | OpenClaw |
| Always-on STT | Vosk |
| Full STT | faster-whisper |
| TTS | Kokoro (local, changeable voices) |
| Short-term memory | RAM (rolling buffer, never persisted) |
| Long-term memory | ChromaDB or Qdrant (local vector DB) |
| Structured memory | SQLite (profiles, preferences, queue) |
| Tool protocol | MCP (Model Context Protocol) |
| Messaging harness | OpenClaw |
| Workflow automation | n8n (self-hosted) |
| Primary integrations | Google (Gmail, Sheets, Drive, Calendar) |
| Offline fallbacks | Grist (sheets), IMAP/SMTP (mail) |

### Deployment topology

```
[Desktop — Cerebral]
  ├── Python backend (AI pipeline, memory, action execution)
  ├── Node.js frontend (system tray, dark UI, visualiser)
  ├── Ollama (local LLM)
  ├── OpenClaw harness (messaging + remote access)
  ├── MCP servers / plugins
  ├── ChromaDB / SQLite (local storage)
  └── n8n (workflow automation)

[Other devices — thin clients]
  └── Connect to Cerebral over local network
      (phone/glasses via OpenClaw until native clients ship)
```

### Audio pipeline

```
Ambient audio
  → Vosk (always-on, lightweight keyword + signal detection)
      → [no signal]: discard, loop
      → [signal detected]: last ~60s from rolling buffer
          → faster-whisper (full transcription)
              → LLM (5W1H extraction → candidate action)
                  → queue
```

### Action execution pipeline

```
User wakes Felix ("Felix, ...")
  → faster-whisper transcribes command
  → LLM decomposes into tasks
  → selects MCP tools
  → executes
  → result spoken via Kokoro + shown in UI
```

---

## Design principles

1. **Open source throughout.** Every component must have an accessible, modifiable codebase. No black boxes.

2. **Integration is the product.** Getting the components talking correctly is the hard work and the value. A feature that doesn't integrate cleanly doesn't ship.

3. **Local first, cloud fallback.** Felix works fully offline. Cloud services (Claude, Google APIs) enhance when available; local alternatives (Ollama, Grist, IMAP) cover when they don't.

4. **The growth loop over the bloat loop.** Felix does not ship every possible feature. It ships the core loop plus the ability to grow. Missing tool → design it → build it → done.

5. **Passive by default, active on wake.** Felix never interrupts. It observes, queues, and waits. The user controls when it speaks.

6. **Transparent intelligence.** Felix shows its work. The Insights view, the queue, the plugin directory — everything Felix knows and does is visible and editable by the user.

7. **Profile = identity, not configuration.** System settings are global. Profiles store who you are, what you remember, and how Felix sounds when talking to you.

---

## Integration registry

### Already covered by OpenClaw (do not duplicate)

| | What |
|--|------|
| ✅ Model providers | Anthropic, Ollama, OpenAI, Google, Groq, Mistral, DeepSeek, LM Studio, HuggingFace, Qwen, and 20+ more |
| ✅ Browser automation | Playwright (bundled) |
| ✅ Web extraction | Mozilla Readability (bundled) |
| ✅ PDF reading | PDF.js (bundled) |
| ✅ Messaging channels | WhatsApp, Telegram, Discord, Slack, Teams, and more |
| ✅ Image generation | ComfyUI (bundled) |
| ✅ Vector SQLite | sqlite-vec (bundled) |

### Starter tools (ships with Felix core)

| MCP Server | Capabilities |
|-----------|-------------|
| Clock | Timers, alarms, reminders, world time |
| Scheduler | Calendar events, recurring tasks |
| Browser | Web search, open URLs, page summarisation |
| Files | Create, open, move, search files and folders |
| Apps | Launch, switch, close applications |
| Clipboard | Read, write, monitor clipboard |
| Notes | Quick capture, searchable local notes |
| System | Volume, brightness, WiFi, screenshots, power |
| Shell | Run terminal commands and scripts |
| OpenClaw | All messaging channels + remote access harness |

### Google Workspace (online, with local OSS fallbacks)

| MCP Server | Fallback | Capabilities |
|-----------|---------|-------------|
| Gmail | IMAP/SMTP | Read, write, send, search, label, thread |
| Google Calendar | Local scheduler | Events, reminders, availability, invites |
| Google Drive | Nextcloud | Upload, download, search, organise |
| Google Docs | LibreOffice Writer | Read, write, create, export |
| Google Sheets | Grist | Read, write, formulas, create |
| Google Slides | LibreOffice Impress | Read, create, export |
| Google Contacts | Local SQLite | Read, search, create |
| Google Maps | OpenStreetMap | Directions, places, travel time |
| Google Tasks | Local scheduler | Create, complete, list |

### Day 1 integrations

| MCP Server | Category | Capabilities |
|-----------|---------|-------------|
| Git | Dev | Status, commit, push, pull, diff, log, branch |
| GitHub / GitLab | Dev | Issues, PRs, repos, notifications |
| Docker | Dev | List, start, stop, build containers |
| Package Managers | Dev | npm, pip, winget — install, update, search |
| SSH | Dev | Remote machines, run remote commands |
| HTTP Client | Dev | API requests, webhooks, test endpoints |
| Wikipedia | Information | Search, lookup, summarise articles |
| Weather | Information | Forecast, alerts, hourly (Open-Meteo OSS) |
| News | Information | Headlines, topic monitoring, sources |
| Stocks / Crypto | Information | Price lookup, watchlist, read-only market data |
| Bitwarden | Security | Read-only local vault access |
| VPN | Security | Connect, disconnect, check status |
| Network Scanner | Security | Devices, ports, ping, diagnostics |
| Printer / Scanner | Hardware | Print jobs, scan to file, check status |
| Game Launcher | Hardware | Steam — launch, library, running status |
| Invoice / Receipt | Finance | OCR extract → Google Sheets / Grist |
| Zoom / Google Meet | Communication | Join, schedule, manage video calls |
| Phone Calls | Communication | Via OpenClaw channels |

### Second wave (growth loop — add when needed)

| MCP Server | Category |
|-----------|---------|
| Notion | Productivity |
| Obsidian | Productivity |
| Todoist / Tasks | Productivity |
| Time Tracker | Productivity |
| YouTube | Social / Content |
| Reddit | Social / Content |
| Twitter / X | Social / Content |
| RSS Monitor | Social / Content |
| Sports Scores | Social / Content |
| GIMP / Darktable | Creative |
| Blender | Creative |
| Figma | Creative |
| FFmpeg | Creative |
| Home Assistant | Smart Home |
| Dropbox / OneDrive | Cloud Storage (low priority) |

### Later

| MCP Server | Category |
|-----------|---------|
| Health (Fitbit / Garmin / Google Fit) | Health |

---

## Memory model

| Tier | Store | Scope | Retention |
|------|-------|-------|-----------|
| Short-term | RAM rolling buffer | System-wide | ~60 seconds, never persisted |
| Environmental | RAM | Per-session | Camera/GPS context (location, travel state, building) |
| Long-term | ChromaDB (vector) | Per-profile | Indefinite, semantically searchable |
| Structured | SQLite | Per-profile | Profiles, queue, preferences, learned patterns |

---

## v1 ship criteria

OpenMind ships v1 when **both** are true:

1. **Feature complete against PRD #1.** Every one of PRD #1's 45 user stories has its full implementation delivered. Stand-in implementations (e.g. n8n-bridge wrappers used as placeholders for first-class OAuth plugins) do not count as delivered unless n8n was the deliberate target architecture for that story.
2. **Daily-driver stable for the author.** The author uses Cerebral every day as their primary assistant. The core wake → queue → approve loop does not break for daily-use stretches. Crashes, regressions, and broken plugins on the daily path are bugs that block v1.

Installable-on-a-friend's-machine, public release, PyPI/installer artefacts, marketing surface, and a v1.0.0 tag are **explicitly post-v1**. They become the v2 ship criteria.

---

## Discord user-account integration

OpenMind has **two** Discord integration paths, intentionally:

1. **Bot-API via OpenClaw** (the harness path, the default). A
   registered Discord bot is the messaging account; messages flow
   through `plugins/openclaw_channels.py` (#168 / PR #171). This is
   the same path Telegram / WhatsApp / Slack take. Currently
   deferred per the user's "add bots at a later date" decision
   ([#164](https://github.com/iggyghub/OpenMind/issues/164)).
2. **User-account direct** (the self-bot path,
   `plugins/discord_user.py` -- Issue
   [#175](https://github.com/iggyghub/OpenMind/issues/175), ADR-0006).
   Felix reads incoming DMs from real humans on the *user's
   personal* Discord account and replies as the user. This path
   bypasses OpenClaw entirely because OpenClaw 2026.4.29's Discord
   channel is bot-API only -- there is no user-account login flow
   to consume.

**Why both can coexist.** They bind two *different* Discord
identities (a bot user vs. the human user), so they don't race on
incoming events.

### ToS risk on the self-bot path

Discord's Developer Terms forbid automating personal user accounts.
Discord actively detects self-bots; detection results in **permanent
ban** of the human account (DMs, friend list, server ownership,
Nitro, purchase history -- all lost, no recovery). The user filing
#175 has explicitly accepted this risk. Slice-2 mitigations (human-
shaped reply delays sampled from a log-normal distribution, typing
indicators, per-sender allowlist, sleep-hours, per-channel rate
limits, per-channel serialisation) reduce detection probability but
do not eliminate it.

**No contributor should run `plugins/discord_user.py` against a
Discord account they are not prepared to lose.** See ADR-0006 for
the full posture; SETUP.md's "Discord (user account) -- experimental,
high risk" subsection covers the setup steps.

### Token storage

The user-account token is stored via the #160 keyring +
`DISCORD_USER_TOKEN` env-var chain, NEVER in a plain-JSON config and
NEVER logged. The provider is *deliberately not* surfaced in the
tray's "API keys" UI (friction-as-safety -- setting it requires the
user to do a slightly more deliberate thing than pasting into a
form).

### Slice sequencing

- **Slice 1** (PR #176, shipped): plugin skeleton + outbound
  `discord_send_message` + draft-only inbound via
  `cerebral/main.py:_surface_discord_draft`. Every inbound DM became
  a queue draft for manual approval; no auto-reply.
- **Slice 2** (Issue #177, this branch): per-sender auto-reply
  allowlist + detection-mitigation gauntlet
  (`cerebral/discord_auto_reply.py`). Empty allowlist preserves
  slice-1 byte-identical behaviour. The
  `scripts/discord_user_allowlist.py` CLI manages senders + settings
  (rate limit, delay distribution, typing indicator, sleep-hours
  window). Live verification against a real recipient is the user's
  acceptance gate.
- **Slice 3** (Issue #178, blocked-on-slice-2): `discord_react` /
  `discord_edit` / `discord_delete` + dynamic presence automation
  (auto-idle / auto-online driven by LLM activity, with slice-2's
  sleep-hours window winning over presence transitions).

---

## Not in scope (yet)

- Security model and per-profile permissions
- Native mobile client (OpenClaw bridges this for now)
- Smartglasses client
- 2D / 3D character themes (visualiser ships first)
- Multi-context / multi-user profiles in a shared household
- Visual plugin builder (natural language builder ships first)
