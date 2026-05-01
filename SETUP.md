# OpenMind — Setup

## Prerequisites

- Node.js 24+ (or 22.14+)
- npm 11+
- Git

## Installation Steps

### 1. Claude Code (global CLI)

```bash
npm install -g @anthropic-ai/claude-code
```

Verify: `claude --version`

### 2. Matt Pocock's Skills

```bash
npx skills@latest add mattpocock/skills --agent "claude-code" --skill "*" --yes
```

Skills are installed to `.claude/skills/` and `.claude/commands/`.

Available skills: `caveman`, `diagnose`, `grill-me`, `grill-with-docs`, `improve-codebase-architecture`, `setup-matt-pocock-skills`, `tdd`, `to-issues`, `to-prd`, `triage`, `write-a-skill`, `zoom-out`.

Run `/setup-matt-pocock-skills` in Claude Code after install to configure the issue tracker, triage labels, and domain docs.

### 3. OpenClaw

```bash
npm install -g openclaw@latest
```

Verify: `openclaw --version`

OpenClaw is a multi-channel AI gateway (WhatsApp, Telegram, Slack, Discord, Teams, voice/video). Docs: https://github.com/openclaw/openclaw
