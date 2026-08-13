# OpenMind

OpenMind is a local-first personal AI agent platform. The user speaks to **Felix** (the assistant's wake name), an LLM decomposes the intent into tasks, and MCP tools execute them. The central brain process is called **Cerebral**. See `CONTEXT.md` for full domain language and architecture.

## Stack

- **Backend:** Python (AI pipeline, memory, MCP execution)
- **Frontend:** Node.js + web (system tray, dark UI, animated visualiser)
- **Local LLM:** Ollama / Qwen (`qwen3:8b` on the 8GB GTX 1080; the sole kept local model)
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

## Operator scripts (PowerShell)

Setup / verify / diagnostic scripts under `scripts/*.ps1` are run by the end user on Windows, often by double-clicking from Explorer. Two non-obvious gotchas (both hit during the #162 verification — see `.learnings/LEARNINGS.md`):

1. **ASCII-only script bodies.** Windows PowerShell 5.1 reads `.ps1` files in the ANSI codepage when no BOM is present. UTF-8 em-dashes (`—`), smart quotes, etc. in **string literals** get mojibake'd into characters that break the parser. Comments are usually safe but keep them ASCII too for consistency. Sanity-check with `Grep "[^\x00-\x7F]"` before pushing.
2. **Pause on exit.** Double-click invocation spawns a transient `powershell.exe -File ...` console that closes the instant the script returns, hiding all output. Wrap the body in `try { ... } catch { ... } finally { Read-Host "Press Enter to close" \| Out-Null }`. The `finally` runs even when `exit` is called inside `try`. Print explicit `SUCCESS` / `FAILED` markers in colour at the end so the user sees a clear outcome.

Doesn't apply to scripts meant only for CI / chaining — those need a clean exit code with no prompt. If a script serves both audiences, use a `-NoPause` switch.

3. **Never spawn powershell.exe from Node/Electron with `detached: true`.** On this box, PowerShell 5.1 started under `DETACHED_PROCESS` exits 0 **without executing the `-File` script** — no error, no output, pid returned (bit us in #519: "Restart Felix" silently never rebooted Cerebral). Use `{ stdio: 'ignore', windowsHide: true }` instead; verified to work from both Node and Electron.
