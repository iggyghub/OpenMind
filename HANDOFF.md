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
| `switch_model` | `{model_id}` | change active LLM (e.g. `"claude/haiku"`) |
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
| ... | (29 total) | |

**Next issue: #30.** Read it with `gh issue view 30 --repo iggyghub/OpenMind` before implementing.

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

## Key constraints to carry forward

- **Local first** — every feature must work offline. Cloud (Claude, Google) is enhancement, not requirement.
- **No disk writes for audio** — the rolling buffer is RAM-only. Never change this.
- **IPC is the only channel** — Cerebral and tray talk only through the WebSocket bridge. No shared files, no direct imports across the boundary.
- **Audio pipeline is optional at start** — Cerebral runs cleanly without the Vosk model. Keep this graceful fallback.
- **TTS is optional at start** — Cerebral runs cleanly without kokoro. Same graceful pattern.
- **Wake name is user-configurable** — don't hardcode "felix" in new code. Always read from the active profile.
- **Profile schema has migrations** — the `_init_schema` method uses `ALTER TABLE ... ADD COLUMN` with `try/except` to add new columns to existing DBs. Follow this pattern for any new columns.
- **Voice ID default is `af_heart`** — the Kokoro American English female "Heart" voice. Profile records created before #5 may have `voice_id="default"` — the TTS engine falls back to `af_heart` if the id is unrecognised.
