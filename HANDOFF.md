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
│   ├── main.py            ← entry point; WS server + audio + profile + TTS
│   ├── requirements.txt   ← websockets, vosk, faster-whisper, sounddevice, numpy, kokoro, soundfile
│   ├── audio/
│   │   ├── __init__.py
│   │   ├── pipeline.py    ← Vosk passive listener + faster-whisper
│   │   └── rolling_buffer.py
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
| ... | (29 total) | |

**Next issue: #18.** Read it with `gh issue view 18 --repo iggyghub/OpenMind` before implementing.

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
