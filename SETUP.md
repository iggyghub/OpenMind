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

From the project root:

```bash
pip install -e .
python -m cerebral.scripts.download_models   # downloads Vosk small EN model (~40 MB) — one time only
python -m cerebral.main
```

`pip install -e .` reads `pyproject.toml` at the project root and installs
the `cerebral` package in editable mode, so subsequent edits take effect
without reinstalling. From then on, anywhere on PATH can run
`python -m cerebral.main`.

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

#### Auto-start the OpenClaw gateway on login (recommended)

OpenClaw 2026.4.29 ships its own native installer for Windows login auto-start.
Run these four commands from any shell (PowerShell or `cmd`):

```powershell
openclaw gateway install
openclaw config set gateway.mode local
openclaw gateway start
openclaw gateway status
```

What each step does:

1. **`openclaw gateway install`** registers a Startup-folder login item at
   `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\OpenClaw Gateway.cmd`,
   generates a per-machine gateway token, and writes config to
   `~/.openclaw/openclaw.json`. **Important:** this **overwrites** any existing
   `openclaw.json`. A `.bak` is saved next to the new file — if you had
   channel credentials (e.g. a Telegram bot token) configured, restore them
   from the `.bak` before the next step.
2. **`openclaw config set gateway.mode local`** sets the gateway to local mode.
   Without this, `gateway start` will refuse to launch. (`openclaw doctor`
   catches this if you skip it.)
3. **`openclaw gateway start`** launches the gateway in the background. After
   login it will be started automatically by the Startup-folder item from step 1.
4. **`openclaw gateway status`** confirms it's running. Expect
   `Runtime: running` and a listener on `127.0.0.1:18789`. Note: status can be
   racy for the first few seconds after `start` — re-check after ~5s if the
   first run shows `stopped`.

To verify the gateway comes back up automatically after a reboot, run:

```powershell
.\scripts\verify-openclaw-running.ps1
```

The script checks that `127.0.0.1:18789` is listening post-boot and prints
PASS/FAIL with evidence to paste into issue #162.

> **Note:** Cerebral's channel bridge does not yet connect to OpenClaw 2026.4.29
> out of the box — the bridge code still defaults to the old port `3000` and
> doesn't send the auto-generated gateway token. That work is tracked in
> [#167](https://github.com/iggyghub/OpenMind/issues/167). Until #167 lands,
> the gateway is up but Cerebral's `[bridge] Connected to OpenClaw` log line
> won't appear unless you override `OPENCLAW_WS_URL` / `OPENCLAW_REPLY_URL` /
> `OPENCLAW_API_KEY` to match your installed OpenClaw.

To remove auto-start: delete `OpenClaw Gateway.cmd` from `shell:startup` by hand,
or use whatever uninstall command OpenClaw exposes in your version
(`openclaw gateway --help`).

Cerebral itself is **not** auto-started — keep launching it manually. Auto-starting
Cerebral on login is a separate follow-up.

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

### Finance plugin (#28)

The Finance plugin OCRs receipt images / scanned PDFs and (on confirm)
appends a row to a Google Sheet or Grist table via the existing
google_workspace `sheets_write_range` tool.

- **Tesseract OCR binary** must be on PATH:
  - Debian/Ubuntu: `sudo apt install tesseract-ocr`
  - macOS: `brew install tesseract`
  - Windows: install the UB-Mannheim build from
    <https://github.com/UB-Mannheim/tesseract/wiki> and add the install
    directory to PATH.
- **Poppler** is required by `pdf2image` to handle scanned-PDF input
  (single-page; multi-page receipts are out of scope for v1):
  - Debian/Ubuntu: `sudo apt install poppler-utils`
  - macOS: `brew install poppler`
  - Windows: download a build from
    <https://github.com/oschwartz10612/poppler-windows/releases> and add
    the `bin/` directory to PATH.
- **Python deps** (`pytesseract`, `pdf2image`, `Pillow`) are in
  `cerebral/requirements.txt` — `pip install -r requirements.txt` picks
  them up.

**Sheet schema.** Default columns are `[date, vendor, total, currency,
items_summary, image_path]`. Override per-call via
`sheet_target.columns` — the plugin maps extracted fields onto your
column order, missing fields → empty string. Example for Google Sheets:

```json
{
  "image_path": "/Users/me/Downloads/receipt.png",
  "sheet_target": {
    "spreadsheet_id": "1AbC…XyZ",
    "sheet_name": "Expenses",
    "columns": ["date", "vendor", "total", "currency"]
  },
  "confirm": true
}
```

For the Grist fallback, replace `spreadsheet_id`/`sheet_name` with
`grist_table` (and optionally `grist_doc_id`):

```json
{
  "image_path": "/Users/me/Downloads/receipt.pdf",
  "sheet_target": {"grist_table": "Expenses"},
  "confirm": true
}
```

**Safety.** `finance_log_expense` requires `confirm: true` to actually
write. With `confirm` omitted or false the plugin returns the extracted
fields and the would-be row but never invokes the workspace plugin —
the LLM is expected to confirm with the user before re-calling with
`confirm: true`. Low-confidence fields (`confidence.<field> < 0.6`,
notably locale-ambiguous slash-format dates and bare-number totals)
should be flagged for the user during that confirmation.

### Model switching (#29)

The tray's **Model** submenu lists every model registered in
`cerebral/llm/router.py` and lets you switch the active model at
runtime, plus pin a different model per task type (`chat`,
`extraction`).

Default registry (real backends):

| Model id | Label | Cloud? | Backend |
|---|---|---|---|
| `ollama/gemma4` | Gemma 4 (local) | no | `OllamaBackend` (`http://localhost:11434`) |
| `claude/haiku`  | Claude Haiku 4.5 | yes | `ClawBackend` (`http://localhost:3000` — OpenClaw) |
| `claude/sonnet` | Claude Sonnet 4.6 | yes | `ClawBackend` |

To use the cloud models, OpenClaw must be running locally (see the
**OpenClaw** section earlier in this file) — Felix never calls the
Anthropic API directly. To use Gemma 4 locally, Ollama must be running
and the `gemma4` model pulled (`ollama pull gemma4`).

A `☁` next to the active model in the tray indicates that requests
will leave the machine; `◉` indicates a local model. The visualiser
briefly shows its `thinking` animation each time the model is
switched.

Per-task pins are useful when you want passive 5W1H extraction to
stay local even though the active conversational model is a cloud
one — open `Task: extraction → Gemma 4 (local)` in the Model
submenu.
