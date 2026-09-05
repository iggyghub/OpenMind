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

## Build rules (ADR-0028) -- these govern everything built here

Six rules, canonical text in `docs/adr/0028-reach-ladder-and-capability-rules.md`.

1. **Reach ladder -- stop at the first rung that reaches.** existing tool -> API
   plugin -> sandboxed shell -> browser session -> computer-use -> ask the human.
   Each descent costs ~10x tokens and ~10x flakiness. Never build a rung while a
   lower one already reaches. "Ask the human" is a rung, not a failure.
2. **Promote on the third repeat.** Ad hoc twice is fine; the third repeat earns a
   plugin. No speculative pre-building.
3. **Mechanism follows what is missing.** know-how -> Skill; a tool -> growth loop;
   replay of a known chain -> Recipe; Felix's own core -> `self_dev`; context room
   -> `delegate`. Not interchangeable.
4. **The ADR-0005 gate is the only permission model.** Every mechanism routes
   through the same 16-class gate, or it does not ship. No side doors.
5. **One GPU is the scheduler.** Nothing assumes concurrency; two local sub-agents
   serialize on the one 1080. The live conversational turn wins contention.
6. **Verified running, or it did not ship.** Green tests are necessary, not
   sufficient -- exercise the real surface (live Cerebral over the IPC bridge, the
   actual browser, the actual tray) before calling it done.

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

3. **Never spawn powershell.exe from Node/Electron with `detached: true`.** On this box, PowerShell 5.1 started under `DETACHED_PROCESS` exits 0 **without executing the `-File` script** — no error, no output, pid returned (bit us in #519: "Restart Felix" silently never rebooted Cerebral). Use `{ stdio: 'ignore', windowsHide: true }` instead; verified to work from both Node and Electron. The same silent-no-op reproduces from a plain PowerShell `Start-Process powershell -ArgumentList '-File',...` wrapper too (confirmed 2026-08-31 running `scripts/launch-felix.ps1` this way from a Claude Code session) — exit code 0, zero new `launcher.log` lines. Invoke a launcher script directly (`& .\scripts\launch-felix.ps1`, no `Start-Process` wrapper) instead; that reliably runs the body.

## Cerebral's real logs are at the repo root, not `.claude/tmp/`

`cerebral/main.py`'s own `logging.basicConfig` only writes to stdout — no `FileHandler` in the Python code. The actual persistence happens one layer up: `scripts/launch-felix.ps1` (the path both `restart_felix` and the tray's respawn button run) spawns Cerebral via `Start-Process -RedirectStandardOutput cerebral.log -RedirectStandardError cerebral.err.log`, both at the **repo root**. When diagnosing any live Cerebral issue, check those two files first — `Glob **/*.log` will bury them under `.claude/tmp/`'s slice-loop noise, and grepping the Python source for a `FileHandler` will (correctly, but misleadingly) turn up nothing. `launcher.log` (also repo root) covers the launch/respawn sequence itself, separate from `cerebral.err.log`'s runtime tracebacks.

**`Start-Process -RedirectStandardOutput`/`-RedirectStandardError` overwrites the target file on every launch — it does not append.** A `restart_felix` mid-diagnosis destroys the only evidence of what just crashed. Copy `cerebral.err.log` aside before restarting anything you're actively debugging.
