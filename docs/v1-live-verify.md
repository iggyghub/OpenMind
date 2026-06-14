# OpenMind v1 — Human Live-Verify Checklist

This document covers everything the autonomous loop built with mocked tests only.
**Agent work:** writing this checklist.
**Human work:** executing it — in order, once Cerebral is running on the dev box.

Run `scripts/verify-v1.ps1` first to confirm the automatable pre-conditions pass,
then work through sections 1–6 manually.

---

## 1. Batched OAuth consent pass

Five new OAuth scopes were added across slices B.1–B.6 (#224–#229). Google requires
re-consent the first time a broader scope set is requested. Do this **once** — it
covers all five plugins in a single browser flow.

**New scopes added by the Bucket B slices:**

| Scope | Plugin | Issue |
|---|---|---|
| `https://www.googleapis.com/auth/documents` | Google Docs | #224 |
| `https://www.googleapis.com/auth/spreadsheets` | Google Sheets | #225 |
| `https://www.googleapis.com/auth/tasks` | Google Tasks | #227 |
| `https://www.googleapis.com/auth/drive` | Google Drive | #228 (upgraded from drive.readonly) |
| `https://www.googleapis.com/auth/contacts` | Google Contacts | #229 |

**Steps:**

1. Start Cerebral: open a terminal in the repo root and run
   `python -m cerebral.main`.
2. Open the Main window → Settings → Google Account.
3. Click **Re-authorize** (or **Connect**, if this is a fresh install).
   Cerebral will open a browser tab using the full `_GOOGLE_SCOPES` list
   from `cerebral/main.py:291–300`.
4. Sign in with your Google account and click **Allow** on the consent screen.
   Confirm you see all five new scopes listed (Documents, Spreadsheets, Tasks,
   Drive, Contacts) alongside the existing Gmail and Calendar scopes.
5. The tab will redirect to `localhost` and close. The tray icon should show
   the account as **connected**.

**Google Maps API key** (Issue #226 — static key, no OAuth):

Maps uses a static API key, not the OAuth flow above.

1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
   and create (or copy) an API key with the **Maps JavaScript API**,
   **Geocoding API**, **Places API**, and **Directions API** enabled.
2. Set it as an environment variable before starting Cerebral:
   ```
   $env:GOOGLE_MAPS_API_KEY = "AIza..."
   ```
   Or store it in the active profile's keyring via the credential store
   (provider name `google_maps`, field `api_token`).
3. Restart Cerebral. The `maps_*` tools should now be available.

---

## 2. Per-plugin live-verify steps

One concrete smoke action per plugin against your real Google account. Run after
completing Section 1.

### 2.1 Google Docs (#224)

**Tool:** `docs_create`

Say to Felix: *"Create a document titled 'OpenMind v1 live test'."*

Expected outcome:
- Felix responds with a document ID (a long alphanumeric string).
- Open [drive.google.com](https://drive.google.com) → confirm a file named
  **OpenMind v1 live test** appears in My Drive.

Cleanup: delete the file from Drive when done.

### 2.2 Google Sheets (#225)

**Tool:** `sheets_create`

Say to Felix: *"Create a spreadsheet titled 'OpenMind test sheet'."*

Expected outcome:
- Felix responds with a spreadsheet ID.
- Open [sheets.google.com](https://sheets.google.com) → confirm **OpenMind test sheet**
  appears in the recent list.

Cleanup: delete the file from Drive when done.

### 2.3 Google Maps (#226)

**Tool:** `maps_geocode`

Say to Felix: *"Geocode 10 Downing Street, London."*

Expected outcome:
- Felix returns a latitude/longitude pair near `51.5034, -0.1276`.
- No API-key error. If you see `REQUEST_DENIED` or `maps: not available`, the
  key is missing or has the wrong APIs enabled — revisit Section 1 Maps step.

### 2.4 Google Tasks (#227)

**Tool:** `tasks_create`

Say to Felix: *"Add a task called 'OpenMind v1 smoke test' to my task list."*

Expected outcome:
- Felix confirms the task was created.
- Open [tasks.google.com](https://tasks.google.com) → confirm **OpenMind v1 smoke test**
  appears in My Tasks.

Cleanup: delete the task when done.

### 2.5 Google Drive (#228)

**Tool:** `drive_list_files`

Say to Felix: *"List my recent Drive files."*

Expected outcome:
- Felix returns a list of file names and IDs from your Drive.
- The list includes files you know exist (e.g. the test doc/sheet from steps
  2.1–2.2 if you have not yet deleted them).

### 2.6 Google Contacts (#229)

**Tool:** `contacts_search`

Say to Felix: *"Search my contacts for [your own first name]."*

Expected outcome:
- Felix returns at least one contact entry with your name and email.
- If the contacts list is empty, try a name you know is in your Google Contacts.

---

## 3. Fallback spot-checks

Each fallback activates when the primary Google plugin returns a connectivity error.
The easiest way to trigger this without fully going offline: temporarily unset the
OAuth token (remove the Google account from Settings) **or** block internet access
on the machine. The fallback layer in `plugins/google_workspace_fallback.py` routes
to the OSS backend automatically.

For each check below, the simplest test is to call the tool through Cerebral while
the primary is unavailable and confirm the fallback backend responds.

### 3.1 Calendar fallback — local SQLite (#232)

**Trigger:** block network or disconnect Google account.

Say to Felix: *"Create a calendar event: 'Fallback test' tomorrow at 10 AM."*

Expected outcome:
- Felix confirms the event was created.
- No Google API error. The event is stored in the local SQLite scheduler
  (`CalendarSQLiteFallback` in `google_workspace_fallback.py:442`).
- Verify by asking: *"List my calendar events for tomorrow."*
  The fallback entry should appear.

### 3.2 Docs fallback — local ODF files (#233)

**Trigger:** block network or disconnect Google account.

Say to Felix: *"Create a document titled 'Offline test doc'."*

Expected outcome:
- Felix confirms the document was created.
- A file named `<uuid>.odt` appears in `~/Documents/OpenMind/` (or the
  directory set by the `LOCAL_DOCS_DIR` env var).

### 3.3 Maps fallback — Nominatim/OSM (#234)

**Trigger:** unset `GOOGLE_MAPS_API_KEY`.

Say to Felix: *"Geocode the Eiffel Tower, Paris."*

Expected outcome:
- Felix returns a lat/lng near `48.8584, 2.2945`.
- The response comes from the Nominatim OSS geocoder, not Google.
- Note: the public Nominatim instance (nominatim.openstreetmap.org) requires a
  network connection. For a fully offline test, set `NOMINATIM_URL` to a
  self-hosted instance.

### 3.4 Tasks fallback — local SQLite (#235)

**Trigger:** block network or disconnect Google account.

Say to Felix: *"Add a task called 'Offline task test'."*

Expected outcome:
- Felix confirms the task was created.
- The task is stored in the local SQLite backing store
  (`TasksSQLiteFallback` in `google_workspace_fallback.py:492`).
- Verify by asking: *"List my tasks."* The offline entry should appear.

### 3.5 Contacts fallback — local SQLite (#236)

**Trigger:** block network or disconnect Google account.

Say to Felix: *"Add a contact named 'Test Person' with email test@example.com."*

Expected outcome:
- Felix confirms the contact was created locally.
- The contact is stored in the local SQLite backing store
  (`ContactsSQLiteFallback` in `google_workspace_fallback.py:624`).
- Verify by asking: *"Search my contacts for Test Person."*
  The offline entry should appear.

---

## 4. Bucket D — Daily-driver stability campaign

v1 ships when **both** D.1 and D.2 pass. File every failure as a GitHub issue
tagged `needs-triage`. Fix all P0/P1 issues before declaring v1 done.

### D.1 — 8-hour continuous passive-mode run

**Goal:** no crash, no unbounded memory growth over 8 hours of background
passive listening.

**Steps:**

1. Start Cerebral on the dev box with no active tasks queued:
   ```
   python -m cerebral.main
   ```
2. Leave it running for 8 hours (overnight or during a normal workday).
   Do not issue voice commands — passive mode only.
3. Monitor memory every 30 minutes using one of these methods:

   *PowerShell (run in a separate terminal):*
   ```powershell
   while ($true) {
       $proc = Get-Process -Name python -ErrorAction SilentlyContinue |
               Where-Object { $_.MainModule.FileName -like "*cerebral*" } |
               Select-Object -First 1
       if ($proc) {
           $mb = [math]::Round($proc.WorkingSet64 / 1MB, 1)
           Write-Host "$(Get-Date -Format 'HH:mm') -- Working set: ${mb} MB"
       }
       Start-Sleep -Seconds 1800
   }
   ```

   *Task Manager:* open Details tab, sort by Memory (Working Set), watch the
   `python.exe` process tied to Cerebral.

4. At the end of 8 hours, confirm:
   - [ ] Cerebral process is still running (no crash).
   - [ ] Memory (working set) at hour 8 is not more than 2x the hour 0 baseline.
         A slow linear growth of a few MB per hour is acceptable; unbounded
         growth indicates a leak and must be filed as a bug.
   - [ ] No unhandled exception tracebacks in the Cerebral log output.

### D.2 — Daily wake-queue-approve cycle

**Goal:** the core voice → intent → queue → approve loop works reliably in
daily use; every breakage is captured.

**Steps (repeat daily for at least 3 consecutive days):**

1. Say *"Felix"* to wake the assistant.
2. Issue a real-world command from your daily workflow — for example:
   - *"Felix, what's on my calendar today?"*
   - *"Felix, draft a quick reply to the last email from [name]."*
   - *"Felix, add a task to review the OpenMind PR."*
3. The tray pulldown should show the candidate action(s). Approve or dismiss.
4. Confirm the approved action completed correctly (check Google Calendar,
   Gmail Drafts, Google Tasks, etc. as appropriate).

**Pass criteria for D.2:**
- All three days complete with no crash.
- Approved actions produce the expected outcome at least 4 out of 5 attempts.
- Any failure (wrong tool selected, tool error, crash) is filed as a GitHub
  issue within 24 hours.

**Filing issues:**
```
gh issue create \
  --title "D.2 breakage: <one-line description>" \
  --body "Steps to reproduce: ..." \
  --label "needs-triage"
```

---

## 5. Channel bridge live-verify — Discord (#164)

Proves the harness is multi-channel: an inbound Discord message reaches Cerebral
through the OpenClaw channel bridge and the reply lands back in Discord.

> **Architecture note (post-#168):** there is no longer a `ChannelBridge` class.
> Cerebral consumes OpenClaw's channel bridge as a stdio MCP server via the
> `plugins/openclaw_channels.py` plugin, whose background subscriber drives an
> `events_wait` long-poll and replies via `openclaw_messages_send`. Conversations
> are keyed by the gateway-supplied `session_key` (e.g. `discord:456` vs
> `telegram:123`), and the plugin's per-`session_key` history buffer
> (`_sessions_history`) is keyed on it — so history cannot bleed across channels
> by construction.

**Pre-conditions:**

1. Complete the **"Configure a Discord channel"** subsection in `SETUP.md`
   (register a bot at the Discord Developer Portal, put the bot token under
   `channels.discord` in `~/.openclaw/openclaw.json`, invite the bot to a
   server, and unbind OpenClaw's internal `main` agent from Discord).
2. Start Cerebral (`python -m cerebral.main`) and confirm the log shows:
   ```
   [openclaw_channels] Connected to OpenClaw -- subscriber loop running
   ```
   If you instead see `events_wait raised (likely scope upgrade pending)`,
   approve the device scope per `SETUP.md`
   (`openclaw devices approve --latest`) and restart.

**Steps:**

1. From a Discord account that is allowed to message the bot, send a DM (or an
   @-mention in an allowed channel) such as *"Felix, what's 2 + 2?"*.
2. The subscriber forwards the inbound event to Cerebral's pipeline and posts
   the LLM reply back to the same Discord conversation.

**Expected outcome:**

- A reply from Felix appears in the same Discord DM / channel within a few
  seconds.
- The Cerebral log shows the inbound event being processed and a
  `openclaw_messages_send` for the reply (no `messages_send ... returned error`).
- **Cross-channel isolation:** if you have also configured another channel,
  confirm a follow-up that relies on history (e.g. *"and double that?"*) only
  uses the same channel's context — the `session_key` prefix keeps Discord and
  other channels' histories separate.

Capture the result as **PRD #1 / story 42** evidence.

---

## 6. Phone live-verify — one outbound call (#165)

Proves `plugins/phone.py:start_call` places a real outbound call through
OpenClaw's voice channel.

**Pre-conditions:**

1. A voice provider (Twilio or equivalent) is configured in
   `~/.openclaw/openclaw.json` and OpenClaw's `POST /voice/dial` endpoint is
   live — see the **"Configure a voice channel"** subsection in `SETUP.md`. The
   provider API key lives in OpenClaw's config, not in OpenMind; the exact key
   names are provider-specific. Confirm `curl -X POST http://localhost:3000/voice/dial`
   with a test body reaches the provider before proceeding.
2. Cerebral's `phone` plugin posts to `http://localhost:3000/voice/dial` by
   default (`DEFAULT_BASE_URL`); override via the plugin's `base_url` if OpenClaw
   listens elsewhere.

**Steps:**

1. Say to Felix: *"Call my phone"* (or *"Call +1XXXXXXXXXX"* with your own
   number in E.164 format).
2. `start_call` declares `external_data_write` + `network_egress_local`
   (`plugins/phone.py:31–34`). `external_data_write` is ask-class, so **expect a
   consent prompt before the call is placed** — approve it. (Confirm that
   *without* approval, no call is placed — the gate must hold.)

**Expected outcome:**

- Your phone actually rings (**user-verified** — the core acceptance criterion).
- `start_call` returns a non-error `ToolResult` whose JSON body carries the
  provider's call id / initial status.
- No `Call failed:` error in the Cerebral log.

Capture the result as **PRD #1 / story 40** evidence.

---

## 7. v1 DoD gate

v1 is complete when:
- [ ] All six Google plugins live-verified (Section 2).
- [ ] All five fallbacks spot-checked (Section 3).
- [ ] D.1: 8-hour passive run passed.
- [ ] D.2: 3-day daily-driver cycle passed.
- [ ] Channel bridge live-verified — Discord inbound → reply (Section 5, #164).
- [ ] Outbound phone call placed and the phone rang (Section 6, #165).
- [ ] All P0/P1 issues from D.1 and D.2 closed.
