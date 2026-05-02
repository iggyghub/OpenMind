# OpenMind — Setup

## Prerequisites

- Python 3.10+
- Node.js 24+ (or 22.14+)
- npm 11+
- Git

---

## Dev environment start sequence

OpenMind runs as two cooperating processes. Start them in order:

### 1. Cerebral (Python backend)

```bash
cd cerebral
pip install -r requirements.txt
python scripts/download_models.py   # downloads Vosk small EN model (~40 MB) — one time only
python main.py
```

Expected output:
```
[cerebral] Starting on ws://localhost:7766
[cerebral] Listening — waiting for tray connection
```

Leave this terminal running. Cerebral exposes a WebSocket server on `ws://localhost:7766` that the tray connects to.

### 2. Felix tray (Node.js frontend)

In a second terminal:

```bash
cd tray
npm install       # downloads Electron + deps, generates assets/icon.png
npm start
```

Expected output:
```
[icon] Created ...\assets\icon.png (32x32)
[tray] Felix tray started
[tray] Connected to Cerebral
```

A system tray icon (purple circle) appears in the taskbar notification area. Right-click it to see the status or quit.

### Verify IPC is working

Cerebral sends a heartbeat event to the tray every 5 seconds. You should see lines like this in the tray terminal:

```
[tray] Event received: {"type":"heartbeat","data":{"status":"running"}}
```

### Shutting down

Right-click the tray icon → **Quit**. This sends a shutdown signal to Cerebral; both processes exit cleanly.

Alternatively, `Ctrl+C` in the Cerebral terminal kills the backend; the tray will display "connecting…" and keep retrying.

---

## Tooling setup (Claude Code)

### Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
```

Verify: `claude --version`

### Matt Pocock's Skills

```bash
npx skills@latest add mattpocock/skills --agent "claude-code" --skill "*" --yes
```

Skills are installed to `.claude/skills/` and `.claude/commands/`.

Available skills: `caveman`, `diagnose`, `grill-me`, `grill-with-docs`, `improve-codebase-architecture`, `setup-matt-pocock-skills`, `tdd`, `to-issues`, `to-prd`, `triage`, `write-a-skill`, `zoom-out`.

Run `/setup-matt-pocock-skills` in Claude Code after install to configure the issue tracker, triage labels, and domain docs.

### OpenClaw

```bash
npm install -g openclaw@latest
```

Verify: `openclaw --version`

OpenClaw is a multi-channel AI gateway (WhatsApp, Telegram, Slack, Discord, Teams, voice/video). Docs: https://github.com/openclaw/openclaw
