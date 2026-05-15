# OpenMind — Handoff

Continuing implementation of the OpenMind project. Read CONTEXT.md and CLAUDE.md first, then this file.

---

## What has been built

### Issue #2 — Project scaffold ✅
Two-process architecture running end-to-end:
- `cerebral/main.py` — Python asyncio WebSocket server on `ws://localhost:7766`
- `tray/main.js` — Electron system tray app (purple orb icon, context menu)
- Both start cleanly, exchange events, shut down together via "Quit" in tray

### Issue #3 — Audio pipeline ✅
- `cerebral/audio/rolling_buffer.py` — 60s RAM-only circular buffer (16kHz int16, never touches disk)
- `cerebral/audio/pipeline.py` — Vosk passive listener (constrained grammar: `["felix","[unk]"]`) + faster-whisper transcription on wake
- Model download script: `python cerebral/scripts/download_models.py` (Vosk small EN ~40 MB)
- faster-whisper auto-downloads `tiny.en` on first wake
- Wake event → `{"type":"wake","data":{"transcript":"..."}}` over IPC
- Cerebral starts without audio if model is missing (warns, continues)

### Issue #4 — Profile manager ✅
- `cerebral/db/profiles.py` — SQLite CRUD (ProfileManager + Profile dataclass)
- DB at `cerebral/data/openmind.db` (gitignored)
- Schema: `id, name, wake_name, pronunciation_guide, voice_id, connected_accounts (JSON), voice_sample (base64 WebM), wake_sample (base64 WebM), created_at, last_used_at`
- First run → Cerebral emits `{"type":"first_run"}` → tray opens BrowserWindow setup form
- Subsequent launches → auto-load last-used profile → emit `{"type":"profile_loaded","data":{...}}`

### Profile setup UI (redesigned per user request)
`tray/windows/profile-setup.html` — dark conversational form:
- Pulsing purple orb + "Hello, I'm Felix. Let's set up your profile."
- "What should I call you?" — name input + 🎙 record button (3s, live waveform, playback, re-record)
- "What would you like to call me?" — wake word input + 🎙 record button (same UX)
- Recordings stored as base64 WebM in `voice_sample` / `wake_sample` profile fields

### Issue #5 — TTS engine ✅
- `cerebral/tts/engine.py` — `TTSEngine` wrapping Kokoro `KPipeline`
  - `speak(text, voice_id)` — async; runs synthesis + playback in a thread, never blocks the event loop
  - `list_voices()` — returns 28-voice static catalogue (American + British, male + female)
  - `ready` property — graceful degradation if `kokoro` is not installed
- Active profile `voice_id` used automatically; switching voice takes effect on next `speak()` call
- Kokoro model warm-up added to `python cerebral/scripts/download_models.py` (~330 MB one-time)
- End-to-end: say "Felix" → Cerebral emits `wake` event → speaks "I'm listening" in profile voice
- `ProfileManager.update_voice(id, voice_id)` added for targeted voice updates

---

## File structure (what exists now)

```
C:\OpenMind\
├── CLAUDE.md
├── CONTEXT.md
├── SETUP.md               ← updated with full dev start sequence
├── HANDOFF.md             ← this file
├── .gitignore
├── cerebral/
│   ├── main.py            ← entry point; WS server + audio + profile + TTS + bridge
│   ├── requirements.txt   ← websockets, vosk, faster-whisper, sounddevice, numpy, kokoro, soundfile
│   ├── audio/
│   │   ├── __init__.py
│   │   ├── pipeline.py    ← Vosk passive listener + faster-whisper
│   │   └── rolling_buffer.py
│   ├── bridge/
│   │   ├── __init__.py
│   │   └── openclaw.py    ← ChannelBridge — Telegram/Discord/etc. via OpenClaw
│   ├── db/
│   │   ├── __init__.py
│   │   └── profiles.py    ← ProfileManager + Profile dataclass; update_voice() added
│   ├── tts/
│   │   ├── __init__.py
│   │   └── engine.py      ← TTSEngine (Kokoro); speak(), list_voices(), ready
│   ├── llm/
│   │   ├── __init__.py
│   │   └── router.py      ← ModelRouter; complete(), switch_model(); OllamaBackend + ClawBackend
│   ├── mcp/
│   │   ├── __init__.py
│   │   └── orchestrator.py ← MCPOrchestrator; Tool/ToolResult/Plugin; discover_plugins()
│   ├── action_queue/        ← renamed from queue/ (stdlib shadowing fix)
│   │   ├── __init__.py
│   │   └── manager.py      ← QueueManager + QueueItem; SQLite-backed; :memory: in tests
│   ├── memory/
│   │   ├── __init__.py
│   │   └── manager.py      ← MemoryManager + Memory; ChromaDB vector + SQLite prefs; chroma_client injection
│   ├── passive/
│   │   ├── __init__.py
│   │   └── extractor.py         ← FiveW1HExtractor + CandidateAction; extract(transcript) → CandidateAction | None
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── conftest.py          ← skips @pytest.mark.integration unless explicitly selected
│   │   ├── test_plugins_browser.py ← 25 unit tests (Browser MCP)
│   │   ├── test_router.py       ← 10 unit tests + 3 integration tests
│   │   ├── test_orchestrator.py ← 20 unit tests
│   │   ├── test_queue.py        ← 22 unit tests
│   │   ├── test_extractor.py    ← 17 unit tests (5W1H extraction)
│   │   ├── test_passive_pipeline.py ← 11 unit tests (signal word detection)
│   │   └── test_memory.py       ← 22 unit tests (memory manager)
│   ├── pytest.ini         ← asyncio_mode=auto, pythonpath=..
│   ├── scripts/
│   │   └── download_models.py  ← downloads Vosk + warms up Kokoro
│   └── data/              ← gitignored; openmind.db lives here
├── tray/
│   ├── main.js            ← Electron entry; tray, WS client, profile events
│   ├── package.json       ← electron ^33, ws ^8, pngjs ^7
│   ├── assets/
│   │   └── icon.png       ← auto-generated 32x32 purple circle (gitignored)
│   ├── scripts/
│   │   └── create-icon.js ← run via `npm run prepare`
│   ├── lib/
│   │   ├── visualiser-state.js    ← VisualiserState (EventEmitter); state machine + toggle
│   │   ├── position-store.js      ← PositionStore; read/write JSON position file
│   │   ├── settings-store.js      ← SettingsStore; JSON persistence with defaults + corrupt-file safety
│   │   └── notification-manager.js ← NotificationManager; opt-in OS alerts + periodic reminder
│   ├── tests/
│   │   ├── visualiser-state.test.js    ← 22 Jest unit tests
│   │   ├── position-store.test.js      ← 5 Jest unit tests
│   │   ├── settings-store.test.js      ← 5 Jest unit tests
│   │   └── notification-manager.test.js ← 18 Jest unit tests
│   └── windows/
│       ├── profile-setup.html  ← conversational first-run form with recording
│       ├── queue.html          ← dark queue pulldown; Approve/Dismiss per item; empty state
│       └── visualiser.html     ← 200×200 animated orb overlay; 4 CSS state classes
├── plugins/
│   ├── browser.py         ← Browser plugin: web_search, navigate, read_pdf (via OpenClaw)
│   └── clock.py           ← Clock plugin: get_time (IANA tz), set_alarm stub
└── docs/
    ├── adr/               ← ADR-0001 through ADR-0004
    ├── issues/            ← issue body docs for all 29 issues
    └── prd-v1.md
```

---

## IPC protocol (WebSocket JSON messages)

### Cerebral → Tray
| type | data | meaning |
|------|------|---------|
| `heartbeat` | `{status, audio, tts, profile, model}` | sent every 5 s; `model` = active model id |
| `first_run` | — | no profiles exist; open setup form |
| `profile_loaded` | Profile object | active profile (on connect + on change) |
| `profiles_list` | `{profiles:[...]}` | full list (on connect + on change) |
| `voices_list` | `{voices:[{id,name,accent,gender},...]}` | Kokoro voice catalogue (on connect) |
| `wake` | `{transcript}` | wake word detected + transcript ready |
| `passive` | `{status}` | returned to passive mode after wake |
| `tts_speaking` | `{text, voice_id}` | TTS synthesis started |
| `tts_done` | `{}` | TTS playback finished |
| `thinking` | — | LLM processing started (between wake and tts_speaking) |
| `model_switched` | `{model_id}` | active model changed (ack of `switch_model` request) |
| `tools_list` | `{tools:[...]}` | LLM-formatted tool list (ack of `list_tools`) |
| `tool_result` | `{name, content, is_error}` | result of a `call_tool` request |

### Tray → Cerebral
| type | data | meaning |
|------|------|---------|
| `shutdown` | — | tray quitting; Cerebral should stop |
| `create_profile` | `{name, wake_name, voice_sample, wake_sample}` | new profile from setup form |
| `switch_profile` | `{id}` | change active profile |
| `delete_profile` | `{id}` | remove profile |
| `list_profiles` | — | request profiles_list event |
| `list_voices` | — | request voices_list event |
| `set_voice` | `{voice_id}` | update active profile's voice; takes effect immediately |
| `switch_model` | `{model_id}` | change active LLM (e.g. `"claude/haiku"`); persisted to `profiles.active_model` |
| `refresh_models` | — | re-query Ollama `/api/tags`; broadcasts `models_list` (#37) |
| `list_tools` | — | request tools_list broadcast |
| `call_tool` | `{name, args}` | invoke a tool by name |

---

## Dev start sequence

```bash
# Terminal 1
cd cerebral
pip install -r requirements.txt
python scripts/download_models.py   # one-time: ~40 MB Vosk + ~330 MB Kokoro
python main.py

# Terminal 2
cd tray
npm install
npm start
```

### Issue #6 — Model router ✅
- `cerebral/llm/router.py` — `ModelRouter` + `OllamaBackend` + `ClawBackend`
  - `complete(prompt, task_type)` — async; routes to active backend; logs which model handled request
  - `switch_model(model_id)` — takes effect immediately, raises `ValueError` for unknown models
  - `ModelUnavailableError` — raised on backend failure, never silently falls back to cloud
  - Default: `ollama/gemma4` via `OllamaBackend` (POST `http://localhost:11434/api/generate`)
  - Cloud: any model via `ClawBackend` (POST `http://localhost:3000/v1/chat/completions`)
  - Backends are injected via constructor — fully testable without live services
- `cerebral/tests/test_router.py` — 10 unit tests (always run) + 3 integration tests (`@pytest.mark.integration`, skipped unless `-m integration`)
- `cerebral/pytest.ini` — `asyncio_mode = auto`, `pythonpath = ..`, integration marker defined
- `cerebral/tests/conftest.py` — skips integration tests unless explicitly selected
- `main.py` updated: `_router = ModelRouter()` at startup; `_on_wake` now calls LLM and speaks response; `switch_model` IPC message handled; `model` field added to heartbeat

**Run unit tests:** `cd cerebral && python -m pytest tests/test_router.py -v`
**Run integration tests (Ollama must be running):** `python -m pytest tests/test_router.py -m integration -v`

### Issue #7 — MCP orchestrator ✅
- `cerebral/mcp/orchestrator.py` — `MCPOrchestrator` + `Tool` + `ToolResult` + `Plugin` protocol
  - `register(plugin)` / `unregister(plugin_name)` — runtime registry management
  - `list_tools()` — unified list across all registered plugins
  - `call_tool(name, args)` — routes to correct plugin; returns `ToolResult(is_error=True)` on unknown tool or plugin exception, never raises
  - `discover_plugins(path)` — auto-loads `*.py` files; each must expose `create() -> Plugin`; skips `_` prefixed files; silently skips broken modules
  - `tools_for_llm` property — returns Anthropic/OpenAI-compatible `[{name, description, input_schema}]`
- `plugins/clock.py` — starter Clock plugin: `get_time` (with optional IANA timezone), `set_alarm` (stub)
- `cerebral/tests/test_orchestrator.py` — 20 unit tests covering all acceptance criteria
- `main.py` updated: `_orc = MCPOrchestrator()` at startup; `discover_plugins()` runs on start; `list_tools` / `call_tool` IPC messages handled; tool count logged in heartbeat path

**Run tests:** `cd cerebral && python -m pytest tests/ -v`

---

### Issue #8 — Tray queue pulldown ✅
- `cerebral/queue/manager.py` — `QueueItem` dataclass + `QueueManager` (SQLite-backed)
  - `add_item(title, summary, tool_name=None, tool_args=None)` → `QueueItem`
  - `approve_item(item_id)` → `QueueItem | None` (marks approved, caller executes tool)
  - `dismiss_item(item_id)` → `bool`
  - `get_pending()` → `list[QueueItem]` (ordered by created_at)
  - `clear_all()` — used in tests and "Clear all" UI button
  - `QueueItem.to_dict()` — IPC-safe serialisation (id, title, summary, status, tool_name, tool_args, created_at)
  - Uses same `openmind.db`; `:memory:` in tests
- `cerebral/tests/test_queue.py` — 22 unit tests (always run); covers all CRUD paths + persistence
- `cerebral/main.py` updated:
  - `_queue = QueueManager()` at startup
  - `queue_update {items:[...]}` sent to new connections + after every state change
  - `queue_pending` count added to heartbeat payload
  - New IPC handlers: `list_queue`, `approve_item {item_id}`, `dismiss_item {item_id}`
  - Approve: marks approved → calls tool via `_orc.call_tool()` if tool_name set → emits `queue_item_result`
- `tray/windows/queue.html` — dark themed pulldown (matches profile-setup palette)
  - Lists pending items: title + summary + optional tool badge
  - Approve / Dismiss buttons per item; optimistic dismiss; result flash (green/red) then auto-remove
  - Empty state: "No pending actions"
  - "Clear all" footer button (dismisses all pending)
- `tray/main.js` updated:
  - `pendingItems` state; `queueWindow` singleton
  - `tray.on('click', openQueueWindow)` — left-click opens queue
  - Context menu: "Queue (N pending)" item when items exist
  - Tooltip: "Felix — N pending actions" when queue non-empty
  - `tray.setTitle()` sets numeric badge on macOS menu bar
  - IPC handlers: `queue:approve`, `queue:dismiss`, `queue:clear`, `queue:request`
  - `queue_update` and `queue_item_result` Cerebral events forwarded to queue window

**New IPC messages:**

| direction | type | data | meaning |
|---|---|---|---|
| Tray → Cerebral | `list_queue` | — | request current queue |
| Tray → Cerebral | `approve_item` | `{item_id}` | user approved |
| Tray → Cerebral | `dismiss_item` | `{item_id}` | user dismissed |
| Cerebral → Tray | `queue_update` | `{items:[...]}` | full pending list after any change |
| Cerebral → Tray | `queue_item_result` | `{item_id, result, is_error}` | tool execution result after approve |

**Run tests:** `cd cerebral && python -m pytest tests/ -v`

---

### Issue #9 — Visualiser ✅
- `tray/lib/visualiser-state.js` — `VisualiserState` (extends `EventEmitter`)
  - Four states: `passive` | `active` | `speaking` | `thinking`
  - `handleEvent(event)` maps Cerebral IPC types → state; returns `{state, visible, changed}`
  - `toggle()` flips visibility; returns `{visible}`
  - Emits `change` on state change or toggle
- `tray/lib/position-store.js` — `PositionStore`; read/write JSON file; silently ignores errors
- `tray/windows/visualiser.html` — 200×200 frameless transparent overlay
  - CSS-animated purple orb + outer ring; four state classes with distinct animations:
    - **passive** — slow 3.5s pulse
    - **active** — brighter/faster 1.1s pulse with enlarged glow
    - **thinking** — cyan tint, rotating ring spinner
    - **speaking** — warm orange tint, waveform bars below orb
  - Smooth 0.6s CSS transitions between all state changes
  - Receives `visualiser:state` IPC message → swaps CSS class
- `tray/main.js` updated:
  - `VisualiserState` + `PositionStore` instantiated at startup
  - `routeToVisualiser(event)` called for `wake`, `thinking`, `passive`, `tts_speaking`, `tts_done`
  - `openVisualiserWindow()` — frameless, always-on-top, click-through (`setIgnoreMouseEvents(true)`)
  - `toggleVisualiser()` — show/hide without restart; saves position on hide
  - Position restored from `cerebral/data/visualiser-pos.json`; defaults to bottom-right corner
  - Tray context menu: "Show Visualiser" / "Hide Visualiser" toggle item
- `cerebral/main.py` updated:
  - `await _broadcast({"type": "thinking"})` added in `_process_command` before LLM call
- `tray/tests/visualiser-state.test.js` — 22 Jest unit tests
- `tray/tests/position-store.test.js` — 5 Jest unit tests
- `tray/package.json` — Jest `^29` added; `npm test` runs suite

**New IPC messages:**

| direction | type | data | meaning |
|---|---|---|---|
| Cerebral → Tray | `thinking` | — | LLM processing has started |
| Tray renderer → renderer | `visualiser:state` | `state` string | main→visualiser window state push |

**Run tray tests:** `cd tray && npm test`
**Run all tests:** `cd cerebral && python -m pytest tests/ -v && cd ../tray && npm test`

---

### Issue #10 — Notification system ✅
- `tray/lib/settings-store.js` — `SettingsStore(filePath, defaults)`
  - `get(key)` / `set(key, value)` — JSON-backed persistence; corrupt-file graceful fallback; returns defaults for missing keys
  - Settings persisted to `cerebral/data/felix-settings.json`
- `tray/lib/notification-manager.js` — `NotificationManager({ store, notify, onNotificationClick })`
  - `enabled` (default `false`) / `intervalMinutes` (default `120`) — read-only getters
  - `setEnabled(bool)` — persists, starts/stops periodic reminder timer
  - `setIntervalMinutes(n)` — persists, restarts timer; `0` disables reminder
  - `handleQueueUpdate(items)` — fires OS notification when enabled and queue grows; one notification per growth event
  - `destroy()` — cancels reminder timer on quit
  - `notify` dependency injected → fully testable without Electron
- `tray/main.js` updated:
  - `Notification` added to Electron imports
  - `SettingsStore` + `NotificationManager` instantiated at startup
  - `queue_update` event calls `notifManager.handleQueueUpdate(pendingItems)` before menu refresh
  - Tray menu: "Notifications: On/Off" toggle; when on, "Reminder interval" submenu (Off / 30 min / 1 hr / 2 hr / 4 hr)
  - `onNotificationClick` → `openQueueWindow()` (clicking the OS alert opens the tray pulldown)
  - `quit()` calls `notifManager.destroy()` to cancel timers cleanly
- `tray/tests/settings-store.test.js` — 5 Jest tests (defaults, corrupt file, persistence, round-trip)
- `tray/tests/notification-manager.test.js` — 18 Jest tests (all acceptance criteria covered)

**JS test count: 50 passing (was 27)**
**Python test count: 52 passing (unchanged)**

**New IPC messages:** none — notification system is entirely tray-side; Cerebral has no involvement.

**Settings file:** `cerebral/data/felix-settings.json` (gitignored via existing `data/` rule)

---

### Issue #15 — OS tools MCP ✅

Five new plugins in `plugins/`, all auto-loading via `discover_plugins()`. No changes to `main.py` required.

- `plugins/files.py` — **Files**: `create_file`, `read_file`, `move_file`, `delete_file`, `search_files` (glob via `Path.rglob`); pure pathlib, no deps
- `plugins/shell.py` — **Shell**: `run_command → {stdout, stderr, exit_code}`; injectable `run_fn` (defaults to `subprocess.run`); raises `ToolResult(is_error=True)` on timeout
- `plugins/system.py` — **System**: `get_volume`, `set_volume`, `take_screenshot`, `get_wifi_status`, `shutdown`, `restart`; platform-aware (Windows/Linux/macOS); `shutdown`/`restart` are **safety-stubbed** — they return a message but never execute; injectable `run_fn`
- `plugins/apps.py` — **Apps**: `list_running → {apps:[{name,pid}]}`, `launch_app`, `close_app` (by name or PID); injectable `process_iter` + `popen_fn`; uses `psutil` by default
- `plugins/clipboard.py` — **Clipboard**: `read_clipboard`, `write_clipboard`, `list_clipboard_history`; history is in-process RAM deque (max 50, newest first, never persisted); injectable `read_fn`/`write_fn`; falls back pyperclip → tkinter → in-memory noop
- `cerebral/tests/test_plugins_os.py` — 43 unit tests (TDD vertical slices); all side effects injected; real filesystem used for Files tests via `tmp_path`
- `cerebral/requirements.txt` — added `psutil>=5.9.0`, `pyperclip>=1.8.0`

**Plugin registration:** all 6 plugins (clock + 5 new) auto-load at startup → 20 tools total

**Python test count: 196 passing (was 153), 3 skipped**

---

## Open GitHub issues (next in order)

All issues are at https://github.com/iggyghub/OpenMind

| # | Title | Blocked by |
|---|-------|-----------|
| ~~6~~ | ~~Model router — Ollama/Gemma 4 via OpenClaw + first spoken LLM response~~ | ✅ done |
| ~~7~~ | ~~MCP orchestrator — plugin registry, tool discovery, tool call routing~~ | ✅ done |
| ~~8~~ | ~~Tray app — queue pulldown, approve/dismiss actions, tray icon states~~ | ✅ done |
| ~~9~~ | ~~Visualiser — dark animated orb/waveform, state-reactive overlay~~ | ✅ done |
| ~~10~~ | ~~Notification system — opt-in OS alerts + configurable periodic reminders~~ | ✅ done |
| ~~11~~ | ~~Passive 5W1H — ambient intent extraction, auto-populate queue~~ | ✅ done |
| ~~12~~ | ~~Memory manager — RAM buffer + ChromaDB long-term + SQLite structured~~ | ✅ done |
| ~~13~~ | ~~Insights engine — passive preference learning + Insights view with full CRUD~~ | ✅ done |
| ~~14~~ | ~~Environmental context — camera + GPS/IP feeds into short-term memory~~ | ✅ done |
| ~~15~~ | ~~OS tools MCP — Files, Shell, System, Apps, Clipboard plugins~~ | ✅ done |
| ~~16~~ | ~~Time & Notes MCP — Clock timers/reminders, Scheduler, Notes~~ | ✅ done |
| ~~17~~ | ~~Browser MCP — web search, navigate, summarise via OpenClaw Playwright~~ | ✅ done |
| ~~18~~ | ~~n8n integration — MCP bridge: list_workflows, trigger_workflow, get_workflow_result~~ | ✅ done |
| ~~19~~ | ~~n8n credential check — verify Google OAuth configured, surface status in heartbeat~~ | ✅ done |
| ~~20~~ | ~~Google Workspace MCP — Gmail, Calendar, Drive, Sheets via n8n~~ | ✅ done |
| ~~21~~ | ~~Local OSS fallbacks — Grist, IMAP/SMTP, Nextcloud, LibreOffice, OpenStreetMap~~ | ✅ done |
| ~~22~~ | ~~OpenClaw channel bridge — Telegram/Discord/WhatsApp via OpenClaw harness~~ | ✅ done |
| ~~23~~ | ~~Communication MCP — Zoom + Google Meet via n8n, phone calls via OpenClaw~~ | ✅ done |
| ~~24~~ | ~~Dev tools MCP — Git, GitHub, Docker, SSH, Package Managers, HTTP Client~~ | ✅ done |
| ~~25~~ | ~~Information MCP — Wikipedia, Weather (Open-Meteo), News (RSS), Stocks/Crypto~~ | ✅ done |
| ~~26~~ | ~~Security MCP — Bitwarden read-only vault, VPN, Network Scanner~~ | ✅ done |
| ~~27~~ | ~~Hardware MCP — Printer/Scanner + Steam launcher~~ | ✅ done |
| ~~28~~ | ~~Finance MCP — Invoice/Receipt OCR to Google Sheets / Grist~~ | ✅ done |
| ~~29~~ | ~~Model switching UI — model browser in tray, runtime switching, per-task mapping, cloud indicator~~ | ✅ done |
| ~~30~~ | ~~Plugin builder — NL to generated MCP server, auto-register~~ | ✅ done |
| ~~37~~ | ~~Persistent model selection + Ollama refresh~~ | ✅ done |
| ... | (29 total) | |

**Queue empty.** All 29 vertical-slice issues have landed. Future scope is tracked in `CONTEXT.md` "Not in scope yet"; run `/to-issues` against one of those chunks to seed the next round.

---

### Issue #29 — Model switching UI ✅

Backend additions to `cerebral/llm/router.py` plus a new tray submenu.
`ModelRouter` now exposes a model registry with metadata so the tray can
render an informed model picker, and a per-task-type override map so
"chat" and "extraction" can be routed to different models without
changing the active model.

- `cerebral/llm/router.py` — `ModelRouter` extensions:
  - `models` registry (id → `{label, is_cloud}`) injected alongside
    `backends`. Defaults synthesise `{label: id, is_cloud: False}` for
    every backend when not supplied. Real backends register
    `ollama/gemma4` (Gemma 4, local), `claude/haiku` (Claude Haiku 4.5,
    cloud), `claude/sonnet` (Claude Sonnet 4.6, cloud).
  - `list_models() → list[dict]` — every model with `is_active` and
    `is_last` flags. The tray uses this directly to build the radio
    submenu.
  - `last_model` property — id of the model that handled the most recent
    `complete()` call. Set only on success — failures leave it
    unchanged so the visible "last used" stays truthful.
  - `active_is_cloud` — convenience flag for the tray's `☁`
    indicator. Reflects whichever model is currently active, regardless
    of per-task overrides.
  - `set_task_model(task_type, model_id)` / `get_task_model(task_type)` /
    `task_models()` — pinning per task type. `set_task_model(task,
    None)` clears the mapping. `complete()` resolves the backend by
    `task_models.get(task_type, active_model)`, so a per-task pin wins
    over the active model. Unknown model id → `ValueError`.
- `cerebral/main.py` updated:
  - `_models_list_event()` — broadcast helper returning `{models,
    active, last, active_is_cloud, task_models}`.
  - `_pulse_back_to_passive(delay=1.2)` — `asyncio.create_task` after
    `model_switching` is broadcast, so the visualiser briefly shows the
    "thinking" animation and then returns to passive.
  - New IPC handlers: `list_models`, `set_task_model {task_type,
    model_id}`. Existing `switch_model` handler now also broadcasts
    `model_switching` (visualiser pulse) + `models_list` (refresh tray).
  - Connection greeting includes `models_list`.
  - Heartbeat carries new fields `last_model` and `active_is_cloud`.
- `tray/lib/model-menu.js` — new `buildModelSubmenu()` helper. Pure
  function (no Electron import) returning a Menu template array. Drives
  the active-model radio set, the cloud `☁` / local `◉` indicator, the
  "(last)" marker on the most recent non-active model, and one
  per-task-type submenu (`chat`, `extraction`) with a "Use active
  model" entry plus one radio per known model.
- `tray/main.js` updated:
  - State: `modelsList`, `activeModel`, `lastModel`, `activeIsCloud`,
    `taskModels`. Updated by the `models_list` event.
  - Tray menu inserts a "Model: ☁/◉ {id}" entry with the model submenu
    (between "Insights" and "Notifications").
  - `model_switching` event → `routeToVisualiser` (state machine flips
    to `thinking`); the follow-up `passive` broadcast 1.2 s later
    reverts.
- `tray/lib/visualiser-state.js` — `STATE_MAP['model_switching'] =
  'thinking'` so the existing CSS spin animation in
  `tray/windows/visualiser.html` runs unchanged on model switch.

**Tests:**
- `cerebral/tests/test_router.py` — 13 new unit tests: list_models
  metadata + active/last flags, last_model tracking through
  switch+complete, last_model unchanged on failure, active_is_cloud,
  default metadata when not supplied, set_task_model pin/clear/unknown,
  complete resolves task pin, complete falls back to active when no
  pin, per-task pin survives switch_model, task_models() returns a
  copy.
- `tray/tests/model-menu.test.js` — 18 new unit tests: header active +
  cloud/local indicator, last-used row visibility, radio entries (one
  per model, active checked, click fires onSwitchModel, cloud `☁`
  marker, `(last)` marker), per-task submenus (one per task type,
  "Use active" + every model, default check, pinned check, click
  routes to onSetTaskModel with correct id / null), empty-models
  degenerate case, formatModelLabel fallback.
- `tray/tests/visualiser-state.test.js` — 1 new test: `model_switching`
  event resolves to `thinking` state.

**Python test count: 677 passing (was 664), 3 skipped**
**JS test count: 69 passing (was 50)**

**New IPC messages:**

| direction | type | data | meaning |
|---|---|---|---|
| Tray → Cerebral | `list_models` | — | request current model registry |
| Tray → Cerebral | `set_task_model` | `{task_type, model_id\|null}` | pin/clear a task→model mapping |
| Cerebral → Tray | `models_list` | `{models, active, last, active_is_cloud, task_models}` | full registry snapshot |
| Cerebral → Tray | `model_switched` | `{model_id, is_cloud}` | acks a switch_model |
| Cerebral → Tray | `model_switching` | `{model_id}` | visualiser cue (pulses thinking) |

**Demo paths:**
- "Felix, switch to Claude" → user picks `☁ Claude Haiku 4.5` from the
  tray Model submenu → next request goes through `ClawBackend`. The
  visualiser pulses `thinking` for ~1.2 s and the menu now shows
  `Model: ☁ claude/haiku`.
- Per-task pinning → user opens `Task: extraction` submenu and picks
  Gemma 4 — passive 5W1H extraction stays local while ad-hoc chat
  uses whatever's active (cloud is fine, but the always-on extractor
  doesn't leak ambient transcripts to the cloud).
- `Last used: …` row in the Model submenu surfaces which model
  handled the most recent request — useful when a per-task mapping
  routes a single request to a different model than active.

---

### Issue #26 — Security MCP ✅

Three new plugins in `plugins/`, all auto-loading via `discover_plugins()`. No
changes to `main.py` required. Two CLI-shell plugins (bitwarden, vpn) and one
mixed CLI/socket plugin (network_scanner). All side effects (`run_fn`,
`socket_factory`, `http_get`) are injected so unit tests never invoke the
real `bw`/`rasdial`/`scutil`/`nmcli`/`arp`/`ping` binaries and never open a
real socket.

- `plugins/bitwarden.py` — **BitwardenPlugin**: 3 tools — `bw_unlock(
  master_password)`, `bw_get_item(name)`, `bw_list_items(folder?)`.
  - **Read-only by design**: no `bw_create` / `bw_edit` / `bw_delete` tool
    exists. The orchestrator `list_tools()` is unit-tested against an
    explicit forbidden-name allowlist so a regression that adds a write tool
    fails the test suite.
  - **HITL secrets hygiene**: the master password is forwarded directly to
    `bw unlock --raw` via stdin/env (`BW_PASSWORD`) and the local reference
    is dropped after the call. The session token returned by `bw unlock` is
    held in `self._session_token` (RAM-only, never persisted) and forwarded
    to subsequent `bw` calls via the `BW_SESSION` env var. Neither the
    password nor the session token is ever echoed back to the LLM — the
    only thing the LLM sees from a successful unlock is `{"unlocked": true}`.
- `plugins/vpn.py` — **VpnPlugin**: 3 tools — `vpn_connect(profile_name)`,
  `vpn_disconnect()`, `vpn_status()`.
  - Platform-aware via injectable `platform_name` (default `sys.platform`):
    Windows → `rasdial`, macOS → `scutil --nc start/stop`, Linux →
    `nmcli connection up/down`. All three branches covered by tests.
  - `vpn_connect` requires a non-empty `profile_name` — there is no default
    profile, no auto-connect path. The profile must already exist in the OS
    network settings; Felix only triggers it.
  - `vpn_status()` returns `{connected, profile, ip}`. The current public IP
    is fetched via the same `http://ip-api.com/json/` endpoint used by
    `cerebral/environment/context.py`; injectable `http_get` (defaults to
    `urllib.request.urlopen`); IP fetch failures fall back to `ip=None`
    rather than failing the whole status call.
- `plugins/network_scanner.py` — **NetworkScannerPlugin**: 3 tools —
  `net_list_devices()`, `net_ping(host, count?)`, `net_check_port(host,
  port, timeout?)`.
  - `net_list_devices` shells out to `arp -a` and parses both the Windows
    two-column table format and the POSIX `host (ip) at mac on iface`
    format. Hostnames are returned when known (POSIX `?` becomes `null`).
  - `net_ping` uses the platform-correct count flag (`-n` on Windows,
    `-c` elsewhere) via `platform_name` injection. Default count 4. Returns
    `{stdout, stderr, exit_code}`; non-zero exit → `is_error=True`.
  - `net_check_port` uses an injected `socket_factory(addr, timeout)` that
    defaults to `socket.create_connection`. Returns `{open: bool}`.
    `ConnectionRefusedError` and `socket.timeout` collapse to `open: false`
    rather than `is_error` — a closed port is a successful check.

**Tool naming:** every tool is prefixed with the plugin name (`bw_*`,
`vpn_*`, `net_*`) — see `.learnings/LEARNINGS.md` after #23 about the
flat-global tool-name namespace.

**Tests:** one file per plugin, all side effects injected.
- `cerebral/tests/test_plugin_bitwarden.py` — 24 unit tests, including an
  explicit assertion that no write tools are exposed and a hygiene test
  that walks `plugin.__dict__` to confirm the master password is never
  retained as an attribute.
- `cerebral/tests/test_plugin_vpn.py` — 21 unit tests, parameterised across
  the Windows/Darwin/Linux/unknown branches.
- `cerebral/tests/test_plugin_network_scanner.py` — 26 unit tests covering
  both ARP formats, both ping flag conventions, and the open/refused/timeout
  socket cases.

**Plugin tool count:** 27 plugins → 93 tools total
(+3 bitwarden +3 vpn +3 network_scanner = +9 over #25).

**Required external binaries:** `bw` (Bitwarden CLI), `rasdial` (Windows) /
`scutil` (macOS) / `nmcli` (Linux), `arp`, `ping`. All shell-outs return
`is_error=True` if the binary is missing — same fail-loud pattern as
`plugins/git.py` etc.

**OS prerequisites:** VPN profiles must be pre-configured in the OS network
settings (Windows: Settings → VPN; macOS: System Settings → Network → VPN;
Linux: NetworkManager). Felix only triggers existing profiles, never
creates them.

**Python test count: 664 passing (was 593), 3 skipped**
**JS test count: 50 passing (unchanged)**

**Demo paths:**
- "Felix, am I connected to VPN?" → LLM calls `vpn_status({})` → returns
  `{connected:false, profile:null, ip:"203.0.113.5"}` → Kokoro speaks
  "You are not connected to a VPN; your public IP is 203.0.113.5."
- "Felix, what's my GitHub password?" → LLM prompts for master password →
  calls `bw_unlock({master_password:"…"})` → calls
  `bw_get_item({name:"github"})` → reads the password aloud once.
- "Felix, who else is on my network?" → LLM calls `net_list_devices({})` →
  Kokoro reads back the list of IPs and hostnames.

---

### Issue #27 — Hardware MCP ✅

Two new plugins in `plugins/`, both auto-loading via `discover_plugins()`. No
changes to `main.py` required. One CLI-shell plugin (printer) and one
filesystem + URL-scheme plugin (steam). All side effects (`run_fn`,
`platform_name`, `steam_root`, `launch_fn`, `process_iter`) are injected so
unit tests never invoke real CLI binaries (`lp`/`lpstat`/`scanimage` /
PowerShell), never open the browser, and never read the real Steam install.

- `plugins/printer.py` — **PrinterPlugin**: 4 tools — `print_file(path,
  printer_name?)`, `print_queue(printer_name?)`, `print_list_printers()`,
  `scan_document(output_path, format?)`.
  - Platform-aware via injectable `platform_name` (default `sys.platform`):
    Windows → PowerShell `Start-Process -Verb Print` (default printer) /
    `Out-Printer -Name "..."` (named printer) / `Get-PrintJob` /
    `Get-Printer | Select-Object -ExpandProperty Name`. POSIX → `lp` /
    `lpstat -o` / `lpstat -p` / `scanimage --format=<png|pdf>
    --output=<path>`. All branches covered by tests.
  - **Output-only by design**: no `print_remove_job` / `print_cancel_job`
    / `print_clear_queue` tool exists. The orchestrator `list_tools()` is
    unit-tested against an explicit forbidden-name allowlist so a regression
    that adds a destructive tool fails the test suite.
  - **File-path validation**: `print_file` and `scan_document` both reject
    empty paths up front, before any shell-out.
  - **Windows scan stub**: Windows WIA scanning isn't implemented (a fragile
    COM bridge is worse than not shipping it). The plugin returns
    `is_error=True` with a helpful message pointing to Windows Fax & Scan,
    documented in the docstring; tests assert this stub path.
  - **Hardware-not-connected**: each shell-out wraps non-zero exit and
    `FileNotFoundError` into `is_error=True` with the printer/scanner name
    in the message — same fail-loud pattern as `plugins/git.py`.
- `plugins/steam.py` — **SteamPlugin**: 3 tools — `steam_list_installed()`,
  `steam_launch(name? | app_id?)`, `steam_is_running(name? | app_id?)`.
  - Pure file parsing + URL-scheme launch — no shell-outs for the launch
    path. Parses `<steam_root>/config/libraryfolders.vdf` to discover every
    library, then walks `<library>/steamapps/appmanifest_*.acf` for each
    game (regex over the top-level VDF strings — no full VDF parser
    required).
  - Default `steam_root` per-platform (injectable for tests): Windows
    `C:\Program Files (x86)\Steam`, macOS `~/Library/Application
    Support/Steam`, Linux `~/.steam/steam` with fallback to
    `~/.local/share/Steam`. If `libraryfolders.vdf` is missing the Steam
    root itself is treated as the only library.
  - Launch via `steam://rungameid/<appid>` URL scheme using an injectable
    `launch_fn` (defaults to `webbrowser.open` — same pattern as
    `plugins/zoom.py`'s `zoommtg://` launch). Looks up the appid by name
    when `name` is provided; unknown name → `is_error=True`.
  - **Safety**: `steam_launch` requires an explicit `name` or `app_id`. No
    "launch the last/default game" path. Tests assert this.
  - `steam_is_running` matches by appid where possible; falls back to
    matching the game's installdir / executable name in the running
    process list (best-effort heuristic — Steam launches games as child
    processes whose name often matches the installdir). Uses an injectable
    `process_iter` defaulting to `psutil.process_iter` — same pattern as
    `plugins/apps.py`.
  - **Hardware-not-connected equivalent**: if `steam_root` doesn't exist,
    `steam_list_installed` returns `is_error=True` with `"Steam not
    installed at <path>"` — never crashes on missing files.

**Tool naming:** every tool is prefixed with the plugin name (`print_*`,
`scan_*`, `steam_*`) per the flat-global namespace rule in
`.learnings/LEARNINGS.md` (after the #23 zoom/meet `join_meeting` collision).

**Tests:** one file per plugin, all side effects injected (no real shell-
outs, no real browser, no real filesystem reads — `tmp_path` is used to
build a fake Steam library on disk).
- `cerebral/tests/test_plugin_printer.py` — 27 unit tests covering
  required-arg validation, the POSIX `lp`/`lpstat`/`scanimage` branch, the
  Windows PowerShell branch (incl. the documented WIA stub-error), and
  hardware-not-connected paths (non-zero exit + `FileNotFoundError`).
- `cerebral/tests/test_plugin_steam.py` — 19 unit tests covering
  libraryfolders.vdf + appmanifest_*.acf parsing across multiple
  libraries, missing-Steam-root error, `steam_launch` URL building (incl.
  case-insensitive name lookup), and the process-iter heuristic for
  `steam_is_running`.

**Plugin tool count:** 29 plugins → 100 tools total
(+4 printer +3 steam = +7 over #26).

**Required external binaries / installs:**
- `lp`, `lpstat`, `scanimage` — POSIX printer/scanner (CUPS + SANE).
  Windows uses built-in PowerShell cmdlets — no extra install.
- Steam must be installed at the platform default location (or pass a
  custom `steam_root` if installed elsewhere). The plugin doesn't shell
  out to the Steam CLI — it reads `appmanifest_*.acf` files directly.

**Python test count: 710 passing (was 664), 3 skipped**
**JS test count: 50 passing (unchanged)**

**Demo paths:**
- "Felix, launch Cyberpunk 2077" → LLM calls `steam_launch({name:
  "Cyberpunk 2077"})` → resolves to appid 1091500 → opens
  `steam://rungameid/1091500` → Steam starts the game.
- "Felix, scan this document and save as PDF to my Desktop" → LLM calls
  `scan_document({output_path:"~/Desktop/scan.pdf"})` → POSIX runs
  `scanimage --format=pdf --output=~/Desktop/scan.pdf`; on Windows, returns
  the documented Fax & Scan stub-error.
- "Felix, is Counter-Strike running?" → LLM calls `steam_is_running({name:
  "Counter-Strike 2"})` → returns `{running: true/false}` → Kokoro reports
  back.

---

### Issue #28 — Finance MCP ✅

One new plugin in `plugins/`, auto-loading via `discover_plugins()`. No
changes to `main.py` required. Pure-Python OCR plugin that delegates the
sheet append to the existing `google_workspace` plugin's
`sheets_write_range` tool — same delegation pattern `plugins/zoom.py`
uses for n8n. The Grist fallback already kicks in transparently via
`plugins/google_workspace_fallback.py`.

- `plugins/finance.py` — **FinancePlugin**: 2 tools —
  `finance_extract_receipt(image_path)` and
  `finance_log_expense(image_path, sheet_target, confirm=False, columns?)`.
  - **Extraction is side-effect-free.** `finance_extract_receipt`
    OCRs the image (or page 1 of a scanned PDF) and returns
    `{vendor, date, total, currency, line_items: [{description, amount}],
    confidence: {vendor, date, total, currency}}`. No sheet write — the
    LLM shows the user the result before committing.
  - **Append requires explicit confirm.** `finance_log_expense` defaults
    to `confirm=False`, in which case it returns the extraction and the
    would-be row but **does not** call the workspace plugin. Tests assert
    that `confirm=False` does not invoke `call_tool` on the workspace
    plugin, and `confirm=True` does, with the right `{spreadsheet_id,
    range, data}` payload. There is intentionally no autopilot path.
  - **Field extraction is regex-only** (no LLM in the plugin):
    - `total` — `(?i)(?:grand\s*total|total|amount|balance)\D{0,30}([0-9]+[.,][0-9]{2})`;
      keyword-anchored = confidence 1.0; bare currency-like number = 0.5;
      nothing = 0.0.
    - `currency` — `$/£/€/¥` → `USD/GBP/EUR/JPY`; literal 3-letter ISO
      4217 fallback. Default `None`, confidence 0.0.
    - `date` — ISO `YYYY-MM-DD` (1.0), `D MMM YYYY` (1.0), and
      `DD/MM/YYYY` / `MM/DD/YYYY` / dash variants (0.5 — locale-
      ambiguous, flagged for review).
    - `vendor` — first non-empty line that doesn't look like an address
      or pure digits. Confidence 0.5 (heuristic).
    - `line_items` — `^(.+?)\s+([0-9]+[.,][0-9]{2})\s*$`; the keyword
      line that produced `total` is excluded.
  - **Sheet column schema** is configurable via `sheet_target.columns`.
    Default: `[date, vendor, total, currency, items_summary, image_path]`
    (with `items_summary` joining line items as `desc amount; desc
    amount`). The plugin narrows the A1 range to the column count
    (`Sheet1!A:F` for 6 cols, `Sheet1!A:B` for 2, etc.) before forwarding
    to `sheets_write_range`.
  - **Sheet target shapes**: `{spreadsheet_id, sheet_name?, columns?}`
    for Google Sheets, `{grist_table, grist_doc_id?, columns?}` for
    Grist (the Grist fallback parses the table id from the range).
  - **Safety**: file-path validation rejects empty `image_path` and
    paths where `Path(image_path).is_file()` is False — surfaces the
    path in the error message, same fail-loud pattern as
    `plugins/printer.py`. No shelling out with the user-provided path —
    pure Python OCR + HTTP via the workspace plugin.
- **Injection points** (so tests never run real OCR, never read PDFs,
  never hit n8n):
  - `ocr_fn(image_path) -> str` defaults to
    `pytesseract.image_to_string(Image.open(...))`.
  - `pdf_to_image_fn(pdf_path) -> list[str]` defaults to
    `pdf2image.convert_from_path` saving page 1 to a tempfile (multi-
    page receipts are out of scope for v1).
  - `google_workspace_plugin` defaults to
    `plugins.google_workspace.create()`; tests pass a fake with a
    recording `call_tool` (mirrors the `n8n_plugin` injection in
    `plugins/zoom.py`).

**Tool naming:** both tools prefixed with `finance_` per the flat-global
namespace rule in `.learnings/LEARNINGS.md`.

**Tests:** one file — `cerebral/tests/test_plugin_finance.py` — 40 unit
tests covering required-arg validation, missing/empty/nonexistent image
paths, the happy-path extraction shape, total keyword-anchored vs.
bare-number confidence levels, currency symbol → ISO mapping (parameter-
ised across `$/£/€/¥` plus the ISO-code fallback), date format coverage
(ISO / D MMM / slash / dash), vendor heuristic (skipping addressy and
digits-only first lines), line-item parsing (excluding the total line),
the `confirm=False` non-write guarantee, the `confirm=True` payload
shape (incl. range narrowing for custom columns), error propagation
from the workspace plugin, the `{grist_table}` target routing through
the same `sheets_write_range` delegation, the PDF input path calling
`pdf_to_image_fn` then `ocr_fn`, the image input path skipping
`pdf_to_image_fn`, and the unknown-tool error.

**Plugin tool count:** 30 plugins → 102 tools total
(+2 finance over #27).

**Required external installs:**
- **Tesseract OCR binary** on PATH (Linux: `apt install tesseract-ocr`,
  macOS: `brew install tesseract`, Windows: UB-Mannheim build).
- **Poppler** for `pdf2image` PDF input (Linux: `apt install
  poppler-utils`, macOS: `brew install poppler`, Windows: poppler-windows
  release on the PATH).
- Python: `pytesseract`, `pdf2image`, `Pillow` — added to
  `cerebral/requirements.txt`.

**Python test count: 750 passing (was 710), 3 skipped**
**JS test count: 50 passing (unchanged)**

**Demo paths:**
- "Felix, what's on this receipt?" + image path → LLM calls
  `finance_extract_receipt({image_path: "/path/to/receipt.png"})` →
  Felix reads back vendor + total + low-confidence flags so the user
  can correct anything before logging.
- "Felix, add this receipt to my expense sheet." → LLM calls
  `finance_log_expense({image_path, sheet_target: {spreadsheet_id,
  sheet_name: "Expenses"}, confirm: false})` → Felix recites the row →
  user confirms → LLM re-calls with `confirm: true` → row appended.
- Offline → same `finance_log_expense` call; `google_workspace_fallback`
  detects the connectivity error, routes the same args to Grist.

---

### Issue #25 — Information MCP ✅

Four new plugins in `plugins/`, all auto-loading via `discover_plugins()`. No
changes to `main.py` required. All four are pure HTTP plugins (no CLI
shell-outs, no API keys) — they hit public open-data endpoints and are
fully testable with an injected `fetch_fn` / `parse_fn`.

- `plugins/wikipedia.py` — **WikipediaPlugin**: 2 tools — `wiki_search(query,
  max_results?)`, `wiki_summary(title)`. Uses the public action API
  (`https://en.wikipedia.org/w/api.php` opensearch) for search and the REST
  v1 endpoint (`https://en.wikipedia.org/api/rest_v1/page/summary/{title}`)
  for article summaries. Spaces in titles are URL-encoded. Default
  `fetch_fn` tries aiohttp then httpx — same fallback pattern as `plugins/
  n8n.py` and `plugins/http_client.py`.
- `plugins/weather.py` — **WeatherPlugin**: 2 tools — `weather_current(
  location)`, `weather_forecast(location, days?)`. Uses Open-Meteo
  (`https://api.open-meteo.com/v1/forecast`) — fully open source, no API
  key. Free-form locations are geocoded via the Open-Meteo geocoding
  endpoint (`https://geocoding-api.open-meteo.com/v1/search`) before the
  forecast call. `weather_forecast` defaults to 7 days; `days` overrides
  (max 16). Unknown locations → `is_error=True`.
- `plugins/news.py` — **NewsPlugin**: 2 tools — `news_headlines(topic?,
  source?, max_results?)`, `news_list_sources()`. Aggregates configurable
  RSS feeds. Default sources injected at construction: BBC, Reuters,
  Hacker News. Per-source failures are tolerated — a single broken feed
  doesn't poison the aggregate; only when *all* requested sources fail
  does the call return `is_error=True`. RSS parsing delegated to
  `feedparser` via an injectable `parse_fn`, so tests never hit the
  network and don't require feedparser. `feedparser>=6.0` added to
  `cerebral/requirements.txt`.
- `plugins/markets.py` — **MarketsPlugin**: 2 tools — `market_price(symbol,
  asset_type?)`, `market_quote(symbol, asset_type?)` (price + 24h change
  + market cap). Auto-detects crypto vs stock by symbol against a 24-coin
  allowlist (BTC, ETH, DOGE, SOL, …); explicit `asset_type` of `"crypto"`
  or `"stock"` overrides. Crypto routed to CoinGecko
  (`https://api.coingecko.com/api/v3/coins/markets`), stocks to Yahoo
  Finance's public chart endpoint
  (`https://query1.finance.yahoo.com/v8/finance/chart/{symbol}`). For
  stocks, `change_24h_pct` is computed from
  `(regularMarketPrice - previousClose) / previousClose`. Both endpoints
  no API key.

**Tool naming:** all tools prefixed with their plugin name
(`wiki_search`, `weather_current`, `news_headlines`, `market_price`) —
see `.learnings/LEARNINGS.md` after #23 (zoom/meet `join_meeting`
collision) about the flat-global tool-name namespace.

**Tests:** one file per plugin, all side effects injected.
- `cerebral/tests/test_plugin_wikipedia.py` — 15 unit tests.
- `cerebral/tests/test_plugin_weather.py` — 12 unit tests.
- `cerebral/tests/test_plugin_news.py` — 12 unit tests.
- `cerebral/tests/test_plugin_markets.py` — 15 unit tests.

**Plugin tool count:** 24 plugins → 84 tools total
(+2 wiki +2 weather +2 news +2 markets = +8 over #24).

**Required external services:** internet only — no API keys, no daemons.
All four endpoints are public open-data services.

**Python test count: 593 passing (was 539), 3 skipped**
**JS test count: 50 passing (unchanged)**

**Demo paths:**
- "Felix, what is the weather in London this week?" → LLM calls
  `weather_forecast(location="London", days=7)` → Open-Meteo geocode
  + forecast → LLM summarises → Kokoro speaks the 7-day outlook.
- "Felix, what is the Bitcoin price?" → LLM calls
  `market_price(symbol="BTC")` → CoinGecko returns price → Kokoro speaks
  the current USD price.
- "Felix, what's in today's headlines?" → LLM calls `news_headlines()` →
  feedparser pulls BBC + Reuters + HN → LLM picks top 3 → Kokoro reads.
- "Felix, what is general relativity?" → LLM calls
  `wiki_search("general relativity")` → picks first result → calls
  `wiki_summary(title)` → Kokoro speaks the lead extract.

---

### Issue #24 — Dev tools MCP ✅

Six new plugins in `plugins/`, all auto-loading via `discover_plugins()`. No
changes to `main.py` required.

**CLI-shell plugins** (inject `run_fn` defaulting to `subprocess.run`, same
pattern as `plugins/shell.py` and `plugins/system.py`; success path returns
`{stdout, stderr, exit_code}`; non-zero exit → `is_error=True`):

- `plugins/git.py` — **GitPlugin**: 7 tools — `git_status`, `git_commit(message)`,
  `git_push`, `git_pull`, `git_diff`, `git_log(max_count?)`, `git_branch(name?)`.
  All accept an optional `repo_path` (defaults to `os.getcwd()`); shells out
  to the local `git` binary via the injected runner.
- `plugins/docker.py` — **DockerPlugin**: 5 tools — `docker_list_containers(all?)`,
  `docker_start_container(name_or_id)`, `docker_stop_container(name_or_id)`,
  `docker_list_images`, `docker_build(path, tag?)`.
- `plugins/package_manager.py` — **PackageManagerPlugin**: 3 tools, three
  back-ends — `pkg_install(manager, name)`, `pkg_update(manager, name?)`,
  `pkg_search(manager, query)`. `manager` validated against allowlist
  `{npm, pip, winget}`; unknown manager → `is_error=True`. Per-manager argv
  shaping handles the small differences (e.g. `pip install -U` for update;
  `pip index versions` for search since `pip search` is disabled).
- `plugins/ssh.py` — **SshPlugin**: 1 tool — `ssh_run_command(host, command,
  port?, key_path?)`. Always sets `-o BatchMode=yes` so any auth challenge
  fails fast rather than blocking on an interactive prompt — no keys
  written, no passwords prompted.

**HTTP plugins**:

- `plugins/github.py` — **GithubPlugin**: 4 tools — `github_list_issues(repo)`,
  `github_create_issue(repo, title, body?)`, `github_list_prs(repo)`,
  `github_get_notifications()`. Delegates to an injected `N8nPlugin`, same
  pattern as `GoogleWorkspacePlugin` (#20). Required n8n workflow names:
  `Felix GitHub List Issues`, `Felix GitHub Create Issue`,
  `Felix GitHub List PRs`, `Felix GitHub Notifications`.
- `plugins/http_client.py` — **HttpClientPlugin**: 4 tools — `http_get`,
  `http_post`, `http_put`, `http_delete`. Each takes `(url, headers?, body?)`
  and returns `{status, body}`. Default `fetch_fn` tries aiohttp then httpx
  (same fallback pattern as `plugins/n8n.py`); JSON responses are parsed,
  non-JSON responses come back as raw text. Status outside 200-399 is
  surfaced as `is_error=True`.

**Tool naming:** all tools are prefixed with their plugin name
(`git_status`, `docker_list_containers`, `pkg_install`, `ssh_run_command`,
`github_list_issues`, `http_get`) — see `.learnings/LEARNINGS.md` after
#23 (zoom/meet `join_meeting` collision).

**Tests:** one file per plugin, all side effects injected.
- `cerebral/tests/test_plugin_git.py` — 21 unit tests (parameterised across
  the 7 subcommands with a single fake run_fn capturing argv).
- `cerebral/tests/test_plugin_docker.py` — 16 unit tests.
- `cerebral/tests/test_plugin_package_manager.py` — 23 unit tests.
- `cerebral/tests/test_plugin_ssh.py` — 13 unit tests.
- `cerebral/tests/test_plugin_github.py` — 14 unit tests.
- `cerebral/tests/test_plugin_http_client.py` — 15 unit tests.

**Plugin tool count:** 20 plugins → 76 tools total
(+7 git +5 docker +3 pkg +1 ssh +4 github +4 http = +24 over #23).

**Required external binaries:** Git/Docker/SSH/package-manager tools shell
out to the local CLIs (`git`, `docker`, `npm`, `pip`, `winget`, `ssh`).
They return `is_error=True` if the binary is missing from PATH.

**n8n setup required:** create the 4 GitHub workflows in n8n at
`localhost:5678` (workflow names must match exactly). The n8n GitHub
credential needs a personal-access-token with `repo` and `notifications`
scopes — see SETUP.md.

**Python test count: 539 passing (was 437), 3 skipped**
**JS test count: 50 passing (unchanged)**

**Demo path:** "Felix, what is the git status of my current repo?"
→ LLM calls `git_status({})`
→ shells out to `git status` in the working directory
→ returns `{stdout, stderr, exit_code}`
→ LLM summarises ("you have 4 modified files and 3 untracked files")
→ Kokoro speaks the answer.

---

### Issue #23 — Communication MCP ✅

Three new plugins in `plugins/`, all auto-loading via `discover_plugins()`. No
changes to `main.py` required.

- `plugins/zoom.py` — **ZoomPlugin**: delegates HTTP to an injected `N8nPlugin`
  and shells out to the local Zoom client via an injectable `launch_fn`
  (defaults to `webbrowser.open`, which honours the `zoommtg://` scheme that
  the desktop client registers on install).
  - `zoom_join_meeting(url? | id?, passcode?)` → triggers `"Felix Zoom Join"`
    via n8n, then calls `launch_fn(url or zoommtg://zoom.us/join?confno=ID)`.
    If n8n fails the launch is skipped and the error is returned — same
    fail-loud pattern used elsewhere.
  - `zoom_schedule_meeting(title, start, duration_minutes?, attendees?)` →
    triggers `"Felix Zoom Schedule"` via n8n.
  - `zoom_list_meetings(max_results?)` → triggers `"Felix Zoom List"` via n8n.
  - `create(n8n_plugin?, launch_fn?, fetch_fn?, base_url?, api_key?)` factory
    for orchestrator auto-registration.
- `plugins/meet.py` — **MeetPlugin**: reuses `GoogleWorkspacePlugin` (#20) so
  there's no duplicate n8n delegation chain — Meet links live on Google
  Calendar event payloads.
  - `meet_join_meeting(url)` → opens the URL via injectable
    `webbrowser_open_fn` (defaults to `webbrowser.open`). No n8n call —
    Meet runs in-browser.
  - `meet_schedule_meeting(title, start, end?, attendees?, description?)` →
    delegates to `GoogleWorkspacePlugin.calendar_create_event` with
    `add_conference=True` so the n8n calendar workflow attaches a Meet
    conference.
  - `meet_get_meeting_link(event_id)` → calls
    `GoogleWorkspacePlugin.calendar_list_events`, finds the event by id,
    returns the `hangoutLink` (or an error if missing).
  - `create(google_workspace_plugin?, webbrowser_open_fn?, n8n_plugin?, ...)`
    factory; falls back to building a fresh `GoogleWorkspacePlugin` from
    `n8n_plugin`/`fetch_fn` when nothing is injected.
- `plugins/phone.py` — **PhonePlugin**: HTTP client to OpenClaw's voice
  channel — no SIP/Twilio in Cerebral, same architecture as the channel
  bridge in #22.
  - `start_call(contact? | number?)` → POSTs to
    `<base_url>/voice/dial` (default `http://localhost:3000`) with body
    `{contact?, number?}`. Returns OpenClaw's response (typically a
    `call_id` + `status`).
  - Injectable `fetch_fn(url, body)` — defaults to aiohttp/httpx;
    `base_url` defaults to OpenClaw's port 3000.
  - `create(fetch_fn?, base_url?)` factory.

**Tool name namespacing:** Zoom and Meet both expose join/schedule
capabilities, but the MCP orchestrator routes by a flat tool name → plugin
map. So tool names are prefixed (`zoom_join_meeting`, `meet_join_meeting`,
…) — the LLM picks Zoom vs Meet by tool name. The plugin folder layout
matches the brief; only the tool names are namespaced.

**n8n setup required:** Create these 3 new workflows in n8n at
`localhost:5678`. They reuse the same Zoom OAuth credential created during
the n8n credentials step in #19. Workflow names must match exactly:
`Felix Zoom Join`, `Felix Zoom Schedule`, `Felix Zoom List`.

The Google OAuth credential from #19/#20 is reused for Meet via the
`Felix Calendar Create` and `Felix Calendar List` workflows — no new
workflows are needed for Meet.

**Zoom client:** the desktop client only needs to be installed if you want
`zoom_join_meeting` to actually open meetings (the plugin launches via the
`zoommtg://` URL scheme it registers). Schedule/list work without the
client installed.

- `cerebral/tests/test_plugin_zoom.py` — 21 unit tests (TDD vertical slices)
- `cerebral/tests/test_plugin_meet.py` — 20 unit tests (TDD vertical slices)
- `cerebral/tests/test_plugin_phone.py` — 13 unit tests (TDD vertical slices)

**Plugin tool count:** 14 plugins → 52 tools total
(+3 zoom + 3 meet + 1 phone = +7 over #22).

**Python test count: 437 passing (was 383), 3 skipped**
**JS test count: 50 passing (unchanged)**

**Demo paths:**
- Voice: "Felix, join my 3pm Zoom call"
  → LLM picks `zoom_list_meetings` to find the 3pm meeting
  → calls `zoom_join_meeting(url=...)`
  → n8n logs the join, Zoom desktop client opens the meeting
- Voice: "Felix, schedule a Zoom call with John tomorrow at 2pm"
  → LLM calls `zoom_schedule_meeting(title="Call with John",
    start="2026-05-04T14:00:00", attendees=["john@..."])`
  → n8n creates the meeting and the calendar invite
- Voice: "Felix, call Mum"
  → LLM calls `start_call(contact="Mum")`
  → POST to `localhost:3000/voice/dial`
  → OpenClaw rings Mum's phone

---

### Issue #22 — OpenClaw channel bridge ✅

A new module `cerebral/bridge/openclaw.py` connects Cerebral to OpenClaw's
external-agent stream so messages from Telegram/Discord/WhatsApp/etc. flow
through the same LLM + MCP path as voice wakes.

- `cerebral/bridge/openclaw.py` — **`ChannelBridge`**
  - **Constructor:** `ChannelBridge(process_fn, *, fetch_fn=None, ws_connect_fn=None, ws_url, outbound_url, api_key="", history_limit=16)`
  - **`start()`** — connects to OpenClaw's `ws_url`, iterates inbound JSON
    frames, hands each to `handle_inbound`. Logs a warning and returns
    cleanly on `ConnectionRefusedError`/`OSError` (graceful degradation —
    Cerebral keeps running on voice alone).
  - **`stop()`** — closes the WS and unblocks the loop.
  - **`handle_inbound(message: dict)`** — public entry point used by tests
    and the WS reader. Validates `sender_id` + `text`, calls
    `process_fn(text, history)`, appends `(user, assistant)` turns to the
    session buffer, POSTs the reply to `outbound_url`. On `process_fn`
    exception → falls back to a generic error reply but still posts.
  - **Session buffer** — RAM-only `dict[str, list[{role,text}]]` keyed by
    `f"{channel}:{sender_id}"`. Truncates to `history_limit` on append.
    `get_history(key)` and `reset_session(key)` exposed for inspection.
  - **Outbound** — POST to `outbound_url` with JSON body
    `{channel, sender_id, text, message_id?}`. `Authorization: Bearer …`
    header added when `api_key` is set; omitted otherwise. Failures are
    logged, not raised — one bad reply doesn't kill the loop.
  - **Default transports** — `_default_fetch` tries aiohttp then httpx;
    `_default_ws_connect` uses `websockets.connect`. Both are injected to
    `None` in tests via stubs/`AsyncMock`.
- `cerebral/main.py` updated:
  - `_bridge_process(transcript, history)` — folds recent history into the
    prompt and calls `_router.complete(...)`. Reuses the existing router
    so cloud/local model switching applies to channel messages too.
  - `_bridge = ChannelBridge(process_fn=_bridge_process, ws_url=…, …)`
    instantiated at module level. URLs and API key read from
    `OPENCLAW_WS_URL` / `OPENCLAW_REPLY_URL` / `OPENCLAW_API_KEY` env vars
    with sensible local defaults.
  - `main()` starts `_bridge.start()` as a background task right before
    the IPC server boots; on shutdown calls `await _bridge.stop()` then
    awaits the task with a 2 s grace period.
  - Heartbeat now includes `bridge: bool` so the tray can surface
    "channels connected" later.
- `cerebral/tests/test_bridge_openclaw.py` — **20 unit tests** (TDD slices):
  inbound routing, outbound POST shape + auth, per-`(channel, sender_id)`
  session isolation, history pass-through to `process_fn`, history-limit
  truncation, `process_fn` error → friendly reply, outbound failure
  swallowed, empty/whitespace text ignored, missing `sender_id` ignored,
  WS connect URL respected, WS frames drive `handle_inbound`,
  malformed JSON skipped, `stop()` closes the WS, graceful degradation
  when OpenClaw is unreachable, `running` lifecycle, `reset_session`.

**SETUP.md** updated with a step-by-step Telegram channel walkthrough
(BotFather → `~/.openclaw/openclaw.json` → env vars → restart) plus a note
that WhatsApp/Discord/Slack work the same way once enabled in OpenClaw —
no Cerebral changes required.

**New env vars (all optional):**

| Name | Default | Purpose |
|------|---------|---------|
| `OPENCLAW_WS_URL` | `ws://localhost:3000/agent/stream` | Inbound message stream |
| `OPENCLAW_REPLY_URL` | `http://localhost:3000/agent/reply` | Outbound reply endpoint |
| `OPENCLAW_API_KEY` | `""` | Optional bearer token for OpenClaw |

**Heartbeat field added:** `bridge: true|false` (true once the WS is connected).

**Python test count: 383 passing (was 363), 3 skipped**
**JS test count: 50 passing (unchanged)**

**Demo path:** "Felix, what time is it?" sent via Telegram
→ OpenClaw receives → forwards over WS to Cerebral
→ `ChannelBridge.handle_inbound` → `_bridge_process` → `_router.complete`
→ reply POSTed to `http://localhost:3000/agent/reply`
→ OpenClaw delivers it back to the Telegram chat.

---

### Issue #21 — Local OSS Fallbacks ✅

One new plugin in `plugins/`, auto-loading via `discover_plugins()`. No changes to `main.py` required.

- `plugins/google_workspace_fallback.py` — **GoogleWorkspaceFallbackPlugin**: wraps `GoogleWorkspacePlugin`; same `name`, same 8 tools, same `ToolResult` contract
  - **Offline detection**: if primary returns `is_error=True` AND the content is not a validation error (`"is required"` / `"Unknown tool"`), treat as connectivity failure and route to OSS fallback
  - **gmail_send → SMTP**: `smtplib.SMTP_SSL`; injectable `smtp_fn(host, port)` → mock SMTP; result `{sent, to, subject}`
  - **gmail_search → IMAP**: `imaplib.IMAP4_SSL`; injectable `imap_fn(host, port)` → mock IMAP4; result `{messages: [{from, subject, date, snippet}]}`
  - **sheets_read_range / write → Grist**: `GET/POST /api/docs/{docId}/tables/{tableId}/records`; injectable `fetch_fn`; `"Sheet1!A1:D10"` → `table_id="Sheet1"`
  - **drive_list_files / upload → Nextcloud**: WebDAV `PROPFIND`/`PUT` at configurable host; `nextcloud_url=None` returns a clear "not configured" error
  - **calendar_create_event / list_events**: no OSS fallback; primary error is returned unchanged
  - All env vars: `IMAP_HOST`, `IMAP_PORT`, `SMTP_HOST`, `SMTP_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `GRIST_URL`, `GRIST_API_KEY`, `NEXTCLOUD_URL`, `NEXTCLOUD_USERNAME`, `NEXTCLOUD_PASSWORD`
  - `create(primary?, fetch_fn?, imap_fn?, smtp_fn?, grist_url?, nextcloud_url?, ...)` factory
- Helper functions exported for testing: `_parse_imap_query(gmail_query)`, `_parse_grist_range(range_str)`
- `cerebral/tests/test_plugin_google_workspace_fallback.py` — 44 unit tests (TDD, all cycles)

**Intentional omissions:**
- Nextcloud is conditional on installation — drive tools return a clear "not configured" error when `nextcloud_url` is None
- LibreOffice (Docs/Slides) fallback is deferred — Docs/Slides tools are not yet in `GoogleWorkspacePlugin`; the growth loop will wire `run_fn` when those tools are added
- Nominatim (Maps) fallback is deferred — Maps tools are not yet in scope
- Calendar OSS fallback (local Scheduler) is a growth-loop candidate

**Plugin tool count:** unchanged (12 plugins, 40 tools — `google_workspace_fallback` uses the same plugin name and tool list as `google_workspace`; only one should be registered at a time)

**Python test count: 363 passing (was 319), 3 skipped**
**JS test count: 50 passing (unchanged)**

**Demo path (offline):** disable internet → "Felix, what emails do I have from John?"
→ LLM calls `gmail_search(query="from:john")`
→ `GoogleWorkspaceFallbackPlugin` tries n8n → gets `ConnectionError`
→ falls back to IMAP → returns message list
→ LLM summarises → Kokoro speaks

---

### Issue #20 — Google Workspace MCP ✅

One new plugin in `plugins/`, auto-loading via `discover_plugins()`. No changes to `main.py` required.

- `plugins/google_workspace.py` — **GoogleWorkspacePlugin**: delegates all HTTP to an injected `N8nPlugin`
  - `gmail_send(to, subject, body, cc?)` → triggers `"Felix Gmail Send"` workflow
  - `gmail_search(query, max_results?)` → triggers `"Felix Gmail Search"` workflow
  - `calendar_create_event(title, start, end?, attendees?, description?)` → triggers `"Felix Calendar Create"`
  - `calendar_list_events(from?, to?, max_results?)` → triggers `"Felix Calendar List"`
  - `drive_list_files(query?, folder_id?, max_results?)` → triggers `"Felix Drive List Files"`
  - `drive_upload_file(filename, content, folder_id?, mime_type?)` → triggers `"Felix Drive Upload"`
  - `sheets_read_range(spreadsheet_id, range)` → triggers `"Felix Sheets Read"`
  - `sheets_write_range(spreadsheet_id, range, data)` → triggers `"Felix Sheets Write"`
  - `n8n_plugin` injectable (default: built from `fetch_fn`/`base_url`/`api_key` args)
  - Returns `ToolResult(is_error=True)` on missing required args, unknown tool, or n8n failure
  - `create(n8n_plugin?, fetch_fn?, base_url?, api_key?)` factory for orchestrator auto-registration
- `cerebral/tests/test_plugin_google_workspace.py` — 32 unit tests (TDD, all cycles)

**Intentionally omitted (growth loop candidates):** Docs, Slides, Contacts, Maps, Tasks — same delegation
pattern, add workflows in n8n + call `call_tool` with a new workflow name.

**n8n setup required:** Create these 8 workflows in n8n at `localhost:5678` using the Google OAuth credential
configured in issue #19. Workflow names must match exactly:
`Felix Gmail Send`, `Felix Gmail Search`, `Felix Calendar Create`, `Felix Calendar List`,
`Felix Drive List Files`, `Felix Drive Upload`, `Felix Sheets Read`, `Felix Sheets Write`.

**Plugin tool count:** 11 plugins → 40 tools total

**Python test count: 319 passing (was 287), 3 skipped**
**JS test count: 50 passing (unchanged)**

**Demo path:** "Felix, what emails do I have from John this week?"
→ LLM calls `gmail_search(query="from:john newer_than:7d")`
→ `GoogleWorkspacePlugin` triggers `"Felix Gmail Search"` via n8n
→ n8n queries Gmail API, returns message list
→ LLM summarises → Kokoro speaks

---

### Issue #17 — Browser MCP ✅

One new plugin in `plugins/`, auto-loading via `discover_plugins()`. No changes to `main.py` required.

- `plugins/browser.py` — **Browser**: routes via OpenClaw's bundled Playwright + Readability + PDF.js
  - `web_search(query, max_results=5)` → `{results: [{title, url, snippet}]}` — POSTs to `http://localhost:3000/browser/search`
  - `navigate(url)` → `{url, content}` — headless Playwright + Readability; POSTs to `.../browser/navigate`
  - `read_pdf(url)` → `{url, text}` — PDF.js extraction; POSTs to `.../browser/pdf`
  - `fetch_fn` injectable — default uses `aiohttp` with `httpx` fallback; tests use sync stubs without live OpenClaw
  - Returns `ToolResult(is_error=True)` on missing required args or any network/HTTP exception
- `cerebral/tests/test_plugins_browser.py` — 25 unit tests (TDD vertical slices); all 6 acceptance criteria exercised

**Plugin tool count:** 10 plugins → 32 tools total

**Python test count: 255 passing (was 230), 3 skipped**
**JS test count: 50 passing (unchanged)**

**Demo path:** "Felix, what is the latest Python version?" → LLM calls `web_search` → `navigate` on top result → LLM summarises content → Kokoro speaks the answer

---

### Issue #16 — Time & Notes MCP ✅

Three plugins in `plugins/`, all auto-loading via `discover_plugins()`. No changes to `main.py` required.

- `plugins/clock.py` (extended) — new tools on the existing `ClockPlugin`:
  - `set_timer(seconds, label)` — schedules an asyncio background task; fires `notify_fn(title, message)` when it expires; returns `{id, label, seconds}`
  - `set_reminder(text, delay_seconds)` — alias path through `_set_timer`; same notification pattern
  - `list_timers()` — returns all active (not-yet-fired) timer entries; done timers self-remove from the dict
  - `cancel_all()` — cancels all pending tasks; called in tests and on shutdown
  - Injectable `notify_fn(title, message)` — defaults to `logging.info`; wired to Cerebral IPC / OS notification in production
  - Stubs gracefully when no running event loop (e.g. import time)
  - `get_time` and `set_alarm` tools unchanged
- `plugins/scheduler.py` — **Scheduler**: SQLite-backed, same `openmind.db`
  - `create_event(title, start_iso, end_iso=None, recurrence=None)` → `{id, title}`
  - `list_events(from_iso=None, to_iso=None)` → `{events:[...]}` — ISO string comparison for range filter
  - `update_event(id, **fields)` — updates any of `title`, `start_iso`, `end_iso`, `recurrence`; error on unknown id
  - `delete_event(id)` — error on unknown id
  - Recurrence values: `"daily"`, `"weekly"`, `"monthly"` or null; validated on create
  - Table: `events (id, title, start_iso, end_iso, recurrence, created_at)`
- `plugins/notes.py` — **Notes**: markdown files + SQLite index
  - `create_note(title, body)` — writes `{id:06d}_{safe_title}.md` to `notes_dir`; frontmatter: `id`, `title`, `created_at`; indexes title+body in SQLite
  - `search_notes(query)` — case-insensitive `LIKE` on title and body columns
  - `list_recent(n=10)` — ordered by `created_at DESC LIMIT n`
  - `delete_note(id)` — removes `.md` file and index row; error on unknown id
  - `notes_dir` defaults to `cerebral/data/notes/`; injectable for tests
  - Table: `notes (id, title, body, filename, created_at)`
- `cerebral/tests/test_plugins_time_notes.py` — 34 unit tests (TDD); all side effects injected

**Plugin tool count:** 9 plugins → 29 tools total (clock gains 3)

**Python test count: 230 passing (was 196), 3 skipped**
**JS test count: 50 passing (unchanged)**

**Demo path:** "Felix, remind me in 30 minutes to check the build" → `set_reminder("check the build", 1800)` → asyncio task queued → fires `notify_fn("Felix — Timer", "check the build is done")` → Cerebral emits notification via existing `NotificationManager` path

---

### Issue #14 — Environmental context ✅
- `cerebral/environment/context.py` — `EnvironmentContext(http_get, capture_fn, infer_fn, interval_seconds)` — all deps injected
  - `async refresh_location()` — IP geolocation via `ip-api.com`; stores city, country, lat, lon in RAM; graceful on network failure
  - `enable_camera(interval_seconds=30)` / `disable_camera()` — opt-in toggle; starts/cancels async `_capture_loop` task
  - `_capture_loop()` — calls `capture_fn()` → `infer_fn(frame)` → stores scene string in RAM; cancellable
  - `get_context()` — `{city, country, lat, lon, scene, camera_enabled}` snapshot
  - `camera_enabled` property
  - RAM-only — no disk writes for frames or coordinates
- `cerebral/passive/extractor.py` updated:
  - `CandidateAction` — new `context: dict` field (default `{}`)
  - `FiveW1HExtractor.extract(transcript, env_context=None)` — attaches `env_context` to returned action when provided
- `cerebral/main.py` updated:
  - `_env = EnvironmentContext()` at startup
  - `await _env.refresh_location()` called in `main()` after plugin discovery
  - `_on_passive()` passes `env_context=_env.get_context()` to `extract()`
  - `_env_context_event()` helper — `{"type": "env_context_update", "data": {"context": ...}}`
  - New IPC handlers: `set_camera_enabled {enabled}`, `get_env_context`
  - Heartbeat includes `env` field (city or `"unknown"`)
  - Connection greeting includes `env_context_update`
- `tray/main.js` updated:
  - `envContext` state variable
  - `camera_enabled: false` added to `SettingsStore` defaults
  - `env_context_update` event → updates `envContext`, calls `refreshMenu()`
  - On connect: syncs persisted `camera_enabled` setting to Cerebral via `set_camera_enabled`
  - Tray menu: "Camera: On/Off" toggle (after Notifications, before separator)
- `cerebral/tests/test_environment.py` — 25 unit tests covering all acceptance criteria

**Python test count: 153 passing (was 128), 3 skipped**
**JS test count: 50 passing (unchanged)**

**New IPC messages:**

| direction | type | data | meaning |
|---|---|---|---|
| Tray → Cerebral | `set_camera_enabled` | `{enabled}` | toggle camera on/off |
| Tray → Cerebral | `get_env_context` | — | request current env context |
| Cerebral → Tray | `env_context_update` | `{context: {city, country, lat, lon, scene, camera_enabled}}` | env state snapshot |

---

### Issue #13 — Insights engine ✅
- `cerebral/insights/engine.py` — `InsightsEngine(profile_id, db_path)` + `Insight` dataclass
  - `record_signal(action, title, tool_name=None)` — records every approve/dismiss with timestamp
  - `maybe_create_insight(title, tool_name=None)` → `Insight | None` — creates at `PATTERN_THRESHOLD` (default 3), no duplicates per pattern key
  - Pattern key: `tool_name` if set, else normalised `title.strip().lower()`
  - `list_insights()` → `list[Insight]` — per-profile, ordered by `created_at`
  - `delete_insight(id)` → `bool`; `pin_insight(id)` → `bool` (toggles); `edit_insight(id, description)` → `bool`
  - `Insight.to_dict()` — IPC-safe: `id, profile_id, description, example, pinned, created_at, updated_at`
  - Two SQLite tables: `insight_signals` + `insights` (per shared `openmind.db`)
- `cerebral/action_queue/manager.py` — `get_item(id)` added (exposes existing `_fetch` as public)
- `cerebral/main.py` updated:
  - `_get_insights()` helper returns `InsightsEngine` for active profile
  - `_insights_update_event()` broadcast helper
  - `approve_item` → `record_signal("approve")` + `maybe_create_insight` → broadcast if new insight
  - `dismiss_item` → `get_item` first → `record_signal("dismiss")` → `maybe_create_insight`
  - New IPC handlers: `list_insights`, `delete_insight`, `pin_insight`, `edit_insight`
  - New connection greeting includes `insights_update`
- `tray/windows/insights.html` — dark themed Insights view (teal orb, matches palette)
  - Cards: description + example trigger badge + Pin / Edit / Delete controls
  - Edit: inline input replaces description text; Save confirms
  - Empty state: "No insights yet"
- `tray/main.js` updated:
  - `insightsWindow` singleton + `insightsList` state
  - `openInsightsWindow()` — 360×500, dark, no menu bar
  - IPC handlers: `insights:request`, `insights:pin`, `insights:edit`, `insights:delete`
  - `insights_update` Cerebral event → forwards to open insights window
  - Tray menu: "Insights" item (between Visualiser and Notifications)
- `cerebral/tests/test_insights.py` — 26 unit tests covering all acceptance criteria

**Python test count: 128 passing (was 102), 3 skipped**
**JS test count: 50 passing (unchanged)**

**New IPC messages:**

| direction | type | data | meaning |
|---|---|---|---|
| Tray → Cerebral | `list_insights` | — | request current insights |
| Tray → Cerebral | `delete_insight` | `{insight_id}` | remove insight |
| Tray → Cerebral | `pin_insight` | `{insight_id}` | toggle pin |
| Tray → Cerebral | `edit_insight` | `{insight_id, description}` | update description |
| Cerebral → Tray | `insights_update` | `{insights:[...]}` | full list after any change |
| Cerebral → Tray | `insight_deleted` | `{id, ok}` | ack after delete |

---

### Issue #12 — Memory manager ✅
- `cerebral/memory/manager.py` — `MemoryManager(profile_id, db_path, chroma_client, chroma_path)` + `Memory` dataclass
  - `await remember(fact)` → `str` memory_id — stores in ChromaDB collection `profile_{id}`
  - `await recall(query, n_results=5)` → `list[Memory]` — semantic similarity search; empty list when nothing stored
  - `await forget(memory_id)` → `bool` — True if found+deleted, False if unknown id
  - `set_preference(key, value)` / `get_preference(key, default="")` / `list_preferences()` — SQLite, per-profile
  - Per-profile isolation: each profile gets its own ChromaDB collection; SQL uses `(profile_id, key)` PK
  - `chroma_client` injection for tests; production uses `PersistentClient(chroma_path)`
- `cerebral/main.py` updated:
  - `_get_memory()` helper returns `MemoryManager` for active profile
  - IPC handlers: `remember {fact}` → `memory_stored`; `recall {query}` → `memory_results`; `forget {memory_id}` → `memory_forgotten`
- `cerebral/tests/test_memory.py` — 22 unit tests; uses `PersistentClient(tmp_path)` fixture for true per-test isolation
- **Note:** `cerebral/queue/` renamed → `cerebral/action_queue/` — avoids shadowing Python stdlib `queue` module (triggered by chromadb's opentelemetry dep)
- `requirements.txt` — added `chromadb>=1.5.0`
- ChromaDB downloads ONNX embedding model (~79 MB) on first use to `~/.cache/chroma/`

**Python test count: 102 passing (was 80), 3 skipped**

**New IPC messages:**

| direction | type | data | meaning |
|---|---|---|---|
| Tray → Cerebral | `remember` | `{fact}` | store a fact for active profile |
| Tray → Cerebral | `recall` | `{query}` | semantic search active profile memories |
| Tray → Cerebral | `forget` | `{memory_id}` | remove a specific memory |
| Cerebral → Tray | `memory_stored` | `{id, fact}` | ack after remember |
| Cerebral → Tray | `memory_results` | `{memories:[{id,fact,distance}]}` | recall results |
| Cerebral → Tray | `memory_forgotten` | `{id, ok}` | ack after forget |

---

### Issue #11 — Passive 5W1H ✅
- `cerebral/passive/extractor.py` — `FiveW1HExtractor(router)` + `CandidateAction` dataclass
  - `extract(transcript)` → `CandidateAction | None`
  - Builds a structured prompt asking the LLM for JSON with title, summary, 5W1H fields dict, confidence
  - Returns `None` for empty transcript (no LLM call), unparseable response, or confidence < 0.5
  - `CONFIDENCE_THRESHOLD = 0.5`; LLM exceptions are caught and logged, return `None`
  - Strips markdown code fences from LLM response before parsing
- `cerebral/audio/pipeline.py` extended:
  - `AudioPipeline(on_wake, on_passive=None, signal_words=None)` — new optional params
  - `DEFAULT_SIGNAL_WORDS` — 11 configurable trigger words (remind, meeting, call, schedule, …)
  - `signal_words` defaults to `[]` at construction; populated from `DEFAULT_SIGNAL_WORDS` at `start()`
  - `_matches_signal_word(text)` — case-insensitive substring check
  - `_on_signal_word_detected()` — snapshots buffer, clears it, starts `_passive_transcribe_emit` thread
  - `_on_signal_detected(transcript_hint)` — async testable entry point (patch `_transcribe` in tests)
  - `_passive_transcribe_emit` — thread: transcribe snapshot → run_coroutine_threadsafe `on_passive`
  - Rolling buffer is cleared immediately on signal detection (before transcription)
  - Wake word path unchanged; signal words only fire when not already in `_passive_active` state
- `cerebral/main.py` updated:
  - Imports `FiveW1HExtractor`, `DEFAULT_SIGNAL_WORDS`
  - `_extractor = FiveW1HExtractor(_router)` at module level
  - `_on_passive(transcript)` async callback: calls `_extractor.extract()` → `_queue.add_item()` → `_broadcast(queue_update)`
  - `AudioPipeline` constructed with `on_passive=_on_passive, signal_words=list(DEFAULT_SIGNAL_WORDS)`
- `cerebral/tests/test_extractor.py` — 17 unit tests (all acceptance criteria)
- `cerebral/tests/test_passive_pipeline.py` — 11 unit tests

**Python test count: 80 passing (was 52), 3 skipped**

**New IPC messages:** none — passive extraction flows into the existing `queue_update` broadcast

---

### Issue #30 — Plugin builder ✅

The growth loop in code. A meta-plugin at `plugins/builder.py` exposes three
`builder_*` tools that let Felix generate, smoke-test, and register new MCP
plugins at runtime from a natural-language description. Every side effect
(LLM call, `pip install`, smoke runner) is injected so the entire flow runs
hermetically in tests — no real network, no real subprocess, no real
filesystem outside `tmp_path`.

- `plugins/builder.py` — **BuilderPlugin**: 3 tools — `builder_create(
  description, name?)`, `builder_list_generated()`, `builder_smoke_test(
  name, tool_name?, args?)`.
  - **Generation flow**: validate name (`^[a-z][a-z0-9_]*$`) → reject if
    `plugins/<name>.py` or `plugins/<name>/` already exists → static-scan
    the generated source → check pip deps against the allowlist → run pip
    installs → stage `server.py` + `README.md` to a `tempfile.
    TemporaryDirectory()` → in-process import → call `smoke_runner_fn(
    plugin, smoke_tool, smoke_args)` → on pass, `shutil.move()` into
    `plugins/<name>/` and `orc.register(plugin)`; on fail, surface the
    error and let the temp dir auto-clean. Failed builds leave nothing
    behind in `plugins/`.
  - **Static guardrails (load-bearing)**: rejects code without `PLUGIN_NAME`
    or without a `def create(...)` factory; refuses `os.system`, raw
    `subprocess.{Popen,run,call,check_output,check_call}`, `os.popen`,
    `from os import system`, `__import__('os')`, top-level `exec(`,
    `eval(`, and raw `open(..., 'w'`. This is a backstop, not a sandbox —
    the generated code still runs in-process during smoke, so the model is
    the primary trust boundary.
  - **pip allowlist**: the constructor takes `pip_allowlist=(...)`; deps
    not in the allowlist are rejected before any install attempt (no
    `pip install` is ever called speculatively). Version pins are
    permitted — `requests==2.31.0` matches an allowlist entry of
    `requests`. `main.py` wires a tight default of
    `("requests","httpx","aiohttp","beautifulsoup4","lxml")`.
  - **Survival across restarts** is delivered by extending
    `MCPOrchestrator.discover_plugins` to load both `plugins/<name>.py`
    (flat, original) and `plugins/<name>/server.py` (subdir, used by the
    builder). Subdirs starting with `_` or `.` are skipped. Empty
    subdirs are silently ignored.
- `plugins/builder.py::create()` returns a parked `_ParkedBuilderPlugin`
  during auto-discovery (no orchestrator handle yet). `cerebral/main.py`
  calls `_attach_builder_plugin()` immediately after `discover_plugins`,
  which finds the parked instance, hands it the live orchestrator, the
  pip allowlist, and the LLM hook (currently a `NotImplementedError` stub
  until #6's structured-output path is wired). Until then, `builder_create`
  surfaces a clear error rather than guessing.

**Tool naming:** every tool is prefixed with `builder_` per the flat-global
namespace convention from `.learnings/LEARNINGS.md` (#23).

**Tests:** one file, all side effects injected.
- `cerebral/tests/test_plugin_builder.py` — 35 unit tests across 8 cycles:
  happy path (5), name validation incl. path-traversal (10 parametrised),
  smoke failure cleanup (3), pip allowlist + version pins (3), code
  guardrails incl. 5 dangerous patterns (8), `builder_list_generated` (2),
  tool-list shape (2), and orchestrator subdir discovery (2). Smoke runner
  is mocked async; `pip_install_fn` is mocked to record calls; LLM
  fixture returns a canned `WeatherbugPlugin` payload that exposes a
  zero-arg `weatherbug_ping` smoke tool.
- `cerebral/tests/test_orchestrator.py` — unchanged, still 20 passing
  (subdir discovery has its own coverage in the builder file).

**Plugin tool count:** 28 plugins → 96 tools total
(+1 builder plugin = +3 tools over master). Once #27 + #28 land:
30 plugins / 102 tools.

**Python test count: 699 passing (was 664 on master), 3 skipped**

**Demo paths:**
- "Felix, I need you to be able to look up Wikipedia summaries." → LLM
  router calls `builder_create({description: "..."})` → builder asks the
  LLM for `{server_py, readme_md, pip_deps:["requests"], smoke_tool:
  "wiki_summary", smoke_args:{title:"OpenMind"}}` → static scan passes →
  `pip install requests` → smoke `wiki_summary({title:"OpenMind"})` →
  pass → registered → `orc.list_tools()` now includes `wiki_summary` and
  the user's next sentence "summarise the OpenMind page" routes through
  the new tool, all in the same session.
- After restart, `discover_plugins(plugins/)` walks `plugins/wiki/server.py`
  and re-registers the plugin without builder involvement.
- "Felix, what plugins did you build for me?" → LLM calls
  `builder_list_generated({})` → `{"generated": ["wiki", "weatherbug"]}` →
  Kokoro speaks the names back.

**External deps:** none new. The builder uses only stdlib (`tempfile`,
`shutil`, `importlib`, `re`, `subprocess` for the default `pip install`
shell-out, which tests bypass entirely).

**Trust model docstring** in `plugins/builder.py` makes explicit that the
in-process smoke is *not* sandboxed: the static scan + pip allowlist are
backstops; the model itself remains the primary trust boundary. A future
hardening pass could move smoke into a subprocess.

---

## Key constraints to carry forward

- **Local first** — every feature must work offline. Cloud (Claude, Google) is enhancement, not requirement.
- **No disk writes for audio** — the rolling buffer is RAM-only. Never change this.
- **IPC is the only channel** — Cerebral and tray talk only through the WebSocket bridge. No shared files, no direct imports across the boundary.
- **Audio pipeline is optional at start** — Cerebral runs cleanly without the Vosk model. Keep this graceful fallback.
- **TTS is optional at start** — Cerebral runs cleanly without kokoro. Same graceful pattern.
- **Wake name is user-configurable** — don't hardcode "felix" in new code. Always read from the active profile.
- **Profile schema has migrations** — the `_init_schema` method uses `ALTER TABLE ... ADD COLUMN` with `try/except` to add new columns to existing DBs. Follow this pattern for any new columns.
- **Voice ID default is `af_heart`** — the Kokoro American English female "Heart" voice. Profile records created before #5 may have `voice_id="default"` — the TTS engine falls back to `af_heart` if the id is unrecognised.
- **Local LLM auto-detect (#37)** — there is no hardcoded `gemma4` default any more. `OllamaBackend.list_installed_models()` queries `GET http://localhost:11434/api/tags` (with a `tags_fetch_fn` injection point for tests). The router builds `ollama/<name>` entries for whatever Ollama actually has installed, falls back to cloud when Ollama is offline, and persists the user's last choice on the active profile via `profiles.active_model`.

---

### Issue #37 — Persistent model selection + Ollama refresh ✅

**What changed:**
- `cerebral/llm/router.py` — `OllamaBackend.list_installed_models(tags_fetch_fn)` discovers installed Ollama models. `_real_backends()` no longer hardcodes `ollama/gemma4`; it builds `ollama/<name>` entries from the live tags response and adds the fixed cloud entries. `ModelRouter.refresh_local_backends(tags_fetch_fn)` re-queries on demand, preserving cloud entries and reassigning `active_model` if the previous active was uninstalled. The default model is auto-picked: first `ollama/*` if any, else first cloud.
- `cerebral/db/profiles.py` — added `active_model TEXT` column with the existing `ALTER TABLE` migration pattern; `update_active_model(profile_id, model_id)` setter; `Profile.active_model` field; `_row_to_profile` reads it defensively.
- `cerebral/main.py` — at startup, restores `_router.switch_model(_active_profile.active_model)` if the saved id is still in backends (warns and stays on the auto-picked default if not). On every `switch_model` IPC, the new id is persisted to the active profile. New `refresh_models` IPC handler calls `refresh_local_backends()` and re-broadcasts `models_list`.
- `tray/lib/model-menu.js` — added `onRefresh` opt → "Refresh installed models" entry at the bottom (outside any radio group). Adds an "Ollama offline — local models unavailable" disabled note when no local models are present.
- `tray/main.js` — passes `onRefresh: () => sendToCerebral({ type: 'refresh_models' })` to the submenu builder.

**New IPC messages:** `refresh_models` (tray → cerebral). The reply is the existing `models_list` broadcast.

**New tests:**
- `cerebral/tests/test_router.py` — slices 7–9: `list_installed_models` (happy/offline/empty/malformed), default-picker (first ollama / first cloud / no backends / explicit honored), `refresh_local_backends` (adds/drops/reassigns active/keeps active).
- `cerebral/tests/test_model_persistence.py` (new file, 6 tests) — defaults to empty, round-trip, overwrite, manager-restart persistence, legacy-DB migration, full-update preserves the column.
- `tray/tests/model-menu.test.js` — refresh entry visibility/click/position, Ollama-offline indicator on/off.

**Test count after #37:** 695 Python tests passing (3 integration skipped) + 75 JS tests passing.

---

### Issue #43 — capability vocabulary + orchestrator gate skeleton ✅

1. `cerebral/security/gate.py` — closed 16-class `Capability` `Enum` (exact ADR-0005 names), `Decision` (`SILENT`/`ASK`/`DENY`), frozen `CallFlags(passive=False, irreversible=False)`, immutable `DEFAULT_POLICY` (`MappingProxyType`) covering every class, and `CapabilityGate.check(capability, flags) → Decision`.
2. `cerebral/security/__init__.py` — re-exports the public surface (`Capability`, `CallFlags`, `CapabilityGate`, `DEFAULT_POLICY`, `Decision`).
3. `cerebral/mcp/orchestrator.py` — `MCPOrchestrator.__init__` takes an optional injected `gate` (default-constructs one). `call_tool(name, args, capability=None, flags=None)` runs `gate.check` before plugin dispatch when a capability is supplied; non-`SILENT` short-circuits with an error `ToolResult` and the plugin is never invoked. Pre-#44 call sites pass no capability and proceed unchanged.
4. **Escalation rule** — `passive=True` moves one notch: `SILENT → ASK`, `ASK → DENY`, `DENY → DENY` (terminal).
5. **Fail-closed in this slice** — `ASK` resolves to `DENY` at the orchestrator. The gate itself returns the verbatim policy verdict; the consent surface that lets `ASK` reach the user lands in #48, and per-profile ACL lands in #45.
6. **`irreversible` is representable but inert** — accepted on `CallFlags`, no decision change in #43. The modal that consumes it lands in #49.
7. **Closed-vocabulary enforcement** — `CapabilityGate.check` rejects non-`Capability` args with `TypeError`; the constructor rejects partial policies with `ValueError` so a new ADR-added class can't silently fall through.
8. **Why the gate stays a pure lookup** — `gate.check` returns SILENT/ASK/DENY verbatim. The orchestrator (today) and future resolvers (#45 ACL, #48 consent surface) layer on top without re-architecting the gate.
9. **New tests:**
   - `cerebral/tests/test_capability_gate.py` — 48 unit tests across 6 slices: closed-vocabulary shape, day-1 defaults parametrised over every class, passive escalation across every class, irreversible-is-inert, `CallFlags` frozenness/defaults, gate-side type and policy-completeness errors.
   - `cerebral/tests/test_orchestrator.py` — +8 integration tests on the call path: silent dispatches, ask denies fail-closed, deny blocks, `passive=True` escalates, plugin never invoked when blocked, no-capability calls behave as before, unknown-tool short-circuit precedes the gate.
10. **Test count after #43:** 872 Python tests passing (3 integration skipped) + 75 JS tests passing.
11. **What this slice intentionally leaves to follow-up issues** — `REQUIRED_CAPABILITIES` declaration + registration enforcement (#44), per-profile ACL + `profile_acl` table (#45), consent surface that lets `ASK` reach the user (#48), modal consumer of `irreversible` (#49), voice consent grammar (#50), queue admission verb-heuristic that flips `passive=True` on queued items (#52), Permissions settings UI (#53).

---

### Issue #44 — declare REQUIRED_CAPABILITIES + registration enforcement ✅

1. Every plugin module under `plugins/` declares `REQUIRED_CAPABILITIES: frozenset[str]` with the minimum capability classes (ADR-0005 16-class vocab) its tools intend. 32 plugins, all declarations stored as frozensets of value-strings (not enum members) so plugin modules never need to import `cerebral.security`.
2. `cerebral/security/__init__.py` — exposes `CAPABILITY_VOCABULARY: frozenset[str]` (the canonical string-form view of the `Capability` enum). Plugins validate against this set; the orchestrator and the builder validate against the same set.
3. `cerebral/mcp/orchestrator.py` — new `PluginRegistrationError(plugin_name, reason, detail)` with stable reason codes (`REASON_MISSING`, `REASON_INVALID_TYPE`, `REASON_UNKNOWN_CAPABILITY`, `REASON_CREATE_FAILED`, `REASON_LOAD_FAILED`). `_validate_required_capabilities()` returns the error (does not raise) so `discover_plugins` can record it on `registration_errors` without a try/except dance.
4. `MCPOrchestrator.register(plugin, *, required_capabilities=None)` gains an optional kwarg. When provided, the orchestrator validates and stores the declaration on `self._plugin_capabilities`. When omitted (legacy / test path), behaviour matches pre-#44. `discover_plugins` always reads from the module; the builder always passes the validated payload value.
5. `discover_plugins` validates the constant **before** calling `module.create()` — `create()` may have side effects (SQLite file creation in notes/scheduler, etc.), and a malformed plugin must not leak them. Tests cover that the factory is never invoked when the declaration is missing.
6. `MCPOrchestrator.registration_errors` is a read-only copy of structured refusal records `{plugin_name, reason, detail, path}`. `required_capabilities_for(name)` returns the declared set or `None`. `unregister(name)` clears the capability record.
7. **Builder migration** — `plugins/builder.py` requires `payload["required_capabilities"]: list[str]`, validates against `CAPABILITY_VOCABULARY` (rejects unknown / non-iterable / non-string), prepends a deterministic `REQUIRED_CAPABILITIES = frozenset({...})` line to generated `server.py` when the LLM omitted it (guarantees the plugin survives a Cerebral restart), and passes the validated frozenset through `orc.register(plugin, required_capabilities=...)`. The static scan in `_scan_generated_code` now also requires `REQUIRED_CAPABILITIES` to appear in the source.
8. `cerebral/main.py` — new `_plugins_list_event()` builds `{plugins: [{name, required_capabilities}], errors: [...]}`. Sent on every new tray connection alongside the other state broadcasts; on-demand via the new `list_plugins` IPC message. Refused plugins also logged at startup via `[cerebral] Plugin refused: ...`.
9. **Capability map per plugin** (intent-level, not implementation primitives — wrapped subprocess calls map to their semantic class; #47's AST check will tighten the call-site mapping):
   - `apps`, `clock`, `docker`, `printer` → `device_control`
   - `clipboard` → `clipboard`
   - `bitwarden` → `vault_unlock`, `secrets_read`
   - `browser` → `external_data_read`, `network_egress_local`
   - `files`, `notes` → `fs_read`, `fs_write`, `fs_delete`
   - `scheduler` → `fs_read`, `fs_write` (SQLite rows, not files)
   - `finance` → `fs_read`, `external_data_write`
   - `git` → `fs_read`, `fs_write`, `network_egress_cloud`
   - `github`, `google_workspace`, `meet`, `zoom` → `external_data_read`, `external_data_write`, `network_egress_local` (+ `device_control` for meet/zoom)
   - `google_workspace_fallback` → adds `network_egress_cloud` to the above
   - `http_client` → `external_data_read`, `external_data_write`, `network_egress_cloud`
   - `markets`, `news`, `weather`, `wikipedia` → `external_data_read`, `network_egress_cloud`
   - `n8n` → `network_egress_local`
   - `network_scanner` → `network_recon`
   - `package_manager` → `code_install`, `network_egress_cloud`
   - `phone` → `external_data_write`, `network_egress_local`
   - `shell` → `shell_exec`
   - `ssh` → `shell_exec`, `network_egress_cloud`
   - `steam` → `fs_read`, `device_control`
   - `system` → `device_control`, `screen_capture`
   - `vpn` → `network_config`, `network_egress_cloud`
   - `builder` → `code_install`
10. **New tests:**
    - `cerebral/tests/test_orchestrator.py` — Slice 9 (registration enforcement): missing constant / wrong type / unknown class / non-str values / empty frozenset / `create()` failure / partial refusal across mixed dirs / `register()` validation raises and does not add the plugin / `register()` without kwarg behaves as pre-#44 / `unregister()` clears the cap record / `registration_errors` returns a copy / `create()` never invoked when declaration missing. Slice 10 (real-plugin audit): parametrized over every file in `plugins/`, asserts declaration exists, is `frozenset[str]`, and only contains vocab classes (32/32).
    - `cerebral/tests/test_plugin_builder.py` — Cycle 9: missing `required_capabilities` rejected; unknown class string rejected; non-iterable payload rejected; LLM omits constant → builder injects it into staged `server.py`; orchestrator's `required_capabilities_for(name)` reflects what the builder declared.
11. **Test count after #44:** 923 Python tests passing (3 integration skipped) + 75 JS tests passing.
12. **What this slice intentionally leaves to follow-up issues** — per-profile ACL + `profile_acl` table (#45), static-pattern inspectability scan + `plugins/_trusted/` escape hatch (#46), AST-completeness check that maps call sites to capability classes (#47), tray UI that renders the `plugins_list` broadcast (lands alongside #53).

---

### Issue #45 — per-profile ACL with profile_acl table and resolution ✅

1. `cerebral/db/profiles.py` — new `profile_acl` table `(profile_id, scope ∈ {class,tool}, target, policy ∈ {silent,ask,deny}, granted_at)` with `PRIMARY KEY (profile_id, scope, target)` and `FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE`. New `acl_defaults_snapshot TEXT NOT NULL DEFAULT '{}'` column on `profiles`, added via the existing `ALTER TABLE … ADD COLUMN` try/except migration loop. `Profile` gains an `acl_defaults_snapshot: dict` field; `_row_to_profile` reads it defensively (legacy rows get `{}`).
2. `ProfileManager` gains: `set_acl_grant(profile_id, *, scope, target, policy)` (upsert via `ON CONFLICT(profile_id, scope, target) DO UPDATE`), `revoke_acl_grant(...) → bool`, `get_acl_grant(...) → str | None`, `list_acl_grants(profile_id) → list[dict]`. Scope/policy validation happens at the helper *and* SQL CHECK level so direct DB writes can't corrupt the table.
3. `cerebral/security/acl.py` — new `ProfileACL(profile_id, profile_manager, defaults_snapshot)` implements the 5-step resolution order from the issue body:
   1. per-tool override (SQLite, scope='tool')
   2. persistent class grant (SQLite, scope='class')
   3. session class grant (RAM, dict on the instance)
   4. once class grant (RAM, FIFO queue per class — consumed on use)
   5. profile's frozen `DEFAULT_POLICY` snapshot (with live `DEFAULT_POLICY` as a legacy fallback when the column is empty).
4. **Passive escalation runs after ACL resolution.** A SILENT grant at *any* layer (per-tool override, persistent class, session) is escalated to ASK when the call is queue-originated. This is the explicit ambient-actuation defeat from the ADR — there is a dedicated regression test per layer.
5. `ProfileACL` exposes the programmatic API the consent surface (#48) and Permissions UI (#53) will use: `grant_once(cap, decision)`, `grant_session(cap, decision)`, `revoke_session(cap)`, `set_persistent_class(cap, decision)`, `revoke_persistent_class(cap)`, `set_tool_override(tool, decision)`, `revoke_tool_override(tool)`, `list_persistent_grants()`, `clear_transient()`.
6. `cerebral/mcp/orchestrator.py` — new `acl: ProfileACL | None` constructor kwarg and `set_acl(acl)` setter. `call_tool` resolves through `self._acl.resolve(...)` when set, otherwise through `self._gate.check(...)`. The gate is unchanged — pure lookup, no I/O, no consent. The ACL composes on top, doesn't replace.
7. `cerebral/main.py` — at startup, builds a `ProfileACL` from `_active_profile.acl_defaults_snapshot` and wires it via `_orc.set_acl(...)`. On `create_profile` / `switch_profile` / `delete_profile` IPC messages, the ACL is rebuilt (which is what clears once+session grants — by virtue of a new instance) or set to `None` if no profile remains. Cerebral restart clears RAM grants automatically because the ACL is built fresh.
8. **Snapshot semantics** — `ProfileManager.create()` captures `DEFAULT_POLICY` at the moment of profile creation and stores it as JSON in `acl_defaults_snapshot`. Subsequent changes to the system defaults do not propagate to existing profiles. New `create()` callers can pass `acl_defaults_snapshot=...` to override (used by tests). Tests assert (a) every class is in the snapshot, (b) the snapshot persists verbatim on the row, (c) a fresh profile with explicit override defaults doesn't disturb the original.
9. **ASK still resolves to DENY at the orchestrator** in this slice (consent surface is #48). The ACL returns the verbatim decision; the orchestrator continues to fail-closed on non-SILENT until the surface lands.
10. **New tests:**
    - `cerebral/tests/test_profile_acl.py` (new file, 55 tests) — 10 slices: snapshot inheritance + non-propagation + legacy fallback / parametrised fall-through to default over every class / per-tool override beats class default + persistent class + revoke path / persistent class grant beats default + persists across instances + revoke path / session grant beats default + loses to persistent + loses to tool override + does not persist + revoke / once grant consumed in FIFO order + loses to persistent + does not persist / passive escalation defeats default + persistent + session + per-tool + stays at DENY / `clear_transient()` drops RAM but keeps SQLite / type validation / `ProfileManager` CRUD edge cases + cross-profile isolation + FK CASCADE on profile delete.
    - `cerebral/tests/test_orchestrator.py` — Slice 11 (6 tests): ACL is consulted on the call path / persistent DENY blocks SILENT default / once-grant consumed after first call / passive escalation defeats persistent SILENT / `set_acl` swaps the resolver / no-ACL falls back to gate (backward compat for pre-#45 tests).
11. **Test count after #45:** 984 Python tests passing (3 integration skipped) + 75 JS tests passing.
12. **What this slice intentionally leaves to follow-up issues** — consent surface that converts ACL ASK decisions into a tray prompt with Once/Session/Persistent/Deny buttons that call into `grant_*` (#48), modal for `irreversible`-flagged calls (#49), voice consent grammar via Vosk (#50), Permissions settings UI that reads `list_persistent_grants()` and writes via `set_persistent_class` / `set_tool_override` (#53).

---

### Issue #46 — static-pattern inspectability scan + plugins/_trusted/ escape hatch ✅

1. `cerebral/security/inspectability.py` (new module) — canonical home for `FORBIDDEN_PATTERNS`, `scan_source(source) → InspectabilityIssue | None`, and `classify_path(path, plugins_dir) → (mark, issue)`. Marks are the string constants `INSPECTED = "inspected"` and `TRUSTED = "trusted"` so the tray payload carries verbatim values without a JSON-side enum table. Refusal reason codes match the orchestrator's `REASON_*` style: `REASON_FORBIDDEN_PATTERN`, `REASON_NOT_INSPECTABLE_PATH`, `REASON_NON_TEXT`. Re-exported from `cerebral.security.__init__` so the builder and the orchestrator both pull the canonical list from the same place.
2. **Pattern list is a strict superset of the pre-#46 builder's 8 entries.** Kept verbatim: `os.system`, `subprocess.{Popen,run,call,check_output,check_call}`, `os.popen`, `from os import system`, `__import__('os')`, `exec()`, `eval()`, raw `open("…", "w")`. Added: `from subprocess import …`, `__import__('subprocess')`, `compile(…, 'exec')`, `pickle.loads`, `marshal.loads`. Verified by `grep` and a parametrised real-plugin-audit test that **all 32 shipped plugins are clean** against the full list. Existing indirection patterns (e.g. `self._run_fn = run_fn or subprocess.run`) already keep the call form (`subprocess.run(`) out of plugin source — the scan accepts them and the runtime `code_install` / `shell_exec` capabilities are what really gate the action.
3. `plugins/builder.py` — `_FORBIDDEN_PATTERNS` deleted; `_scan_generated_code` now calls `scan_source()` from the canonical module. Structural checks (`PLUGIN_NAME`, `REQUIRED_CAPABILITIES`, `create()`) stay in the builder where they belong — they're builder-output schema, not orchestrator-side inspectability. Also: `_default_pip_install` now calls `subprocess.run` via a module-level alias (`_run_subprocess = subprocess.run`) so the builder's own source passes the canonical scan — same indirection convention shell.py, docker.py, and the other shell-touching plugins already used.
4. `cerebral/mcp/orchestrator.py` — `_load_plugin_file(path, *, inspectability=INSPECTED)` reads the source as UTF-8, runs the scan when `inspectability == INSPECTED`, and records the refusal **before** module exec. Side-effect protection regression test confirms a `Path(...).write_text(...)` at module top level never fires for a scan-refused plugin. UnicodeDecodeError on source read surfaces as `REASON_NON_TEXT`; other read errors fall through to `REASON_LOAD_FAILED`.
5. **`plugins/_trusted/` escape hatch.** `discover_plugins` now walks the `_trusted/` subtree explicitly via `_discover_trusted_subtree`. Plugins under `plugins/_trusted/<name>/server.py` skip the scan but still: (a) must declare `REQUIRED_CAPABILITIES` (test pins refusal otherwise), (b) gate at call time exactly like inspected plugins (regression test calls a trusted `shell_exec` tool and confirms the orchestrator denies it), (c) are recorded with `inspectability == TRUSTED` so the tray renders the red badge. Trusted plugins must use the subdir form (`plugins/_trusted/<name>.py` flat form refused with `NOT_INSPECTABLE_PATH`) — the user inspects a folder, not just a file.
6. **Non-conforming layouts now surface, not silently skip.** Pre-#46 a subdir in `plugins/` with no `server.py` was silently dropped from discovery. Post-#46 the orchestrator records `REASON_NOT_INSPECTABLE_PATH` so the tray can render *why* the plugin isn't there. Underscored / dotfile / `__pycache__` dirs other than `_trusted/` are still silently ignored — those are reserved scaffolding.
7. `MCPOrchestrator._plugin_inspectability: dict[str, str]` — per-plugin mark recorded inside `_load_plugin_file` after a successful `register()`. New accessor `inspectability_for(plugin_name) → str | None`. Plugins registered via the direct `register()` kwarg path (tests, the parked builder before main.py wires it up) bypass disk discovery and have no inspectability mark — `inspectability_for` returns `None` for them. `unregister()` clears the record.
8. `cerebral/main.py` — `_plugins_list_event()` adds `"inspectability"` to every registered plugin entry alongside `"required_capabilities"`. Stable shape (`"inspected"` / `"trusted"` / `null`) for the tray. The companion `errors` list already carries the `reason` code so the tray renders forbidden-pattern refusals next to the badge-bearing ones.
9. `cerebral/security/__init__.py` — re-exports the inspectability surface (`FORBIDDEN_PATTERNS`, `INSPECTED`, `TRUSTED`, `InspectabilityIssue`, `REASON_FORBIDDEN_PATTERN`, `REASON_NON_TEXT`, `REASON_NOT_INSPECTABLE_PATH`, `classify_plugin_path`, `scan_source`). `classify_path` is re-exported as `classify_plugin_path` to disambiguate when imported alongside other unrelated `classify_*` helpers in the codebase.
10. **New tests** — `cerebral/tests/test_plugin_inspectability.py` (new file, 81 tests across 11 slices):
    - Slice 1: `scan_source` clean source + every forbidden pattern parametrised + strict-superset-of-pre-#46-builder regression + negative lookbehind preserved (`obj.exec_command(...)` not flagged).
    - Slice 2: `classify_path` covers all three conforming layouts + non-conforming refusals (subdir wrong filename, `_trusted/<name>.py` flat-in-trusted, nested subdirs).
    - Slice 3: orchestrator refuses bad flat plugins with `REASON_FORBIDDEN_PATTERN`; scan fires before module exec (sentinel-file regression).
    - Slice 4: orchestrator refuses bad subdir plugins; clean subdir plugins load with `inspectability == INSPECTED`.
    - Slice 5: `_trusted/` subtree — same bad source loads in `_trusted/`, marked `TRUSTED`; still requires `REQUIRED_CAPABILITIES`; still gates at call time; subtree subdir with no `server.py` recorded as `NOT_INSPECTABLE_PATH`.
    - Slice 6: non-conforming subdirs recorded; underscored / dotfile dirs silently ignored; partial-refusal across mixed bag.
    - Slice 7: non-UTF-8 plugin source recorded as `REASON_NON_TEXT`.
    - Slice 8: `inspectability_for` accessor — `INSPECTED` / `TRUSTED` / `None`; `unregister` clears.
    - Slice 9: real-plugin audit — all 32 shipped plugins pass the canonical scan.
    - Slice 10: builder no longer carries `_FORBIDDEN_PATTERNS`; builder's `_scan_generated_code` still rejects via the canonical patterns (`pickle.loads` regression).
    - Slice 11: `plugins_list` IPC payload exposes the `inspectability` field; refusal entries carry the `forbidden_pattern` reason; belt-and-suspenders test exercises the real `cerebral.main._plugins_list_event` via module-level orchestrator swap.
11. **Test count after #46:** 1065 Python tests passing (3 integration skipped) + 75 JS tests passing.
12. **What this slice intentionally leaves to follow-up issues** — AST-completeness check that maps call sites to capability classes (#47), tray UI that renders the red "trusted, unverified" badge from the new `inspectability` field (lands alongside #53), consent surface that converts ACL ASK decisions into the tray prompt (#48). The static-pattern scan in this slice is a deliberately cheap backstop; #47's AST walk is the real check.
13. **One process correction worth recording.** The implementation PR included `Closes #46` in the body and the issue auto-closed on merge — the discipline saved a manual `gh issue close` round-trip and confirmed the new `feedback_closes_in_pr_body.md` memory.

---

### Issue #47 — AST-completeness check for declared capabilities ✅

1. `cerebral/security/call_site_capabilities.py` (new module) — single-file `ast` walker that maps every classifiable call site to the capability class(es) it needs and reports under-declaration. Public surface: `check_completeness(source, declared, *, source_path=None) → tuple[Finding, ...]`, `assert_complete(...)` (raises `CompletenessError`), `format_findings(...)`, and the `Finding` / `CompletenessError` dataclasses. Refusal reason code `REASON_UNDER_DECLARED = "under_declared_capability"` follows the #44/#46 `REASON_*` style.
2. **Mandatory in the builder, optional in the loader** (matches the issue AC split). `plugins/builder.py._create` runs the check on the staged source after the static scan and before pip-install / smoke / persistence — failures short-circuit the entire pipeline and leave no files behind. The orchestrator's hand-authored-plugin path is unchanged: registration relies on the author's hand-typed `REQUIRED_CAPABILITIES` and the AST utility is exposed for them to invoke on demand.
3. **Any-of semantics.** Each call-site rule maps to `frozenset[Capability]`; declaring AT LEAST ONE class in the set satisfies. Unambiguous calls map to single-class sets (`shutil.rmtree` → `{fs_delete}`); ambiguous primitives map to families (`subprocess.*` → shell-family `{shell_exec, device_control, network_recon, network_config, code_install, fs_write, fs_delete, vault_unlock, secrets_read}`; HTTP libs → `{network_egress_local, network_egress_cloud}`; `socket.*` → `{egress_local, egress_cloud, network_recon}`). The shell-family any-of is the practical resolution of "subprocess is the universal primitive; the plugin's intent-level declaration is the real record."
4. **Alias indirection — one hop, both forms.** First pass collects two alias tables:
   - module-level (`_run_subprocess = subprocess.run`, only in builder.py),
   - `__init__` self-attr with optional `or` chain (`self._run_fn = run_fn or subprocess.run`, used by 11 shell-touching plugins: shell, docker, git, system, ssh, vpn, bitwarden, printer, package_manager, network_scanner, plus builder).
   Only bindings whose RHS resolves to a known capability target are recorded. The walker rewrites `self._run_fn(...)` and `_run_subprocess(...)` to `subprocess.run` before lookup; calls inside non-aliased helpers (e.g. `_default_fetch` in n8n/phone/browser) are still caught directly because the walker descends into the whole file.
5. **Dotted-target table covers** subprocess.{run,Popen,call,check_call,check_output}, os.{remove,unlink,rmdir,removedirs,makedirs}, shutil.{rmtree,move,copy,copy2,copyfile,copytree}, pyperclip.{copy,paste}, httpx/requests/urllib.request/aiohttp methods + sessions, socket.{socket,create_connection,getaddrinfo}, mss.mss/PIL.ImageGrab.grab, pyautogui/keyboard/mouse, keyring, pip.main.
6. **Method-name fallback** (used when the receiver isn't statically dot-resolvable, e.g. `Path('x').read_text()`): `read_text`, `read_bytes`, `iterdir`, `write_text`, `write_bytes`, `touch`, `mkdir`, `unlink`, Tk `clipboard_*`. Generic names like `replace`/`rename`/`read`/`write` are deliberately excluded — they collide with `str.replace`, `dict.rename`, etc. and would false-positive on every plugin that does normal string handling (the finance plugin tripped this during implementation — the regression test now pins the exclusion).
7. **Dynamic dispatch is "unknown"** — `getattr(...)(...)`, `globals()[name](...)`, call-result chains, and any other form where `_resolve_dotted` returns None are classified as unknown and never fail the check. Matches the sharpener and pairs with #46's static-pattern scan for the truly dangerous dynamic forms.
8. **Failure message format** matches the sharpener pin: `path:line:col: <call snippet> requires <cap or any-of {a,b,…}> (declared: {…})`. Multi-finding errors emit one line per finding under a single header. `Finding` carries structured `(line, col, snippet, target, required, declared)` so the builder (and any future caller) can render the report in its own format.
9. `plugins/builder.py` now declares **`frozenset({"code_install", "fs_write"})`** — the AST walker caught the staging-write call sites (`Path.mkdir`, `Path.write_text`, `shutil.move` into `plugins/<name>/`). Intent-level user prompt remains `code_install`; the second class records the real filesystem touch. Comment in the source pins the rationale.
10. **New tests** — `cerebral/tests/test_call_site_capabilities.py` (new file, 89 tests across 10 slices):
    - Slice 1: empty source / no calls / declared-complete / over-declared all pass.
    - Slice 2: under-declaration produces a structured `Finding` with line, col, snippet, target, required, declared; failure message includes path + required class + declared set.
    - Slice 3: parametrised dotted-target coverage (16 entries); subprocess any-of family validated; HTTP libs satisfied by either egress class; HTTP without any egress declaration still fails.
    - Slice 4: method-name fallback over Path read/write/delete/touch/mkdir; bare `open(...)` → fs_read.
    - Slice 5: module-level alias resolution; `self.x = subprocess.run` direct binding; `self.x = run_fn or subprocess.run` `or`-chain; incidental aliases (`self._fetch = fetch_fn or _default_fetch`) NOT captured, but calls inside `_default_fetch` itself ARE caught (whole-file walk).
    - Slice 6: dynamic dispatch via `getattr`, subscript, call-result chains — all classified as unknown, never fail.
    - Slice 7: nested functions, lambdas, decorator-form calls, default-arg call evaluation, comprehensions, class-body calls — every form walked.
    - Slice 8: multi-finding listing covers every site in source order; format emits one line per finding.
    - Slice 9: `assert_complete` returns `None` on clean source, raises `CompletenessError` with structured findings on dirty source; `CompletenessError.findings` / `.source_path` shape stable for builder consumption; `REASON_UNDER_DECLARED` constant pinned.
    - Slice 10: parametrised real-plugin audit — every shipped plugin (33 modules, including builder.py's new fs_write declaration) returns an empty findings tuple. Walker either maps to a declared cap or returns "unknown"; never under-flags.
11. **New tests** — `cerebral/tests/test_plugin_builder.py` Cycle 10 (5 tests):
    - Under-declared call site rejected → no smoke runner invocation, no files persisted into plugins_dir, nothing registered with the orchestrator.
    - Same source with complete declaration → smoke runs, files persist, registration carries the full capability set.
    - Over-declared payload passes through (over-declaration is intentional).
    - Syntax error in generated source surfaces as a clean error (builder catches `SyntaxError`), no files persisted.
    - AST check runs BEFORE pip-install — a malformed declaration with a pip dep doesn't side-effect pip even with a vetted allowlist.
12. `cerebral/security/__init__.py` — re-exports `check_completeness`, `assert_complete`, `format_findings`, `Finding`, `CompletenessError`, `REASON_UNDER_DECLARED`.
13. **Test count after #47:** 1159 Python tests passing (3 integration skipped) + 75 JS tests passing.
14. **What this slice intentionally leaves to follow-up issues / future deepening** — HTTP-lib URL-literal local/cloud splitting (deferred; v1 uses any-of `{network_egress_local, network_egress_cloud}` because the shipped plugins build URLs from runtime `base_url`; follow-up comment posted on #47); cross-file walk for `plugins/<name>/server.py` sibling files (v1 walks `server.py` only); structured-output self-correcting loop for the builder (lands after issue #6); consent surface that converts `ASK` decisions into a tray prompt (#48); the Permissions UI tab that surfaces the capability declaration per plugin (#53).
15. **Two process notes worth recording.**
    - **Deviation from the sharpener was the right call.** The sharpener pinned literal-URL splitting for HTTP libs; strictly implementing it false-positived on `n8n`, `phone`, and `browser` (their URLs are runtime-built from a configurable `base_url` defaulting to localhost). v1 maps HTTP libs to the any-of egress set; the deviation is documented on the issue thread. Memory `feedback_closes_in_pr_body.md` says to follow live code and post a brief note rather than silently diverge — that's what happened here.
    - **`gh pr merge --delete-branch` from a worktree fired the predictable failure** noted in `feedback_gh_merge_worktree_cleanup.md`. The merge itself succeeded (state=MERGED, issue #47 auto-closed); `--delete-branch` failed because `master` is held by a sibling worktree. `git ls-remote --heads origin issue-47-ast-completeness-check` confirmed the remote branch survived; manual `git push origin --delete` cleaned it. Third time this has fired this month — the memory is earning its keep.

---

### Issue #48 — tray consent notification (Once/Session/Persistent/Deny + Why?) ✅

1. `cerebral/security/consent.py` (new module) — `ConsentSurface` bridges the orchestrator's `ASK` decisions to the tray. Public surface: `ConsentSurface(prompt_fn, has_subscriber_fn, acl, request_id_fn)`, async `request(capability, tool_name, args, flags) → Decision`, sync `set_acl(acl)` and `set_consent_surface(...)` convenience setters; constants `CHOICE_ONCE/SESSION/PERSISTENT/DENY`, helper `is_valid_choice`, `build_args_preview`. `ConsentRequest` dataclass holds the IPC payload shape with a `to_ipc() → dict` renderer.
2. `cerebral/security/labels.py` (new module) — `CAPABILITY_LABEL: Mapping[Capability, str]` (short noun phrase, e.g. `"Write files on disk"`) and `CAPABILITY_DESCRIPTION: Mapping[Capability, str]` (one-sentence explainer mentioning "Felix"). Both tables are `MappingProxyType` (immutable) and complete against the closed 16-class vocabulary; import-time assertion raises if a class is missing from either. Helpers `label_for(cap) → str` and `description_for(cap) → str` are re-exported from `cerebral.security`.
3. `cerebral/mcp/orchestrator.py` — new `consent: ConsentSurface | None` constructor kwarg + `set_consent_surface(consent)` setter. `call_tool` now does: ACL/gate resolves → if `ASK` and a consent surface is wired, `await self._consent.request(capability, name, args, flags)` → SILENT dispatches, DENY refuses. `set_acl` keeps the surface in sync (it's profile-scoped) by calling `self._consent.set_acl(...)` when wired. No consent surface = pre-#48 fail-closed behaviour (ASK → DENY) preserved for tests and the no-profile bootstrap.
4. `cerebral/main.py` — wires the WebSocket bridge. `_consent_prompt(req)` adds an `asyncio.Future` to `_pending_consents[request_id]`, broadcasts `consent_request` to all connected tray clients, awaits the future. New `consent_response` IPC handler validates the choice via `is_valid_choice` (defensive: unknown choice → DENY) and resolves the matching future. `_consent_has_subscriber()` returns `len(_connected) > 0` so the surface fail-closes when no tray is attached. `ConsentSurface` is constructed at startup with the active profile's ACL and attached via `_orc.set_consent_surface(...)`.
5. **Four buttons → ACL methods** (verified live against `cerebral/security/acl.py`):
   - **Session** → `acl.grant_session(capability, Decision.SILENT)` (RAM, cleared on profile switch / restart)
   - **Persistent** → `acl.set_persistent_class(capability, Decision.SILENT)` (writes to `profile_acl` table)
   - **Deny** → no ACL mutation; orchestrator refuses this call only
   - **Once** → no ACL mutation; orchestrator dispatches this call only (see point 11 for the sharpener deviation)
   The Permissions-UI-only `set_tool_override` is intentionally NOT surfaced in the prompt (sharpener #1).
6. **Fail-closed paths.** All three deny without prompting and without mutating the ACL:
   - No tray subscriber attached when prompt would fire → DENY without emitting an event.
   - `flags.irreversible=True` → DENY (the modal lands in #49).
   - 30s timeout via `asyncio.wait_for` → DENY (configurable via `OPENMIND_CONSENT_TIMEOUT_SEC`, read fresh per call so operators can tune without restart; non-numeric / non-positive values fall back to 30s with a log warning).
7. **Concurrency.** Per-`(profile_id, capability.value)` `asyncio.Lock` in `ConsentSurface._locks` serialises prompts of the same class. A second concurrent caller waits for the first prompt to close, then **re-resolves through the ACL inside the lock**. Session/Persistent grants from the first prompt satisfy the waiter silently; Once and Deny leave the ACL untouched so the waiter re-prompts. Different capabilities run in parallel (per-class, not global). `set_acl(...)` clears `_locks` so per-profile state never leaks across switches.
8. **IPC contract** (slotted into the existing `{type, data: {...}}` envelope used by every other event — diverges from the sharpener's flat sketch to match `_plugins_list_event`, `_queue_update_event`, etc.):
   ```
   Cerebral → tray:  {"type": "consent_request",  "data": {
       "request_id": "<uuid4>",
       "tool_name":  "files.write_journal",
       "capability": "fs_write",
       "capability_label":       "Write files on disk",
       "capability_description": "Felix needs to ...",
       "args_preview": {"path": "/Users/me/journal.md"},
       "flags": {"passive": false, "irreversible": false}
   }}
   Tray → Cerebral: {"type": "consent_response", "data": {
       "request_id": "<same uuid>",
       "choice": "once" | "session" | "persistent" | "deny"
   }}
   ```
9. **Args preview truncation.** `build_args_preview(args, limit=200)` passes through scalars and short strings, truncates strings > 200 chars with `…`, and reprs containers (sharpener #8). No masking heuristics in v1 — sensitive values shouldn't be in tool args (vault → `vault_unlock`, not a passed-in token).
10. **Tray side.** `tray/lib/consent-manager.js` is the UI-agnostic JS half (testable in jest): handles `consent_request` events, opens / closes prompts via injected callbacks, routes choices back via injected `send`. `tray/main.js` integration: one `BrowserWindow` per outstanding `request_id`, sized 360×340, `alwaysOnTop`, `skipTaskbar`. `consentManager.reset()` is called on WebSocket disconnect so the user isn't left clicking buttons that go nowhere. `tray/windows/consent.html` renders the four buttons with `Allow once / Allow this session / Always allow / Deny` labels, a Why? expander showing the capability description + args preview, and Escape as a shortcut for Deny. Passive and irreversible pills shown when set.
11. **Deviation from sharpener pin #1 (documented in code, tests, and on issue #48).** Pin #1 maps Once → `acl.grant_once(SILENT)`. Pin #4 says a concurrent waiter must re-prompt after Once. With `grant_once`, the waiter's `ACL.resolve()` would consume the stashed SILENT grant and silently proceed — directly contradicting #4. `ConsentSurface._apply_choice` therefore handles Once as a no-op on the ACL (returns SILENT; orchestrator dispatches the current call only). The behaviour is pinned by `test_concurrent_calls_once_choice_reprompts_second`. `grant_once` remains in the public ACL API for future callers. Follows the `#47` HTTP-lib precedent: live semantics > sharpener pin, plus an explanatory comment on the issue thread.
12. **New tests:**
    - `cerebral/tests/test_consent_surface.py` (new file, 34 tests across 10 slices): fail-closed paths (no subscriber, irreversible, timeout) / four choice paths (Once/Session/Persistent/Deny — each verifying the right ACL mutation and the correct return) / IPC payload envelope shape + passive/irreversible flag pass-through / args preview truncation + scalar preservation + key coercion / `is_valid_choice` accepts the four verbs and rejects everything else / per-(profile, capability) prompt serialisation with two concurrent callers (Persistent satisfies, Once/Deny re-prompt) / different-capability calls run in parallel / orchestrator integration (ASK → surface → SILENT dispatches; DENY blocks; no surface = fail-closed; SILENT default skips surface; no subscriber fail-closes) / full ask → Persistent → next-call-silent round-trip with a real `ProfileManager` and a rebuilt-ACL across calls / `set_acl` swap clears locks and rebinds the surface / `OPENMIND_CONSENT_TIMEOUT_SEC` env override + garbage / zero / negative all fall back to the default.
    - `cerebral/tests/test_consent_labels.py` (new file, 120 tests across 3 slices): exhaustive `CAPABILITY_LABEL` + `CAPABILITY_DESCRIPTION` coverage of the 16-class vocab parametrised per class (label + description present, accessors return strings, both tables are immutable `MappingProxyType`, no extra keys) / quality invariants (labels under 60 chars without trailing punctuation, descriptions under 240 chars ending in `.` and starting uppercase, every description mentions "Felix" for tone consistency).
    - `tray/tests/consent-manager.test.js` (new file, 27 tests across 10 cycles): vocabulary check / single request opens prompt with full record / respond emits `consent_response` and clears state / all four choices round-trip verbatim / unknown choice coerces to Deny (defensive) / respond to stale or already-resolved request_id is a no-op returning false / malformed payload (null, missing request_id, missing capability) is ignored without crashing / optional fields default cleanly / two concurrent requests both open and resolve independently / reset closes all open prompts but does NOT emit responses (Cerebral will timeout) / `get()` and `pendingCount` accessors.
13. **Test count after #48:** 1313 Python tests passing (3 integration skipped) + 102 JS tests passing.
14. **What this slice intentionally leaves to follow-up issues** — irreversible-flag modal in the visualiser window (#49) is currently stubbed as DENY; voice consent via Vosk constrained grammar `["yes","no","later"]` (#50); builder-pipeline integration consuming `code_install` once for the whole build via the new surface (#51 AC#3); queue admission overlay + verb denylist flipping `passive=True` on queued items so they escalate through the surface (#52); Permissions settings UI rendering `list_persistent_grants()` and writing via `set_persistent_class` / `set_tool_override` (#53). The Once button's lack of ACL mutation is by design (see #11) — the public `grant_once` API is preserved for non-prompt callers.
15. **Three process notes worth recording.**
    - **Sharpener internal inconsistency, resolved by following the user-facing semantic.** Pins #1 and #4 contradicted on Once. Followed #4 (the user-visible behaviour); deviation note posted on the issue and pinned in code + tests. Same pattern as #47's HTTP-lib URL-splitting deviation — the precedent is now established for catching and documenting these conflicts inline.
    - **IPC envelope shape, deliberately aligned to the existing convention.** Every event in `cerebral/main.py` uses `{type, data: {...}}`; the sharpener's flat consent payload diverged. Followed the established envelope so the tray's `event.data` access stays uniform. A wrapper class (`ConsentRequest.to_ipc()`) keeps the rendering centralised so future shape tweaks are one-line.
    - **`gh pr merge --delete-branch` from a worktree fired the now-familiar failure** (fourth time this month, per the `feedback_gh_merge_worktree_cleanup.md` memory). Merge succeeded (state=MERGED, #48 auto-closed via `Closes #48`); `--delete-branch` failed because `master` is held by a sibling worktree. `git ls-remote --heads origin issue-48-tray-consent-surface` confirmed the remote branch survived; manual `git push origin --delete` cleaned it. The memory continues to earn its keep — assume this pattern will fire on every cleanup PR from a worktree until the underlying gh/git interaction is fixed upstream.

### Issue #51 — builder integration (consent prompt + new-plugin flag + uninstall ACL cleanup) ✅

1. `cerebral/db/profiles.py` — new `plugin_flags` SQLite table `(plugin_name TEXT PRIMARY KEY, new_plugin INTEGER NOT NULL DEFAULT 0 CHECK (new_plugin IN (0,1)), installed_at DATETIME DEFAULT CURRENT_TIMESTAMP)` plus 5 methods on `ProfileManager`: `set_plugin_new_flag(plugin_name, value)`, `get_plugin_new_flag(plugin_name) → bool` (defaults False when no row), `remove_plugin_flag(plugin_name) → bool`, `list_plugin_flags() → dict[str, bool]` (only rows present, used by the tray's plugins_list badge renderer), `remove_plugin_acl_rows(plugin_name, tool_names: Iterable[str]) → int` (deletes per-tool overrides matching the supplied tool names across all profiles; class-scope rows survive per the sharpener pin).
2. `cerebral/security/acl.py` — `ProfileACL.__init__` gains optional `new_plugin_flag_for_tool: Callable[[str], bool] | None` kwarg (defaults to a no-op lambda for legacy callers). `_resolve_pre_escalation` consults the hook *before* steps 1-5: when True for the tool's plugin, resolution short-circuits straight to the profile's default-policy snapshot, bypassing per-tool overrides, persistent class grants, session grants, and once grants. Passive escalation still applies *after* the carve-out, identical to the rest of the chain. The hook is a callable rather than a direct ProfileManager reference because the ACL needs the tool → plugin translation, which only the orchestrator owns.
3. `cerebral/mcp/orchestrator.py` — new `plugin_for_tool(tool_name) → str | None` lookup backed by the same `_tool_index` that routes calls. Used by `cerebral/main.py:_new_plugin_flag_for_tool` to translate a tool name → owning plugin → `pm.get_plugin_new_flag(plugin)`. Unregistering a plugin drops its `_tool_index` entries (already covered by `_remove_from_index`); pinned by `test_plugin_for_tool_drops_mapping_on_unregister`.
4. `plugins/builder.py` — major rework of `_create`:
   - Validates payload `description: str` (non-empty after strip) immediately after the existence check; AC#1's required field.
   - REQUIRED_CAPABILITIES now declares `{"code_install", "fs_write", "fs_delete"}` — the AST-completeness check (#47) caught the `shutil.rmtree` / `Path.unlink` in `_uninstall` and refused registration until `fs_delete` was added.
   - Stages source files into a `tempfile.TemporaryDirectory` *before* prompting so the consent surface's preview_path points at a real file on disk at request time. (Test fake snapshots `is_file()` inline because the temp dir is torn down before the assertion fires.)
   - Prompts via `self._orc.consent_surface.request(Capability.CODE_INSTALL, "builder.install", args_preview, CallFlags())`. The `args_preview` dict carries six fields: `plugin_name`, `description`, `capability_labels` (sorted `label_for()` strings), `capabilities` (the raw vocabulary set, for the tray's internal use), `pip_deps`, and `preview_path` (string path to the staged `server.py`).
   - Fail-closed when `consent_surface` is None (`MCPOrchestrator()` with no surface wired) — returns a clear "no consent surface" error without staging, installing, or registering. Pinned by `test_install_with_no_consent_surface_fails_closed`.
   - On `Decision.SILENT` proceeds with pip install → import → smoke → move → register. On anything else returns "Install cancelled for plugin … (consent: …)" with no side effects.
   - After successful install, if a `ProfileManager` is wired (always in production via main.py), calls `pm.set_plugin_new_flag(name, True)`. Legacy test paths that don't supply a `profile_manager` skip this silently — same default-False story as hand-authored plugins, per AC#5.
   - Re-install on an existing name returns `"Plugin 'X' already exists in <dir> — uninstall first."` (existing message extended; both the legacy "exists" assertion and the new "uninstall first" affordance match).
5. **New `builder_uninstall` tool** (the user-facing "update" path; the builder still refuses to overwrite, so updates are uninstall → re-create):
   - Validates name against `_NAME_RE`; rejects path-traversal etc.
   - Refuses if the plugin isn't registered AND no `plugins/<name>/` or flat `plugins/<name>.py` exists (returns "No plugin 'X' found …").
   - Captures the tool names from the live plugin BEFORE `orchestrator.unregister(name)` (the post-unregister state has no tools to enumerate).
   - Removes `plugins/<name>/` via `shutil.rmtree` and any flat `plugins/<name>.py` via `Path.unlink`.
   - When `ProfileManager` is wired: `pm.remove_plugin_flag(name)` (clears the new-plugin badge) and `pm.remove_plugin_acl_rows(name, tool_names)` (drops per-tool overrides). Returns the dropped-row count in the JSON result.
   - Class-scope grants (`scope='class'`) survive — per the sharpener pin and AC#7 rationale: "a user who granted FS_WRITE persistently to one plugin probably wants it for the next one too."
6. `_ParkedBuilderPlugin.attach(...)` gains a `profile_manager: ProfileManager | None = None` kwarg threaded through to the live instance; `cerebral/main.py:_attach_builder_plugin` now passes `_pm` alongside `_llm_fn` and the existing pip allowlist.
7. `cerebral/main.py` wiring (three additions, all additive, no IPC breakage):
   - `_new_plugin_flag_for_tool(tool_name)` module-level helper closes over `_orc` and `_pm`; `_build_acl(profile)` now passes it into `ProfileACL`. Every ACL rebuild on profile switch picks up live flag state via the closure.
   - `_plugins_list_event()` now carries `new_plugin_flag: bool` per plugin in the registered list, alongside `required_capabilities` and `inspectability`. The tray's Permissions UI (#53) will consume it to render the "new plugin" badge.
   - New `clear_new_plugin_flag` IPC handler in `_handle_message`: validates `data.name`, calls `pm.set_plugin_new_flag(name, False)`, broadcasts the refreshed `plugins_list` so every connected tray drops the badge.
8. **Sharpener corrections noted on issue #51** (per the `feedback_handoff_format` precedent and the issue-#48 / issue-#47 deviation pattern):
   - The sharpener's "single-table approach" implicitly assumed a `plugins` SQLite table existed (matching `profiles.acl_defaults_snapshot`). It didn't, so the dedicated `plugin_flags` table from the sharpener's stated fallback was used. Hand-authored plugins have no row → `get_plugin_new_flag` returns False → AC#5 ("default to `new_plugin: false`") is satisfied without a migration.
   - `remove_plugin_acl_rows` takes `(plugin_name, tool_names)` rather than `(plugin_name)`; the ProfileManager doesn't own the orchestrator's tool → plugin map, so the orchestrator computes the tool list before unregister and passes it in. `plugin_name` is preserved on the signature for logging / future per-class variants.
   - The sharpener showed `orchestrator._consent.request(...)`; the live code uses the public property `orchestrator.consent_surface.request(...)` (the underscore is the legacy private name). Follows the post-#48 contract.
9. **6 new test cycles in `cerebral/tests/test_plugin_builder.py`** (Cycles 11–16; 18 new tests):
   - **TestBuilderInstallPrompt** (5): prompt fires for `code_install` with `tool_name="builder.install"`; args carry the six fields (name/description/labels/deps/preview_path) and the preview file exists at request time; Deny blocks persistence (no pip / no smoke / no files / no register); prompt runs before pip install; no consent surface = fail-closed.
   - **TestBuilderDescriptionField** (2): missing `description` rejected; whitespace-only `description` rejected.
   - **TestBuilderNewPluginFlag** (3): install sets `pm.get_plugin_new_flag(name) == True`; install without `profile_manager` leaves it unset (legacy path); the end-to-end regression — persistent SILENT FS_WRITE grant + new-plugin flag set → ACL.resolve returns ASK, AC#4's full assertion.
   - **TestBuilderUninstallFirst** (2): existing subdir and flat-file paths both return error messages containing "uninstall first".
   - **TestBuilderUninstall** (8): per-tool rows dropped, class-scope rows survive; `plugins/<name>/` directory removed; new-plugin flag cleared; orchestrator unregistered (incl. tool index cleared); full create→uninstall→re-create cycle succeeds; unknown name fails cleanly; invalid name rejected; `builder_uninstall` appears in `list_tools()`.
   - **TestBuilderPersistentGrant** (1, Cycle N from the sharpener): user picks Persistent on first install; second install of a DIFFERENT plugin name doesn't re-prompt (one prompt total) and both plugins register. Uses a real `ProfileACL` + a thin fake consent surface that mirrors the production `_apply_choice` logic (mutate ACL on Persistent → return SILENT; subsequent calls resolve to SILENT via the persistent grant). The fake substitutes the source string with `"weatherbug"→"other"` and `"WeatherbugPlugin"→"OtherPlugin"` so the generated module's `PLUGIN_NAME` actually matches the payload name — a subtle gotcha when reusing `_GENERATED_SERVER` across plugins.
10. **3 new slices in `cerebral/tests/test_profile_acl.py`** (12 new tests): plugin_flags table CRUD (default-False, set-persists, overwrite, remove-returns-rowcount, list-only-rows-present); `remove_plugin_acl_rows` (drops only per-tool rows for the named tools across all profiles, class-scope survives, no-match returns 0); ACL new-plugin-flag carve-out (persistent class grant ignored when flag set; per-tool override also ignored; other plugins resolve normally; default hook is a no-op).
11. **3 new tests in `cerebral/tests/test_orchestrator.py`** (Slice 12): `plugin_for_tool` returns the owning plugin name, returns None for unknown tools, drops mapping on unregister.
12. **Test count after #51:** 1349 Python passing (was 1313 → +36; 3 integration skipped) + 102 JS passing (unchanged — IPC additions are additive and don't touch consent-manager.js or the visualiser).
13. **What this slice intentionally leaves to follow-up issues** — Permissions UI (#53) consumes `plugins_list.new_plugin_flag` to render the "new plugin" badge and sends `clear_new_plugin_flag` over IPC to flip it off (the IPC handler is wired and tested; only the UI is missing). The irreversible-flagged path (#49) still resolves to DENY in the consent surface, so a future irreversible install path (none exists yet — builder calls aren't irreversible) would correctly fail-closed there. The voice-consent constrained grammar (#50) and the queue admission overlay (#52) are independent of the builder path.
14. **Three process notes worth recording.**
    - **`MCPOrchestrator(consent=...)` is duck-typed.** The orchestrator type-hints `ConsentSurface | None` but only calls `await consent.request(...)` and `consent.set_acl(acl)` on it. The test fakes `_AutoAllowConsent` and `_ScriptedConsent` satisfy the protocol without inheriting; perfect for hermetic tests of paths that aren't specifically about the surface internals. Updated 34 test sites in `test_plugin_builder.py` via `replace_all` of `MCPOrchestrator()` → `_make_orc()` (helper returning `MCPOrchestrator(consent=_AutoAllowConsent())`).
    - **TemporaryDirectory + consent fake = capture inline.** The builder stages source into a `tempfile.TemporaryDirectory` then prompts. The `with` block exits before the test inspects the captured args, so the staged file is gone by then. `_ScriptedConsent.request` now snapshots `Path(preview_path).is_file()` inline and stores it as `preview_path_is_file_at_request` so the test asserts on the snapshot, not the live filesystem.
    - **`gh pr merge --delete-branch` from a worktree fired the failure again** (fifth time per the `feedback_gh_merge_worktree_cleanup.md` memory, second time on this specific worktree path). Merge succeeded (state=MERGED, #51 auto-closed via `Closes #51`); `--delete-branch` failed with `fatal: 'master' is already used by worktree at .../optimistic-austin-31f2a7`. `git ls-remote --heads origin issue-51-builder-integration` confirmed the remote branch survived; manual `git push origin --delete issue-51-builder-integration` cleaned it. Five-for-five — budget for it on every cleanup PR.
    - **Multi-line `gh issue comment --body` quoting on PowerShell**: now logged to `.learnings/LEARNINGS.md` and to the `feedback_gh_issue_comment_quoting.md` memory. The fix is `gh issue comment N --body-file <path>` — single-quoted here-string into a temp file, never inline. Used for the sharpener correction comment on #51 (https://github.com/iggyghub/OpenMind/issues/51#issuecomment-4454099909).

### Issue #49 — irreversible-flag modal surface ✅

1. `cerebral/security/modal.py` (new module) — `ModalSurface` mirrors `ConsentSurface` but with a strictly two-button (Accept / Cancel) vocabulary, no ACL binding (AC#4: acceptance is one-shot, never persisted), and no per-(profile, capability) lock (irreversible calls are rare; sharpener #3 explicitly waived serialisation). Public surface: `ModalSurface(prompt_fn, has_subscriber_fn, request_id_fn)`, async `request(capability, tool_name, args, flags) → Decision`. Returns `Decision.SILENT` on Accept, `Decision.DENY` on Cancel / timeout / no-subscriber / unknown-choice. `ModalRequest` dataclass renders the IPC payload via `to_ipc()`; constants `CHOICE_ACCEPT = "accept"` and `CHOICE_CANCEL = "cancel"` plus helper `is_valid_modal_choice`. Shares `_timeout_seconds()` and `build_args_preview()` from `consent.py` — `OPENMIND_CONSENT_TIMEOUT_SEC` covers both surfaces for v1; a dedicated `OPENMIND_MODAL_TIMEOUT_SEC` can split off later if a longer modal window proves useful.
2. `cerebral/security/__init__.py` re-exports `ModalSurface`, `ModalRequest`, `CHOICE_ACCEPT`, `CHOICE_CANCEL`, `is_valid_modal_choice` alongside the existing `ConsentSurface` family.
3. `cerebral/mcp/orchestrator.py` — new `modal: ModalSurface | None` constructor kwarg + `set_modal_surface(modal)` setter + `modal_surface` property. **Routing change in `call_tool`:** ACL/gate resolves first → if `flags.irreversible=True` AND the decision is not already `DENY`, route through the modal (when wired) or fail-closed to `DENY` (when not). The consent surface only handles `ASK` for non-irreversible calls. The `if/elif` structure means irreversible never reaches the consent surface from the orchestrator path, regardless of whether the underlying decision was `SILENT`, `ASK`, or `DENY` — the gate/ACL is consulted purely to short-circuit refusal before bothering the user.
4. **Sharpener #2 carve-out preserved:** `flags.irreversible=True` with `decision is Decision.DENY` (e.g. `shell_exec` under the default policy) skips the modal entirely. The user isn't asked to confirm something the policy already refuses. Pinned by `test_irreversible_with_acl_deny_skips_modal`.
5. **AC#2 (the headline regression for #49) honoured at two layers:**
   - `ProfileACL.set_persistent_class(FS_DELETE, SILENT)` + `flags.irreversible=True` → modal still fires. Pinned by `test_irreversible_fires_even_with_persistent_grant`.
   - `ProfileACL.grant_session(FS_DELETE, SILENT)` + `flags.irreversible=True` → modal still fires. Pinned by `test_irreversible_fires_even_with_session_grant`.
   The orchestrator's irreversible-routing rule sits *before* the ACL's SILENT short-circuit takes effect for dispatch, so the existing per-class grant doesn't bypass the modal.
6. **AC#4 (no ACL mutation, ever):** `ModalSurface` has no `acl` attribute, no `set_acl` method, no `grant_*` call. Defence-in-depth via the type system — there's nothing to mutate by accident. End-to-end via the orchestrator: two consecutive `Accept`s leave `acl.list_persistent_grants() == []` and both prompt fresh. Pinned by `test_modal_does_not_carry_an_acl` and `test_accept_does_not_mutate_acl_via_orchestrator`.
7. **Removed the pre-#49 stub in `ConsentSurface.request`** (the `if flags.irreversible: return DENY` from the issue-#48 codebase). The orchestrator's `call_tool` ladder is now the single source of truth for irreversible routing. Defensive double-checking in `ConsentSurface` would just hide a future routing bug. The `test_irreversible_flag_denies_without_prompting` test from `test_consent_surface.py` was removed; the equivalent invariant — "irreversible never reaches the consent surface" — lives in `test_irreversible_modal.py::test_irreversible_never_reaches_consent_surface` and asserts on the orchestrator path rather than the surface internals.
8. **IPC contract** (slotted into the existing `{type, data: {...}}` envelope):
   ```
   Cerebral → tray:  {"type": "irreversible_modal_request", "data": {
       "request_id": "<uuid4>",
       "tool_name":  "files.delete",
       "capability": "fs_delete",
       "capability_label":       "Delete files on disk",
       "capability_description": "Felix needs to ...",
       "args_preview": {"path": "/Users/me/notes.md"},
       "flags": {"passive": false, "irreversible": true}
   }}
   Tray → Cerebral: {"type": "irreversible_modal_response", "data": {
       "request_id": "<same uuid>",
       "choice": "accept" | "cancel"
   }}
   ```
   The verb namespace is fully disjoint from the consent surface's four. The tray's `modal-manager.js` rejects all consent-surface verbs (`once`/`session`/`persistent`/`deny`) by coercing them to `cancel`, and Cerebral's IPC handler re-validates via `is_valid_modal_choice` and resolves any unknown to `cancel` on the future. Belt and suspenders.
9. `cerebral/main.py` wiring:
   - New `_pending_modals: dict[str, asyncio.Future[str]]` parallel to `_pending_consents`.
   - `_modal_prompt(req)` adds a future, broadcasts `irreversible_modal_request`, awaits the future; surface wraps in `asyncio.wait_for(..., timeout=...)`.
   - `ModalSurface(prompt_fn=_modal_prompt, has_subscriber_fn=_consent_has_subscriber)` — same subscriber check as the consent surface (`len(_connected) > 0`); a tray that's gone is gone for both surfaces.
   - `_orc.set_modal_surface(_modal_surface)` at module load.
   - New `irreversible_modal_response` IPC handler validates the choice via `is_valid_modal_choice` (defensive: unknown choice → DENY) and resolves the matching future.
10. **Visualiser deviation (documented in code + here per sharpener #1).** ADR-0005 says "modal in the visualiser window", but the visualiser is a 200×200 transparent click-through `BrowserWindow` with `setIgnoreMouseEvents(true)` — not a viable modal host. The implementation opens a dedicated 420×320 standalone `BrowserWindow` (`tray/windows/irreversible-modal.html`) with `alwaysOnTop: true` and `skipTaskbar: true`. The window is anchored to the visualiser UX in *intent* — pink/magenta warning treatment, prominent args block — but is a separate window in *practice*. The deviation matches the sharpener's recommended phrasing: "visualiser window" was shorthand for "modal anchored to the visualiser UX surface".
11. **Tray side.** `tray/lib/modal-manager.js` is the UI-agnostic JS half (testable in jest): handles `irreversible_modal_request` events, opens / closes prompts via injected callbacks, routes Accept/Cancel back via injected `send`. `tray/main.js` integration: one `BrowserWindow` per outstanding `request_id` via a separate `modalWindows` Map (never collides with `consentWindows`), sized 420×320, `alwaysOnTop`, `skipTaskbar`. `modalManager.reset()` is called on WebSocket disconnect alongside `consentManager.reset()`. `tray/windows/irreversible-modal.html` renders Accept (primary, pink) + Cancel (secondary, neutral), a prominent args block, the capability description, and Escape mapped to Cancel. Accept is NOT auto-focused — the user must tab to it or click, by design, to keep someone from confirming an irreversible action by reflex.
12. **New tests:**
    - `cerebral/tests/test_irreversible_modal.py` (new file, 23 tests across 7 slices): fail-closed (no subscriber, timeout) / two choice paths (Accept → SILENT, Cancel → DENY, unknown → DENY) / no-ACL-mutation invariant — both structurally (`ModalSurface` has no `acl` attribute / `set_acl` method) and end-to-end (two `Accept`s leave the ACL empty, modal fires both times) / IPC envelope shape with passive flag pass-through and args truncation / `is_valid_modal_choice` accepts only `accept`/`cancel`, rejects the four consent verbs / orchestrator integration: routes irreversible to modal, Cancel blocks dispatch, **fires even with a persistent SILENT grant (AC#2)**, **fires even with a session SILENT grant (AC#2)**, ACL-says-DENY skips the modal, never reaches the consent surface, no modal surface = fail-closed regardless of grant, non-irreversible still routes to consent not modal, no-subscriber fail-closes via orchestrator / `set_modal_surface` late-binding sets the property and switches behaviour from fail-closed to dispatch.
    - `tray/tests/modal-manager.test.js` (new file, 26 tests across 10 cycles): vocabulary check (only `accept`/`cancel`) / single request opens prompt with full record / respond emits `irreversible_modal_response` and clears state / both choices round-trip verbatim / unknown choice coerces to `cancel` (defensive) / consent-surface verbs (`persistent` etc) coerce to `cancel` / respond to stale or already-resolved request_id is a no-op returning false / malformed payload (null, missing request_id, missing capability) is ignored without crashing / optional fields default cleanly (including `flags.irreversible: true` as the default — it's the defining property) / two concurrent requests both open and resolve independently / reset closes all open prompts but does NOT emit responses / `get()` and `pendingCount` accessors.
    - `cerebral/tests/test_consent_surface.py`: removed `test_irreversible_flag_denies_without_prompting` (the old stub assertion). The replacement invariant — "orchestrator never routes irreversible to the consent surface" — lives in `test_irreversible_modal.py`.
13. **Test count after #49:** 1371 Python passing (was 1349 → +22 net: 23 added in `test_irreversible_modal.py` minus 1 removed in `test_consent_surface.py`; 3 integration skipped) + 128 JS passing (was 102 → +26 from `modal-manager.test.js`).
14. **What this slice intentionally leaves to follow-up issues** — voice consent via Vosk constrained grammar `["yes","no","later"]` (#50) is independent; the modal is currently mouse/keyboard only. Queue admission overlay (#52) and the Permissions UI (#53) are independent — the modal carries no persistent state for the UI to render. A separate `OPENMIND_MODAL_TIMEOUT_SEC` env var (sharpener #6 suggested it as optional) was not added in v1 — `OPENMIND_CONSENT_TIMEOUT_SEC` covers both surfaces with a single tuning knob. If user feedback in active use wants a longer modal timeout, the split is a one-line change.
15. **Four process notes worth recording.**
    - **Stub-removal carries a regression test, not silence.** The old `irreversible-as-DENY` stub in `ConsentSurface.request` was deleted, but the new code pins the invariant elsewhere: `test_irreversible_never_reaches_consent_surface` drives a full orchestrator round-trip with both surfaces wired and asserts the consent prompt is never called. A removed defence is only safe if a positive test now witnesses what the defence was protecting.
    - **`MCPOrchestrator(modal=...)` follows the duck-typing precedent from #51.** The orchestrator only calls `await modal.request(...)` on the modal — no `set_acl` (deliberately absent: no ACL to bind). Test fakes need only `async request(...)`; the type hint is documentation, not protocol enforcement.
    - **The orchestrator's routing change is `if/elif`, not two independent `if`s.** Irreversible routes to the modal *exclusively*; the consent surface's `elif decision is Decision.ASK and self._consent is not None` branch is only reached for non-irreversible calls. This is the structural reason `test_irreversible_never_reaches_consent_surface` passes — there is no path where both surfaces fire on the same call.
    - **`gh pr merge --delete-branch` from a worktree fired the failure again** (sixth time per the `feedback_gh_merge_worktree_cleanup.md` memory). Merge succeeded (state=MERGED, #49 auto-closed via `Closes #49`); `--delete-branch` failed with `fatal: 'master' is already used by worktree at .../optimistic-austin-31f2a7`. `git ls-remote --heads origin issue-49-irreversible-modal` confirmed the remote branch survived; manual `git push origin --delete issue-49-irreversible-modal` cleaned it. Six-for-six.

### Issue #53 — Permissions settings UI ✅

1. `cerebral/security/acl.py` — `ProfileACL.list_session_grants()` returns the RAM session grants in a tray-shaped row list (`{capability, policy}`). Once-grants are intentionally NOT surfaced — they're consumed on the next call and listing them would race the consumer.
2. `cerebral/db/profiles.py` — new `shell_exec_unlocked INTEGER NOT NULL DEFAULT 0` column on `profiles` (one-way per-profile flip), `Profile.shell_exec_unlocked: bool` field, `ProfileManager.unlock_shell_exec(profile_id)` setter, and `_row_to_profile` defensively reads the new column. Migrated old DBs via the same `ALTER TABLE … ADD COLUMN` try/except loop that the other newer columns use; the loop now keys on `(name, sql_type, default)` tuples instead of just `(name, default)` so an `INTEGER` column can join the migration without a schema branch.
3. `cerebral/main.py` — `_permissions_state_event()` builds the snapshot the Permissions UI renders from: `profile_id`, `capability_vocabulary`, `class_defaults`, `persistent_class_grants`, `persistent_tool_overrides`, `session_class_grants`, `shell_exec_unlocked`. Stable shape; missing-profile case returns the same shape with empty containers + `profile_id=None` so the tray can render an empty state without crashing. `_capability_vocabulary()` projects the closed `Capability` enum + `CAPABILITY_LABEL` (#48) + `CAPABILITY_DESCRIPTION` (#48) + `DEFAULT_POLICY` per row — ordered by enum declaration so the tray's render is stable across runs.
4. **New IPC handlers in `cerebral/main.py`** (each one re-broadcasts `permissions_state` after mutating, so every connected tray's view stays consistent):
   - `list_permissions` → broadcasts the fresh state event (used on Permissions-window open / on-demand refresh).
   - `set_class_policy {capability, decision}` → `ProfileACL.set_persistent_class`. Refused with no row written and no rebroadcast when capability/decision is invalid OR when the capability is `shell_exec` and `_active_profile.shell_exec_unlocked` is False.
   - `revoke_class_policy {capability}` → `ProfileACL.revoke_persistent_class`. Used when the user resets a row to its inherited default.
   - `set_tool_override {tool, decision}` → `ProfileACL.set_tool_override`. `decision == "inherit"` calls `revoke_tool_override` instead — sharpener pin #4's "no row in profile_acl" semantics.
   - `revoke_session_grant {capability}` → `ProfileACL.revoke_session`. Used by the Capabilities-tab session-grant Revoke button.
   - `unlock_shell_exec` → `pm.unlock_shell_exec(active_profile.id)` + reloads the cached profile so the next `permissions_state` carries the updated flag. One-way per the ADR; no re-locker.
5. **Connect-time greeting** now sends `permissions_state` alongside the existing `profile_loaded` / `profiles_list` / `voices_list` / `models_list` / `plugins_list` events, so the Permissions window has a payload to render the moment it opens. `create_profile`, `switch_profile`, and `delete_profile` also rebroadcast after rebuilding the ACL — covering AC#6 ("switching profiles re-reads from `profile_acl` and the session store").
6. `tray/lib/permissions-store.js` — UI-agnostic state manager (testable in jest, no Electron). Public surface: `applyState`, `applyToolsList`, `applyPluginsList`, `state` / `tools` / `plugins` accessors, `effectiveClassPolicy(capability)` that returns `persistent > session > snapshot default` (matches the resolution order without per-tool, which lives in the Tools tab), `effectiveToolOverride(tool)` that returns the stored row or `'inherit'`, `filterTools(query)` that case-insensitive-matches on tool name OR plugin name (sharpener #4), `flaggedPlugins()` for the new-plugin clearer list, plus six outbound mutators (`setClassPolicy`, `revokeClassPolicy`, `setToolOverride`, `revokeSessionGrant`, `unlockShellExec`, `clearNewPluginFlag`) that wrap the IPC envelopes and return `false` on invalid input so the renderer can treat `false` as "ignore". `requestRefresh()` fires `list_permissions + list_tools + list_plugins` in one go for the on-open refresh.
7. `tray/windows/permissions.html` — 480x560 BrowserWindow, two tabs:
   - **Capabilities tab** — 16 rows from the closed vocabulary, each with a silent / ask / deny three-toggle (active state highlighted purple); session-grants sub-panel listing currently active session grants with a Revoke button each; new-plugin clearer sub-panel listing flagged plugins with a "Trust this plugin" button each. `shell_exec` row is greyed out with a dedicated red "Unlock shell access" button that opens a confirmation modal (Cancel + Enable buttons; Escape maps to Cancel) — Accept fires `unlock_shell_exec` and the next `permissions_state` event makes the toggles editable.
   - **Tools tab** — search input at the top filters by tool OR plugin name; per-tool override dropdown (`inherit / silent / ask / deny`) wired to `setToolOverride`. Initial selection comes from `permissions_state.persistent_tool_overrides`.
8. `tray/main.js` — Permissions menu entry alongside Queue / Insights / Visualiser / Model. New `permissionsWindow` BrowserWindow (480x560, resizable) opened on click; closes on user-close. `permissions_state`, `tools_list`, `plugins_list` events are cached at module level so a freshly-opened window has data to render before the next broadcast. `permissions:ready` IPC from the renderer pushes the cached snapshots into the new window AND fires the three list-* IPCs to Cerebral so the open-time view is fresh after a profile switch the window missed. `permissions:send` IPC is a thin forwarder — the store wraps every user action in a single envelope so main.js doesn't need a per-verb handler.
9. **Sharpener decisions worth pinning:**
   - **Single `permissions_state` event over a separate `capability_vocabulary` event** (sharpener #6 hedge: "Recommended"). Folded the vocabulary into the same payload so the tray gets one message per refresh; simpler than orchestrating two on-connect emissions and one less event the renderer needs to subscribe to.
   - **`shell_exec_unlocked` is a column on `profiles`, not a `profile_meta` table** (sharpener #2 explicit pick). One column, one migration row in the loop, no new table. Reverting to locked is intentionally not in this UI.
   - **Default-matching writes still create a row.** When the user clicks "deny" on a capability whose snapshot default is "deny", we still emit `set_persistent_class` and write the row. The user's explicit click is meaningful; reverting back to the snapshot is the separate `revoke_class_policy` verb. Tray UX maps three buttons to one verb per click cleanly that way.
   - **Once-grants intentionally not surfaced.** `list_session_grants` deliberately excludes once-grants because rendering them in a UI list races the consumer (a once-grant is consumed on next call; the user might click Revoke after the call already started). The Capabilities tab session-grant sub-panel only shows session class grants.
10. **New tests:**
    - `cerebral/tests/test_permissions_ipc.py` (new file, 27 tests across 9 slices): vocabulary covers all 16 classes + carries label/description/default + ordered by enum declaration / state-event payload shape (profile_id, class_defaults match snapshot, session_grants empty on fresh ACL, persistent splits class vs tool, no-active-profile renders empty payload) / `set_class_policy` happy path + invalid-capability no-op + invalid-decision no-op + `revoke_class_policy` clears / `set_tool_override` writes scope='tool' row + `inherit` clears existing row + invalid-decision no-op + missing-field no-op / `revoke_session_grant` clears in-memory grant + unknown-capability no-op / `shell_exec_locked_by_default` + `set_class_policy` refused while locked + `unlock_shell_exec` persists across `ProfileManager` restart and enables subsequent set + idempotent / `list_permissions` broadcasts state event / round-trip set→read via IPC + `switch_profile` rebroadcasts with the new profile's state (clean grants, locked shell_exec) + state event emitted on initial connect / vocabulary closure to known classes (AC#7).
    - `cerebral/tests/test_profile_acl.py` (+9 tests across 2 new slices): `list_session_grants` (empty on fresh ACL, populated after `grant_session`, excludes once-grants, clears after revoke + clears on `clear_transient`) / `shell_exec_unlocked` profile column (locked by default, `unlock_shell_exec` persists, idempotent, only affects the target profile).
    - `tray/tests/permissions-store.test.js` (new file, 39 tests across 13 cycles): vocabulary constants (`VALID_DECISIONS = silent/ask/deny`, `VALID_TOOL_OVERRIDES` adds `inherit`, `DEFAULT_STATE` keys) / `applyState` replaces snapshot + triggers onChange + ignores nullish + defends against missing nested fields / `applyToolsList` + `applyPluginsList` round-trips / `effectiveClassPolicy` matches the persistent > session > default order + returns null for unknown / `effectiveToolOverride` returns `inherit` when no row + stored row otherwise / `filterTools` matches name AND plugin (case-insensitive) + handles missing fields / `flaggedPlugins` lists only flag=true + ignores nullish entries / `setClassPolicy` happy path + refuses unknown decision + missing capability + `inherit` (only valid for tool overrides) / `revokeClassPolicy` envelope / `setToolOverride` happy path + accepts `inherit` + refuses unknown + missing tool / `revokeSessionGrant` envelope + missing capability / `unlockShellExec` envelope (no payload) / `clearNewPluginFlag` envelope + missing name / `requestRefresh` sends list_* trio / round-trip set→re-apply state reflects the change + profile switch reloads state and clears session grants.
11. **Test count after #53:** 1407 Python passing (was 1371 → +36; 3 integration skipped) + 167 JS passing (was 128 → +39).
12. **What this slice intentionally leaves to follow-up issues** — voice consent via Vosk constrained grammar `["yes","no","later"]` (#50) is independent of the UI surface (active-mode audio path, not tray IPC). Queue admission overlay (#52) hangs off the queue UI, not the Permissions UI. Sharpener #9's deferrals — exporting/importing permission profiles, bulk-edit across all tools of a plugin, undo history, "which tool triggered which session grant" — are deliberately out of scope; the v1 surface is the Capabilities + Tools tab pair plus the session-grant + new-plugin clearers, nothing else.
13. **Three process notes worth recording.**
    - **`asyncio.run()` inside sync test bodies poisons the shared event loop.** First pass at `test_permissions_ipc.py` wrapped each `_handle_message` call in `asyncio.run(...)`. The full Python suite then crashed `tests/test_plugin_n8n.py::test_unknown_tool_returns_error` with `RuntimeError: There is no current event loop in thread 'MainThread'` — the n8n test calls `asyncio.get_event_loop()` from a thread, which on Python 3.12+ requires an existing loop, and `asyncio.run` had just closed the only one. Converted the affected tests to `async def` so `pytest.ini`'s `asyncio_mode = auto` runs them on the shared loop without leaking state. Logged to `.learnings/LEARNINGS.md` under "asyncio.run + pytest-asyncio auto mode".
    - **Sharpener pin #6 "Recommended" was the right shape but the wrong message split.** Implemented it as a single `permissions_state` payload that includes the vocabulary, rather than emitting a separate `capability_vocabulary` event on connect. Same data the sharpener pinned, one message instead of two. The tray subscribes to one event and gets the full picture; future tweaks to the vocabulary shape only touch one renderer path. Documented above as a sharpener decision; no comment on the issue thread because the deviation is purely structural — the user-facing semantics are identical.
    - **`gh pr merge --delete-branch` from a worktree fired the failure again — seven-for-seven.** Merge succeeded (state=MERGED, #53 auto-closed via `Closes #53`); `--delete-branch` failed with `fatal: 'master' is already used by worktree at .../optimistic-austin-31f2a7`. `git ls-remote --heads origin issue-53-permissions-ui` confirmed the remote branch survived; manual `git push origin --delete issue-53-permissions-ui` cleaned it. The `feedback_gh_merge_worktree_cleanup.md` memory has now fired on every cleanup PR for the last seven issues — assume it's the steady state.

### Issue #52 — queue admission overlay (risky badge + ambient-actuation defeat) ✅

1. `cerebral/action_queue/risky_verbs.py` (new module) — closed v1 verb vocabulary as a `frozenset[str]`: `{send, transfer, wire, delete, purchase, pay, unlock, disable}`. `is_risky(action_what: str) -> bool` lowercases, tokenises via `re.findall(r"\b\w+\b", ...)`, returns True iff any token is in the set. Simple token-match, no stemming — "sends" / "sending" / "transferable" all correctly do not match. Pinned by 24 parametrised + edge-case tests in `cerebral/tests/test_risky_verbs.py` (vocabulary pin, per-verb detection, case-insensitivity, empty string, no-stem inflections, punctuation, substring rejection, multi-verb, anywhere-in-string).
2. `cerebral/action_queue/manager.py` — `QueueItem` gains `risky: bool` (default False). `to_dict()` includes the field for the IPC payload. Computed via `is_risky(title)` on `add_item` AND on `_row_to_item` (the SQLite read path) so existing rows pick up the flag on Cerebral restart without a schema migration — derived data, not stored. The DB-restart regression is pinned by `test_get_pending_preserves_risky_flag_across_restart` (write with mgr1, read with mgr2, assert risky still True for "Send the email" and False for "Read my notes").
3. `cerebral/mcp/orchestrator.py` — new module-level `_DECISION_RANK` mapping (`SILENT=0 < ASK=1 < DENY=2`) + `_worse(a, b)` strict-greater helper, plus the headline new method:
   ```python
   async def check_capabilities(
       self,
       tool_name: str,
       capabilities: frozenset[str],
       flags: CallFlags | None,
   ) -> Decision
   ```
   Runs `ACL.resolve(cap, tool_name, flags)` (or `Gate.check(cap, flags)` when no ACL is bound) for each capability — passive escalation already happens inside `resolve()` — and tracks the worst Decision across the set (DENY > ASK > SILENT). Then routes the worst through the modal surface (for `flags.irreversible` and `decision is not DENY`) OR the consent surface (for `decision is ASK`) — **at most once per call**. Unknown tool → DENY (defensive, never silently allows). Empty capability set → SILENT (mirrors `call_tool`'s "no capability" branch). Empty capabilities path explicitly skips the resolution loop AND the modal/consent routing.
4. **The reason `check_capabilities` exists, not a `call_tool` loop:** `call_tool` *invokes* the tool on a SILENT decision. Looping `call_tool` across declared capabilities would dispatch the side-effectful tool the moment the first SILENT cap resolves — breaking the AND semantics the queue admission overlay needs. `check_capabilities` resolves side-effect-free and is the regression-tested contract. Pinned by 14 tests in `test_orchestrator.py` Slice 10 (empty set, unknown tool, single SILENT/DENY, worst-across-set with SILENT+ASK and DENY+ASK+SILENT, plugin never invoked, plugin never invoked even when the FIRST cap iterated is SILENT, passive escalation per cap, consent prompts ONCE across multiple ASK caps, irreversible routes to modal, irreversible skipped when ACL already DENY, consent denial propagates).
5. `cerebral/main.py` — `approve_item` handler (line 743) rewrite:
   - `flags = CallFlags(passive=True)` for the gate check.
   - Resolve plugin via `_orc.plugin_for_tool(item.tool_name)` → capabilities via `_orc.required_capabilities_for(plugin_name)`. None / empty → no gate constraint, dispatch directly.
   - `decision = await _orc.check_capabilities(item.tool_name, caps, flags)`.
   - On `Decision.SILENT`: dispatch via `_orc.call_tool(item.tool_name, item.tool_args or {})` — **no capability, no flags** (see process note below). `check_capabilities` is the gate for this call; passing the capability into `call_tool` would re-run the gate and double-prompt the user.
   - On non-SILENT (DENY only — ASK never returned): broadcast `queue_item_result` with `is_error=True` and a decision-derived message. Tool NEVER invoked.
   - Wake-originated path (`call_tool` IPC handler at main.py:734, eventually invoked via `_process_command`) continues to pass no capability / no flags — same behaviour as before. The contrast between queue (passive=True via the new check) and wake (no flags via call_tool directly) is the structural source of the ambient-actuation defeat.
6. `tray/windows/queue.html` — small inline patch (no behaviour migration to a manager module; sharpener #3 said don't bother): 🛑 badge inline with the title for `item.risky === true` (with drop-shadow via `filter: drop-shadow(0 0 3px #e0555555)`), rows start collapsed (`item-summary` / `item-tool` hidden via `max-height: 0; opacity: 0` transition), click the title to toggle `.expanded` on the row, no per-item persistence — sharpener #3's "collapse fresh on every menu open" honoured because the renderer re-runs `render()` from scratch on every `queue:items` event. The toggle chevron rotates 90° when expanded. The `item-risky-badge` has a `title=` attribute for hover-reveal of the rationale.
7. **Sharpener decisions worth pinning:**
   - **Sharpener #6 said the queue is RAM-only — it's not.** The queue has been SQLite-backed since #8 (`cerebral/data/openmind.db`, table `queue`). The implementation reads `risky` on every row read (derived, not stored) so vocabulary changes apply to existing items without a schema migration. Posted as a sharpener correction on the issue (https://github.com/iggyghub/OpenMind/issues/52#issuecomment-4461562809), folded into the comment with the second correction below — single combined comment, not two.
   - **Sharpener #4 hedged on AND semantics with "use the first element" or "AND them across `call_tool` calls".** Both unsafe. The first-element approach loses AND; the loop-`call_tool` approach dispatches the side-effectful tool on the first SILENT cap, before the next cap is even checked. `check_capabilities` is the correct shape: gate-side resolution with at-most-one user prompt, then a single dispatch via `call_tool` without re-running the gate. Documented in the sharpener-correction comment alongside the SQLite point.
   - **No JS test harness for queue.html.** Sharpener #5 noted the queue HTML inlines all its JS — adding a jest harness for it is more plumbing than the patch is worth. Skipped, as the sharpener recommended.
8. **Headline AC regression** — `cerebral/tests/test_queue_consent_integration.py` (new file, 9 tests):
   - `test_persistent_silent_grant_does_not_bypass_queue_consent` — `acl.set_persistent_class(EXTERNAL_DATA_WRITE, SILENT)` + `check_capabilities("send_message", {"external_data_write"}, CallFlags(passive=True))` → consent surface fires once, returns SILENT (user accepted) → final decision SILENT. The persistent SILENT was defeated; the user got prompted anyway. **This is ADR-0005's ambient-actuation defeat.**
   - `test_session_silent_grant_does_not_bypass_queue_consent` — same regression for `grant_session` instead of `set_persistent_class`.
   - `test_wake_originated_send_dispatches_silently_under_silent_grant` — counterpart: `call_tool("send_message", {...}, capability=EXTERNAL_DATA_WRITE, flags=CallFlags())` (no passive) under the SAME persistent SILENT → plugin's `call_tool` is invoked, consent surface is NOT touched.
   - `test_queue_path_denies_when_consent_refused` — consent returns DENY → `check_capabilities` returns DENY → plugin never invoked.
   - `test_queue_path_with_no_grant_denies_without_prompting` — without ANY grant, passive on the ASK-default `external_data_write` escalates to DENY at ACL resolution time → consent surface doesn't even fire. Full-strength defeat: the user must re-issue via wake to consent in-the-moment. The user is NOT pestered by a passive prompt for an action they never granted.
   - `test_multiple_caps_take_worst_under_passive` — plugin declares two capabilities, both with persistent SILENT grants → passive lifts each from SILENT to ASK → worst-across-set is ASK → consent prompts ONCE (not twice) → user accepts → final SILENT.
   - `test_risky_send_is_queueable_and_flagged` — `risky` flag propagates through `to_dict()` to the IPC payload; risky items are approvable like any other (badge is visibility, not a gate).
   - `test_approve_item_handler_dispatches_tool_under_silent_grant` — end-to-end through the real `cerebral.main._handle_message`: persistent SILENT + auto-allow consent → plugin's `call_tool` invoked exactly once with the right args, `queue_item_result` broadcast with `is_error=False`. **This test caught the double-prompt bug** (see process note below) — only the full handler path under a fake consent that asserts on `len(received)` surfaces it.
   - `test_approve_item_handler_refuses_when_consent_denied` — same handler path with consent returning DENY → plugin NEVER invoked, `queue_item_result` carries `is_error=True` and "deny" in the message.
9. **Test count after #52:** 1460 Python passing (was 1407 → +53: 24 risky_verbs + 6 queue extension + 14 check_capabilities + 9 integration; 3 integration skipped) + 167 JS passing (unchanged — queue.html has no JS test harness, sharpener #5).
10. **What this slice intentionally leaves to follow-up issues** — voice consent via Vosk constrained grammar `["yes","no","later"]` (#50) is independent; the queue admission overlay doesn't touch the audio path. That's the only remaining `ready-for-agent` issue in the security-model arc.
11. **Three process notes worth recording.**
    - **The double-prompt bug.** First wiring of `approve_item` passed both a representative capability AND `flags=CallFlags(passive=True)` into `call_tool`. The end-to-end IPC test `test_approve_item_handler_dispatches_tool_under_silent_grant` caught it: `len(consent.received) == 2` (once via `check_capabilities`, once via `call_tool`'s own gate). Unit-level tests of each method behaved correctly in isolation — the bug only surfaces when both run on the same logical call. Fix: `check_capabilities` is authoritative for the queue path; `call_tool` dispatches with `capability=None, flags=None`. Logged to `.learnings/LEARNINGS.md` under "Pre-flight gate methods + call_tool: dispatch without re-checking".
    - **Test asymmetry between unit + integration is the right kind.** The 14 unit tests for `check_capabilities` cover its internal contract (AND semantics, single prompt across multiple caps, plugin never invoked). The 9 integration tests cover the orchestrator + ACL + consent composition AND the IPC handler path. The double-prompt bug fell exactly in the integration gap — could only be caught by a test that exercises the full call site, not the method in isolation. Worth doing both; cheaper to add integration tests for handler glue than to over-mock the unit tests.
    - **Sharpener corrections, single combined comment.** Both sharpener errors (#6 SQLite vs RAM, #4 unsafe `call_tool` loop) were corrected in ONE comment on the issue rather than two. Less noise, the precedent chain is the same, and the implementation choices that follow from each correction live in the same place. Five-for-five now (#47/#48/#51/#53/#52) on sharpener-pin / live-code conflicts.
