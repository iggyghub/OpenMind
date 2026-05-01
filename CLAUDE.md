# OpenMind

OpenMind is a local-first personal AI agent platform. The user speaks to **Felix** (the assistant's wake name), an LLM decomposes the intent into tasks, and MCP tools execute them. The central brain process is called **Cerebral**. See `CONTEXT.md` for full domain language and architecture.

## Stack

- **Backend:** Python (AI pipeline, memory, MCP execution)
- **Frontend:** Node.js + web (system tray, dark UI, animated visualiser)
- **Local LLM:** Ollama / Gemma 4
- **Cloud LLM:** Claude (Anthropic) via OpenClaw
- **STT:** Vosk (always-on) + faster-whisper (active)
- **TTS:** Kokoro (local)
- **Memory:** RAM buffer + ChromaDB (vector) + SQLite (structured)
- **Tool protocol:** MCP (Model Context Protocol)
- **Harness:** OpenClaw (messaging + remote access gateway)

## Repo layout

> To be filled in as the project is scaffolded.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for this repo. See `docs/agents/issue-tracker.md`.

### Triage labels

Using canonical defaults: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context repo — one `CONTEXT.md` + `docs/adr/` at the root. See `docs/agents/domain.md`.
