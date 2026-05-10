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

#### Run OpenClaw alongside Cerebral

OpenClaw listens on `http://localhost:3000` by default. Start it in a third terminal:

```bash
openclaw start
```

Cerebral connects to OpenClaw at startup. If OpenClaw is not running, the
channel bridge logs a warning and Cerebral keeps running — voice still works.

#### Configure a Telegram channel (recommended first channel)

Telegram is the easiest channel to bring up: bots are free, BotFather is the
only step, and there's no business-account approval like WhatsApp.

1. **Create a Telegram bot** via [BotFather](https://t.me/BotFather):
   - `/newbot` → choose a display name (e.g. *Felix*) and a username
     (e.g. *felix_openmind_bot*).
   - BotFather replies with an **HTTP API token**. Keep it secret.

2. **Add the bot to your OpenClaw config**. Open `~/.openclaw/openclaw.json`
   (Windows: `%USERPROFILE%\.openclaw\openclaw.json`) and add:

   ```json
   {
     "channels": {
       "telegram": {
         "enabled": true,
         "botToken": "PASTE_TOKEN_FROM_BOTFATHER",
         "longPolling": true
       }
     },
     "agents": [
       {
         "name": "felix",
         "default": true,
         "endpoint": {
           "type": "websocket",
           "wsUrl": "ws://localhost:3000/agent/stream",
           "replyUrl": "http://localhost:3000/agent/reply"
         }
       }
     ]
   }
   ```

   The `wsUrl` and `replyUrl` are what OpenClaw exposes for external agent
   clients. Cerebral connects to `wsUrl` to receive inbound messages and
   POSTs replies to `replyUrl`. These match Cerebral's defaults — change
   them only if you've reconfigured OpenClaw to listen elsewhere.

3. **(Optional) Set environment variables for Cerebral** if you've moved
   OpenClaw off `localhost:3000` or enabled a shared secret:

   ```bash
   # .env or shell profile
   OPENCLAW_WS_URL=ws://localhost:3000/agent/stream
   OPENCLAW_REPLY_URL=http://localhost:3000/agent/reply
   OPENCLAW_API_KEY=optional-shared-secret
   ```

4. **Restart OpenClaw and Cerebral.** In your Cerebral terminal you should
   see `[bridge] Connected to OpenClaw at ws://localhost:3000/agent/stream`.

5. **Test it.** Open Telegram, find your bot, send `Felix, what time is it?`
   — Felix should reply through the channel within a few seconds.

#### WhatsApp / Discord / Slack

Same pattern: enable the channel in `~/.openclaw/openclaw.json`, supply the
provider's credentials (Discord bot token, Slack app token, etc.), and
Cerebral picks up messages from those channels through the same bridge —
no Cerebral changes required. See OpenClaw's per-channel docs at
[docs.openclaw.ai/channels](https://docs.openclaw.ai/channels) for the
channel-specific keys.

### n8n (workflow automation)

n8n is the integration harness for cloud services (Google Workspace, Zoom, GitHub API, etc.). Felix triggers n8n workflows via the `n8n` MCP plugin.

#### Option A — npm (simplest, runs in foreground)

```bash
npm install -g n8n
n8n start
```

n8n opens at http://localhost:5678. Create a free local account on first run (no n8n Cloud account required).

#### Option B — Docker (recommended for always-on background service)

```bash
docker run -d \
  --name n8n \
  --restart unless-stopped \
  -p 5678:5678 \
  -v ~/.n8n:/home/node/.n8n \
  n8nio/n8n
```

Data is stored in `~/.n8n` (Windows: `%USERPROFILE%\.n8n`).

#### Environment variable

Set your n8n API key so Felix can authenticate. Generate one in n8n → Settings → API → Create API Key, then:

```bash
# .env or shell profile
N8N_API_KEY=your-n8n-api-key
```

If `N8N_API_KEY` is unset, the plugin uses the default `changeme` — fine for local dev with no auth configured.

#### Verify

```bash
curl http://localhost:5678/healthz
# → {"status":"ok"}
```

The `n8n` plugin auto-registers via `discover_plugins()` — no changes to `main.py` required.

### n8n credentials (Google OAuth + Zoom) — one-time setup

After n8n is running, connect your cloud accounts so Felix can call Google Workspace and Zoom on your behalf. This is a human-in-the-loop step — OAuth flows require browser clicks.

Full instructions: **[docs/setup/n8n-credentials.md](docs/setup/n8n-credentials.md)**

Verify credentials are active at any time:

```bash
python scripts/n8n_check_credentials.py
# → All required credentials are configured.
```

### n8n workflows — required workflow names

Felix's plugins trigger n8n workflows by **exact name**. Create these in
your n8n instance (the Google ones reuse the OAuth credential from the
step above; the Zoom ones reuse the Zoom credential):

| Workflow name             | Used by                       |
|---------------------------|-------------------------------|
| `Felix Gmail Send`        | google_workspace plugin (#20) |
| `Felix Gmail Search`      | google_workspace plugin (#20) |
| `Felix Calendar Create`   | google_workspace + meet (#20/#23) |
| `Felix Calendar List`     | google_workspace + meet (#20/#23) |
| `Felix Drive List Files`  | google_workspace plugin (#20) |
| `Felix Drive Upload`      | google_workspace plugin (#20) |
| `Felix Sheets Read`       | google_workspace plugin (#20) |
| `Felix Sheets Write`      | google_workspace plugin (#20) |
| `Felix Zoom Join`         | zoom plugin (#23) |
| `Felix Zoom Schedule`     | zoom plugin (#23) |
| `Felix Zoom List`         | zoom plugin (#23) |
| `Felix GitHub List Issues`  | github plugin (#24) |
| `Felix GitHub Create Issue` | github plugin (#24) |
| `Felix GitHub List PRs`     | github plugin (#24) |
| `Felix GitHub Notifications`| github plugin (#24) |

If a workflow name doesn't match exactly, the plugin returns
`Workflow not found: '...'` — recheck spelling and capitalisation in n8n.

The GitHub workflows authenticate via a stored n8n GitHub credential —
create a Personal Access Token at <https://github.com/settings/tokens> with
the `repo` and `notifications` scopes, then add it as a GitHub credential
in n8n (Settings → Credentials → New → GitHub).

### Zoom desktop client (optional)

The Zoom plugin's `zoom_join_meeting` tool launches the local Zoom client
via the `zoommtg://` URL scheme that the client registers on install.
**You only need the Zoom desktop client installed if you actually use
join.** Scheduling and listing meetings hit the Zoom REST API via n8n
and work without the client.

Download: <https://zoom.us/download>

### Dev tools — local CLI binaries (#24)

The Git, Docker, SSH, and package-manager plugins shell out to the local
CLIs and return `is_error=True` if the binary is missing from PATH. Make
sure each of these is on your PATH if you want the corresponding tools to
work:

- `git` — needed by the `git` plugin (`git_status`, `git_commit`, …).
- `docker` — needed by the `docker` plugin (`docker_list_containers`, …).
- `ssh` — needed by the `ssh` plugin (`ssh_run_command`).
- `npm`, `pip`, `winget` — needed by the `package_manager` plugin
  (`pkg_install`, `pkg_update`, `pkg_search`). Each manager is invoked
  only when explicitly named in the call.

The `github` and `http_client` plugins make pure HTTP calls and don't need
any extra binaries (only n8n for `github`).

### Information plugins — public APIs (#25)

The Wikipedia, Weather, News, and Markets plugins all hit free public
endpoints — **no API keys, no extra binaries, just internet**:

- `wikipedia` — Wikipedia REST + action API.
- `weather` — Open-Meteo (`api.open-meteo.com`, `geocoding-api.open-meteo.com`).
- `news` — RSS aggregation (BBC, Reuters, Hacker News by default).
  Requires the `feedparser` package, which is in `cerebral/requirements.txt`
  — `pip install -r requirements.txt` picks it up.
- `markets` — CoinGecko for crypto, Yahoo Finance public chart endpoint
  for stocks.

### Security plugins (#26)

The Bitwarden, VPN, and Network Scanner plugins shell out to local OS
binaries and require some prerequisites:

- `bitwarden` — needs the **Bitwarden CLI** (`bw`) on PATH. Install via
  `npm install -g @bitwarden/cli` (or `winget install Bitwarden.CLI`).
  First-time use: run `bw login` once at the terminal to authenticate the
  CLI to your account; from then on Felix prompts for the master password
  per session via `bw_unlock` (the password is never stored). The plugin
  is **read-only** — it exposes get/list tools only, never create/edit/delete.
- `vpn` — needs **VPN profiles already configured in the OS network
  settings**. Felix only triggers existing profiles; it never creates
  them. Configure profiles via:
  - Windows: Settings → Network & Internet → VPN → Add VPN
  - macOS: System Settings → Network → VPN → Add VPN configuration
  - Linux: NetworkManager (e.g. `nmcli connection add` or the GUI)

  The plugin shells out to `rasdial` / `scutil` / `nmcli` — these are
  built-in to each OS, no extra install required.
- `network_scanner` — uses `arp` and `ping`, both standard on every
  supported OS. `net_check_port` opens a plain TCP socket via Python's
  stdlib `socket` module — no binary required.

### Hardware plugins (#27)

The Printer/Scanner and Steam plugins are auto-loading and require some
prerequisites depending on platform:

- `printer` — POSIX needs `lp` / `lpstat` / `scanimage` on PATH (CUPS for
  printing, SANE for scanning). Install with the platform package manager
  (e.g. `sudo apt install cups sane-utils` on Debian/Ubuntu, `brew install
  cups sane-backends` on macOS). Windows uses built-in PowerShell cmdlets
  (`Start-Process -Verb Print`, `Out-Printer`, `Get-PrintJob`,
  `Get-Printer`) — no extra install required. **Windows scanning is not
  supported** — `scan_document` returns a documented stub-error pointing
  to Windows Fax & Scan rather than half-implementing a fragile WIA COM
  bridge.
- `steam` — needs Steam installed at the default location for your
  platform: `C:\Program Files (x86)\Steam` (Windows), `~/Library/
  Application Support/Steam` (macOS), or `~/.steam/steam` /
  `~/.local/share/Steam` (Linux). If you've installed Steam elsewhere,
  pass a custom `steam_root` when calling `plugins.steam.create()` or
  symlink the default path. The plugin reads `appmanifest_*.acf` files
  directly — no Steam CLI required. Launching uses the
  `steam://rungameid/<appid>` URL scheme registered by the Steam client
  on install.
