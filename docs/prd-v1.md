## Problem Statement

Managing a modern computer requires switching between dozens of disconnected tools — browser, calendar, email, files, terminal, messaging apps, music, smart home controls — with no single unified interface that understands context and intent. Voice assistants exist, but they either require cloud connectivity, lack computer-level access, or cannot be meaningfully extended. The user must remember which app does what, manually copy information between tools, and repeat themselves across contexts. There is no system that passively learns the shape of a day and surfaces useful actions at the right moment — without interrupting.

## Solution

OpenMind is a local-first personal AI agent platform. Its assistant, **Felix**, runs entirely on the user's desktop (the **Cerebral** backend), listens passively in the background, and executes actions on the computer using a suite of MCP tool servers. Felix wakes on its name, awaits a command, and acts — or silently queues candidate actions detected during passive listening for the user to approve. Every component is open source and modifiable. Felix can extend itself: when a tool does not exist, the user describes it and Felix builds a new MCP server on the spot.

## User Stories

1. As a user, I want Felix to listen passively in the background so that it can detect useful context without interrupting my flow.
2. As a user, I want to say "Felix" to wake the assistant so that I can give a command without touching a keyboard or mouse.
3. As a user, I want my wake name to be configurable per profile so that I can personalise what I call my assistant.
4. As a user, I want Felix to await my command after waking without reading out a queue or interrupting so that I stay in control of the interaction.
5. As a user, I want candidate actions detected during passive listening to appear in a tray pulldown queue so that I can review and act on them at my own pace.
6. As a user, I want to approve or dismiss queued actions from the tray pulldown so that Felix never acts without my intent.
7. As a user, I want periodic reminders (configurable interval) when there are pending items in the queue so that I do not miss useful suggestions.
8. As a user, I want an opt-in notification system for queue activity so that I can choose whether to be alerted or check the tray manually.
9. As a user, I want Felix to decompose my spoken command into tasks and execute them via available tools so that I can express intent naturally rather than memorising commands.
10. As a user, I want Felix to select the best available tool for each task automatically so that I do not need to know which MCP server handles what.
11. As a user, I want Felix to use a local model (Ollama / Gemma 4) by default so that my data stays on my machine and I can work offline.
12. As a user, I want to switch between local and cloud models (Claude) per task type so that I can use the best model for complex tasks when online.
13. As a user, I want to browse and switch between available models from the UI so that I can experiment as the model landscape evolves.
14. As a user, I want Felix to speak responses using Kokoro TTS with configurable voices so that the interaction feels natural and personal.
15. As a user, I want to select different voices per profile so that each person's Felix sounds distinct.
16. As a user, I want a system tray icon as Felix's primary presence so that it stays out of my way while remaining accessible.
17. As a user, I want a dark animated abstract visualiser (orb/waveform) as an optional on-screen presence so that Felix has a visual identity when I want it.
18. As a user, I want the visualiser to react to Felix's state (passive, active, speaking, thinking) so that I can see what Felix is doing at a glance.
19. As a user, I want a profile selected at launch (or auto-detected after first use) so that Felix knows who it is talking to from the start.
20. As a user, I want each profile to store my name, preferred voice, wake name override, pronunciation guide, and connected accounts so that Felix adapts to me specifically.
21. As a user, I want Felix's long-term memory to be scoped per profile so that different people's memories do not bleed into each other.
22. As a user, I want ambient context (location from GPS/IP, activity from camera) to feed into Felix's short-term understanding so that suggestions are situationally aware.
23. As a user, I want the rolling audio buffer to stay in RAM and never be written to disk so that passive listening does not create a surveillance log.
24. As a user, I want Felix to learn my preferences passively from my approvals and dismissals so that it improves without me rating things explicitly.
25. As a user, I want to view Felix's learned model of me in an Insights view so that I can see what it has inferred about my habits and preferences.
26. As a user, I want to edit, delete, or pin any entry in the Insights view so that I have full control over what Felix knows about me.
27. As a user, I want to describe a new capability in plain language and have Felix generate, install, and register a new MCP server so that I can extend the system without writing code.
28. As a user, I want generated plugin code to be placed in a readable /plugins directory so that I can inspect and modify it.
29. As a user, I want Felix to have full access to my computer (files, apps, browser, terminal, system settings) so that it can complete any task I could do myself.
30. As a user, I want Felix to work fully offline using local models and open-source tool fallbacks so that I am never dependent on internet connectivity.
31. As a user, I want Google Workspace (Gmail, Calendar, Drive, Docs, Sheets, Slides, Contacts, Maps, Tasks) integrated so that Felix can read and act on my primary productivity suite.
32. As a user, I want local OSS fallbacks (Grist, IMAP/SMTP, Nextcloud, LibreOffice, OpenStreetMap) for every Google service so that Felix stays useful when offline.
33. As a user, I want Git, GitHub, Docker, package managers, SSH, and an HTTP client integrated so that Felix can assist with my development workflow.
34. As a user, I want Wikipedia, weather (Open-Meteo), news, and stock/crypto prices integrated so that Felix can answer factual and current information questions.
35. As a user, I want Bitwarden (read-only local vault), VPN control, and a network scanner integrated so that Felix can assist with security and network tasks.
36. As a user, I want printer and scanner control integrated so that Felix can manage physical document workflows.
37. As a user, I want Steam game launching integrated so that I can ask Felix to start a game without finding the launcher.
38. As a user, I want OCR-powered invoice and receipt extraction to Google Sheets or Grist so that Felix can digitise paper-based financial records.
39. As a user, I want Zoom and Google Meet integration so that Felix can join, schedule, and manage video calls.
40. As a user, I want phone call capability via OpenClaw channels so that Felix can make and receive calls through connected messaging providers.
41. As a user, I want to reach Felix from my phone via WhatsApp, Telegram, or any OpenClaw-supported channel so that I have remote access before a native mobile client exists.
42. As a user, I want OpenClaw to handle all external messaging channels through a single harness so that Felix does not need per-service messaging code.
43. As a user, I want the system to be fully open source so that I can modify any component to better suit my needs.
44. As a user, I want the integration layer to be service-agnostic via MCP so that I can swap Google for an open-source alternative without changing Felix's behaviour.
45. As a user, I want to eventually use Felix on my phone and smartglasses, connecting back to Cerebral as the central brain, so that the assistant travels with me.

## Implementation Decisions

### Architecture
- Cerebral is the Python backend process. It owns the AI pipeline, memory, MCP orchestration, and action execution. All other devices connect to it.
- The frontend is a Node.js + web process owning the system tray, dark UI, animated visualiser, and device communication. It communicates with Cerebral over a local IPC/WebSocket bridge.
- See ADR-0002.

### Core modules (Python backend)
- audio_pipeline: Vosk passive listening, 60-second rolling RAM buffer (never persisted), faster-whisper triggered transcription, wake detection and routing.
- intent_engine: 5W1H extraction from transcribed text; command to task decomposition; LLM-powered tool selection.
- model_router: Routes task completions to Ollama/Gemma 4 (local) or Claude (cloud) via OpenClaw inference layer; supports runtime model switching.
- mcp_orchestrator: Plugin registry; discovers and loads MCP servers; routes LLM tool calls to the correct server. See ADR-0001.
- queue_manager: Holds passive-detected candidate actions; manages approval, dismissal, and reminder scheduling.
- memory_manager: Three-tier memory: RAM rolling buffer (short-term), ChromaDB vector store (long-term, per-profile), SQLite (structured, per-profile).
- profile_manager: Profile CRUD backed by SQLite; launch-time selection; memory scoping per identity.
- tts_engine: Kokoro local TTS; per-profile voice selection.
- plugin_builder: Natural language to generated MCP server code, auto-install, test, register in orchestrator; output to /plugins.
- insights_engine: Passive preference learning from approval/dismissal signals; pattern detection; Insights view data provider with full CRUD.
- env_context: Camera and GPS/IP inputs to location and activity inference; feeds short-term memory.

### Frontend modules (Node.js)
- tray_app: System tray icon, queue pulldown, OS notification dispatch, reminder scheduling.
- visualiser: Dark animated abstract orb/waveform rendered in a web overlay; state-reactive (passive / active / speaking / thinking).
- ipc_bridge: WebSocket protocol between Python backend and Node.js frontend.

### MCP servers
- Tool protocol: All capabilities exposed via MCP. See ADR-0001.
- Harness: OpenClaw handles model providers and all messaging channels. See ADR-0003.
- Day 1 servers (28 total): Clock, Scheduler, Files, Apps, Clipboard, Notes, System, Shell, Gmail, Google Calendar, Google Drive, Google Docs, Google Sheets, Google Slides, Google Contacts, Google Maps, Google Tasks, Git, GitHub/GitLab, Docker, Package Managers, SSH, HTTP Client, Wikipedia, Weather (Open-Meteo), News, Stocks/Crypto, Bitwarden (read-only), VPN, Network Scanner, Printer/Scanner, Game Launcher (Steam), Invoice/Receipt OCR, Zoom/Google Meet, Phone Calls.
- All Google services have defined local OSS fallbacks for offline use.
- Second wave servers added via growth loop: Notion, Obsidian, Todoist, Time Tracker, YouTube, Reddit, Twitter/X, RSS, Sports Scores, GIMP/Darktable, Blender, Figma, FFmpeg, Home Assistant, Dropbox/OneDrive. See ADR-0004.
- Growth loop: describe missing tool in natural language, plugin_builder generates MCP server, registers automatically.

### Model layer
- Default local: Ollama / Gemma 4
- Default cloud: Claude (Anthropic)
- Routing via OpenClaw inference (supports 30+ providers)
- Model switching available at runtime per task type

### Profiles
- Identity containers only, not settings panels.
- Store: name, preferred voice, wake name and pronunciation guide, connected accounts, long-term memory scope.
- Selected at launch or auto-detected after first use.
- Security model deferred to a future PRD.

## Testing Decisions

### What makes a good test
Tests verify external behaviour only: inputs and outputs of a module's public interface. No testing of internal implementation details or private methods. Tests run without internet, without hardware (mic, camera, printer), and without a live Ollama or Kokoro instance (use mocks/stubs).

### Modules to test
- audio_pipeline: Wake detection, buffer rollover, and transcription trigger logic with synthetic audio chunks and mock Vosk/faster-whisper interfaces.
- intent_engine: 5W1H extraction and task decomposition with fixed transcription strings and a mock LLM; verify output schema.
- model_router: Routing logic (local vs cloud selection), model switching, and fallback behaviour with mock OpenClaw inference responses.
- mcp_orchestrator: Server registration, tool discovery, and tool call routing with mock MCP servers; verify the correct server is called for each tool name.
- queue_manager: Enqueue, approve, dismiss, pending list, and reminder trigger logic with in-memory state.
- memory_manager: Remember/recall/forget across all three tiers with an in-memory ChromaDB instance and an in-memory SQLite database.
- profile_manager: Profile CRUD, active profile selection, and memory scoping with a test SQLite database.
- plugin_builder: Generate-to-register pipeline with a mock LLM and mock MCP server registry; verify generated server passes a basic tool-call test.

### Modules not unit tested at this stage
- tts_engine: hardware-dependent (Kokoro); covered by integration test only.
- env_context: hardware-dependent (camera, GPS); covered by integration test only.
- tray_app, visualiser: UI/OS-dependent; covered by manual testing.
- ipc_bridge: covered by end-to-end integration test.

## Out of Scope

- Security model and per-profile permissions (future PRD)
- Native mobile client (OpenClaw remote access bridges this for now)
- Smartglasses client
- 2D / 3D character themes (abstract visualiser ships first; themes are a future PRD)
- Multi-user / shared household profile management
- Visual plugin builder (natural language builder ships first)
- Second wave MCP servers: Notion, Obsidian, Todoist, YouTube, Reddit, Twitter/X, RSS, GIMP, Blender, Figma, FFmpeg, Home Assistant, Dropbox/OneDrive
- Health integrations (Fitbit, Garmin, Google Fit)
- Sports scores integration

## Further Notes

- OpenClaw's qqbot plugin has been disabled due to a Windows file-lock bug. Not blocking.
- The wake name "Felix" was chosen for phonetic distinctiveness and reliable Vosk detection. The pronunciation guide field in each profile allows Vosk to be tuned to alternate wake names.
- All Google Workspace MCP servers must implement the same abstract interface as their OSS fallbacks so that Felix's behaviour is identical whether online or offline.
- The /plugins directory is the source of truth for the plugin registry. Adding a file registers a server; removing it deregisters it.
- Model selection treats the local model as the default and cloud as opt-in to avoid unexpected data egress.
