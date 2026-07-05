# OpenMind

**Local-first personal AI agent platform.** Felix hears you, understands you, and acts on your computer.

OpenMind is a voice-driven AI assistant that runs on your own machine. You speak a wake word, Felix transcribes what you said, an LLM plans the action, and modular tools carry it out — set a timer, search the web, send an email, manage files, run a workflow. It works fully offline with local models and uses cloud models only when they add value.

> **Status:** Active solo development, pre-v1. The goal for v1 is daily-driver stability for the author, not a public release. 340+ commits and counting. This repo is the real, working codebase — architecture decisions, issues, and fix logs included.

---

## Why it exists

Commercial assistants are cloud-locked black boxes. OpenMind takes the opposite position:

- **Local first, cloud fallback.** Runs fully offline on local models (Ollama). Cloud LLMs (Anthropic Claude) enhance when available, never required.
- **Open and inspectable throughout.** Every component has an accessible, modifiable codebase. Every tool Felix uses is readable code.
- **Passive by default.** Felix never interrupts. It listens for its name, queues candidate actions, and waits for approval.
- **Transparent intelligence.** Everything Felix knows and does — memory, learned preferences, pending actions, permissions — is visible and editable in the UI.

## How it works

Two local processes, connected over WebSocket:

```
[Cerebral — Python backend]              [Felix — Electron front-end]
  AI pipeline & model routing              System tray + main window
  Voice: Vosk → faster-whisper → TTS       Chat canvas (voice + text)
  MCP tool execution                       Floating state visualiser
  Memory: vector DB + SQLite               Consent prompts & queue
          └────────── ws://localhost ──────────┘
```

**Voice pipeline:** Vosk runs always-on, lightweight wake-word and signal detection. On wake, faster-whisper does full transcription, the LLM plans, tools execute via MCP, and Kokoro speaks the result — all locally.

**Two-tier model routing:** simple requests go to a local Ollama model (fast, free, private); complex reasoning routes to Claude. Per-task model assignment is user-configurable.

**The core loop:** intent → LLM selects a tool → executes through a permission gate. Multi-step chains let the planner see each result and pick the next tool. Approved chains can be saved as re-runnable "Recipes."

**The growth loop:** when Felix lacks a tool, the gap becomes a new MCP plugin — designed from a natural-language description, registered, and permanently available. The platform ships a core plus the ability to grow, not every feature imaginable.

## Engineering highlights

- **Plugin architecture (MCP).** Every capability — clock, browser, files, email, shell — is a Model Context Protocol server in `/plugins`. Uniform interface for the LLM regardless of what's underneath (direct API, n8n workflow, local tool). Adding a capability = adding a plugin.
- **Multi-tier memory.** A ~60-second RAM rolling buffer that is never written to disk; a local vector database (ChromaDB) for semantically searchable long-term memory; SQLite for structured state (profiles, queue, permissions, transcripts). Raw audio is never persisted.
- **Safety-conscious execution.** Tool calls pass through a permission gate: silent-class actions run friction-free, sensitive ones prompt for consent, irreversible ones require explicit confirmation. Shell commands run inside an OS-level sandbox (Windows AppContainer + Job Object: kernel-ACL-confined writes, no network, scrubbed environment, resource caps). Deny-by-default, fail-closed.
- **Multi-profile identity.** Per-user profiles own their memory, permissions, connected accounts, and voice preferences. System settings stay global; identity stays scoped.
- **Documented decision-making.** Architecture decision records (ADRs), a fix log, and slice-based delivery through issues and PRs — the process is visible in the repo history, not just the end state.

## Stack

| Layer | Technology |
| --- | --- |
| Backend | Python (Cerebral) |
| Front-end | Electron + HTML/CSS/JS (tray, main window, visualiser) |
| Local LLM | Ollama |
| Cloud LLM | Anthropic Claude |
| Wake word / STT | Vosk (always-on) / faster-whisper (full transcription) |
| TTS | Kokoro (local) |
| Long-term memory | ChromaDB (local vector DB) |
| Structured memory | SQLite |
| Tool protocol | MCP (Model Context Protocol) |
| Browser automation | Playwright |
| Workflow automation | n8n (self-hosted) |
| IPC | WebSocket (localhost) |

## Repo map

| Path | What it is |
| --- | --- |
| `cerebral/` | Python backend — AI pipeline, routing, memory, execution |
| `tray/` | Electron front-end — tray, main window, visualiser |
| `plugins/` | MCP tool servers (the unit of capability) |
| `scripts/` | Utilities and management CLIs |
| `docs/` | Architecture decision records and design docs |
| `CONTEXT.md` | Full domain glossary and architecture reference |
| `SETUP.md` | Getting it running |

## About

Built and maintained by **Adam Poder** — self-taught AI automation developer. Sole author of the architecture, backend, front-end, and plugin system.

- GitHub: [github.com/iggyghub](https://github.com/iggyghub)
- Email: iggyphi@gmail.com

*If you're evaluating this project for hiring purposes: `CONTEXT.md` is the deepest single read (full architecture and glossary), `docs/` holds the ADRs, and the issue/PR history shows how features were sliced and shipped.*
