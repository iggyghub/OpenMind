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

To remove auto-start: delete `OpenClaw Gateway.cmd` from `shell:startup` by hand,
or use whatever uninstall command OpenClaw exposes in your version
(`openclaw gateway --help`).

Cerebral itself is **not** auto-started — keep launching it manually. Auto-starting
Cerebral on login is a separate follow-up.

#### Approve the gateway scope upgrade (required before first Cerebral launch)

Cerebral's `openclaw_channels` plugin drives an `events_wait` long-poll
against the gateway as soon as the subscriber starts. **`events_wait` is
itself a privileged call** — even with no channels configured, the first
Cerebral boot against a freshly-paired gateway will file a scope-upgrade
request and the gateway will close the WebSocket until the request is
approved.

Approve it once from any shell BEFORE the first `python -m cerebral.main`:

```powershell
openclaw devices list-pending   # surface the pending requestId
openclaw devices approve --latest
```

`--latest` resolves the most recent pending request automatically. The
scopes requested cover reading channel transcripts (`operator.read`) and
sending replies (`operator.write`); approve once per Cerebral install.
Re-pairing (`openclaw devices clear`) requires re-approval.

If you skip this step and start Cerebral, you'll see one rate-limited
WARN line per subscriber start:

```
[openclaw_channels] events_wait raised (likely scope upgrade pending) --
approve via `openclaw devices approve --latest` (use `openclaw devices
list-pending` to surface the requestId; see SETUP.md). Detail: ...
```

Run the approve command and the subscriber recovers on the next
iteration — no Cerebral restart required.

#### Configure a Telegram channel (recommended first channel)

Telegram is the easiest channel to bring up: bots are free, BotFather is the
only step, and there's no business-account approval like WhatsApp.

OpenClaw 2026.4.29 exposes channels to Cerebral via a stdio MCP server
(`openclaw mcp serve`). Cerebral consumes that surface through the
`plugins/openclaw_channels.py` MCP-client plugin — there is no longer a
WebSocket-subscribe + HTTP-reply pair to configure, and no `agents:` array
in `~/.openclaw/openclaw.json`.

1. **Create a Telegram bot** via [BotFather](https://t.me/BotFather):
   - `/newbot` → choose a display name (e.g. *Felix*) and a username
     (e.g. *felix_openmind_bot*).
   - BotFather replies with an **HTTP API token**. Keep it secret.

2. **Tell OpenClaw about the bot** via the `openclaw channels` CLI (or
   edit `~/.openclaw/openclaw.json` by hand). The minimal Telegram entry:

   ```json
   {
     "channels": {
       "telegram": {
         "enabled": true,
         "botToken": "PASTE_TOKEN_FROM_BOTFATHER",
         "longPolling": true
       }
     }
   }
   ```

   The gateway token at `gateway.auth.token` was generated by
   `openclaw gateway install` in the previous section — leave it alone;
   Cerebral reads it.

3. **Unbind the OpenClaw internal `main` agent from Telegram.** By default,
   OpenClaw routes inbound channel messages to its own internal LLM agent
   (`defaultAgentId: "main"`). Cerebral consumes the same channel via
   `openclaw mcp serve`, so leaving the internal agent bound would race
   with Cerebral on every reply:

   ```powershell
   openclaw agents unbind --agent main --bind telegram
   ```

   (Repeat for any other channel Cerebral should drive: `--bind discord`,
   `--bind whatsapp`, etc.)

4. **Approve Cerebral's scope-upgrade request** if you haven't already —
   see the **Approve the gateway scope upgrade** section above. The
   `events_wait` long-poll triggers the same scope upgrade with or
   without a configured channel, so the prerequisite is the same; this
   step is a no-op if you already approved.

   ```powershell
   openclaw devices list-pending   # find the pending requestId
   openclaw devices approve --latest
   ```

5. **(Optional) Override the gateway URL or token.** Cerebral reads
   `gateway.auth.token` from `~/.openclaw/openclaw.json` by default. To
   point at a non-default install or rotate the token without editing the
   file:

   ```bash
   # .env or shell profile
   OPENCLAW_GATEWAY_TOKEN=<token from gateway.auth.token>
   OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789   # only if non-default
   ```

6. **Restart Cerebral.** In your Cerebral terminal you should see
   `[openclaw_channels] Connected to OpenClaw -- subscriber loop running`.
   If you skipped step 4 you'll instead see a single rate-limited
   `[openclaw_channels] events_wait raised (likely scope upgrade pending)
   -- approve via \`openclaw devices approve --latest\`` line — run the
   approve command and the loop picks up on the next iteration without
   a restart.

7. **Test it.** Open Telegram, find your bot, send `Felix, what time is it?`
   — Felix should reply through the channel within a few seconds.

#### WhatsApp / Discord / Slack

Same pattern: enable the channel in `~/.openclaw/openclaw.json` with the
provider's credentials (Discord bot token, Slack app token, etc.), then
`openclaw agents unbind --agent main --bind <channel>` so the internal
agent doesn't race Cerebral. Scopes already approved in step 4 cover all
channels; no per-channel pairing. See OpenClaw's per-channel docs at
[docs.openclaw.ai/channels](https://docs.openclaw.ai/channels) for the
channel-specific keys.

### Discord (user account) -- experimental, high risk

> **Read first:** this is a separate Discord integration from the
> bot-API one above. It runs Felix against your **personal Discord
> account** (a "self-bot"), not a registered bot. Discord forbids
> this in their Developer Terms and actively detects it. **Detection
> results in permanent ban of your Discord account** -- DMs, friend
> list, server ownership, Nitro, purchase history are all lost, with
> no recovery path. Do NOT enable this on an account you care about.
> See [ADR-0006](docs/adr/0006-discord-user-account-integration.md)
> for the full ToS-risk posture and mitigation roadmap.

This path exists because OpenClaw 2026.4.29's Discord channel is
bot-API only -- it cannot connect to a personal user account. The
plugin `plugins/discord_user.py` (Issue
[#175](https://github.com/iggyghub/OpenMind/issues/175)) talks
directly to Discord, bypassing OpenClaw.

#### 1. Install the self-bot library

```bash
pip install discord.py-self
```

This is **not** in `cerebral/requirements.txt` -- the dep stays
opt-in so contributors who don't enable the plugin don't pay the
install cost. A missing install degrades gracefully (the plugin logs
"Discord user plugin not available" and Cerebral keeps running).

#### 2. Extract your Discord user-account token

The token lives in your browser's local storage when you're logged
in to Discord on the web client. Standard browser dev-tools
extraction works. Treat this string the way you would your Discord
password -- it grants full access to your account.

#### 3. Configure the token

Two storage paths, in resolution order:

- **Per-profile keyring entry** (preferred). Write
  `provider="discord_user", field="api_token"` via the #112
  CredentialStore. The plugin is **deliberately not** exposed in the
  tray "API keys" UI for now (#175 / ADR-0006 friction-as-safety),
  so for slice 1 you set this manually:

  ```powershell
  python -c "from cerebral.db.credentials import CredentialStore; CredentialStore().set_secret(1, 'discord_user', 'api_token', 'PASTE-TOKEN-HERE')"
  ```

  (Replace `1` with your active profile id.)

- **Env var fallback** (simpler for ad-hoc testing):

  ```powershell
  $env:DISCORD_USER_TOKEN = "PASTE-TOKEN-HERE"
  ```

  ```bash
  # .env or shell profile
  DISCORD_USER_TOKEN=PASTE-TOKEN-HERE
  ```

The token is **never** logged, **never** written to
`~/.openclaw/openclaw.json`, and **never** echoed back to the
renderer over IPC. The plugin scrubs the value from any error
message before it leaves the process.

#### 4. Restart Cerebral

In your Cerebral terminal you should see:

```
[discord_user] Token validated for user=<your-username>
[discord_user] Subscriber loop running (DMs only, draft-only inbound)
```

Slice-1 behaviour: incoming DMs from real humans surface as queue
items titled `Discord DM from <author>`. **There is no auto-reply
yet** -- the plugin only drafts the inbound message for you to read.
Auto-reply (with a per-sender allowlist + human-shaped delays + typing
indicators + rate limits) lands in slice 2 as a follow-up issue.

#### 5. Verify outbound send

From an LLM tool call (or the tray's tool tester):

```json
{"tool": "discord_send_message",
 "args": {"channel_id": "<id from discord_list_conversations>",
          "content": "test message from Felix",
          "confirm": true}}
```

Without `confirm: true` the tool returns the would-be message for
review and never hits the network. With `confirm: true`, a real DM
lands in the recipient's client.

#### Disabling the plugin

Unset the env var (and clear the keyring entry if you set one). The
plugin's startup check sees no token and logs `not configured --
subscriber not started`; the tool surface returns "no Discord user-
account token configured" for any call. No restart juggling needed.

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
