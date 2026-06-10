# OpenMind — Domain Context

## Glossary

**OpenMind** — the platform. The installable software product as a whole.

**Cerebral** — the local brain. The central Python backend process that runs on the desktop/home server. All devices connect to it. The name is internal/architectural; users don't speak it.

**Felix** — the default wake name. The word a user speaks to activate the assistant. Phonetically distinct, short, reliably detected by Vosk. Customisable per profile. What OpenMind calls itself in conversation.

**Profile** — a user identity container. Stores who someone is (name, voice preference, wake name override, pronunciation guide, **connected accounts**) and their scoped long-term memory. Selected on launch or auto-detected after first use.

**System setting** — a non-identity machine-wide preference (notifications, reminder interval, camera). Global, shared by every profile, not part of a profile. _Avoid_: calling credentials or connected accounts "settings".

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

**Visualiser** — the on-screen character in advanced mode. Currently: a dark, animated abstract form (orb/waveform style). Reacts to voice activity and system state. Future: configurable — 2D avatar, 3D model, or abstract theme packs.

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
