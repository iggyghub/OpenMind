# OpenMind Harness UI Rework -- Specification

**Status:** Draft for implementation
**Scope:** Renderer-side IA restructure + three new WS message types. No `tray/main.js` changes. No orchestrator behavior changes except enable/disable support.
**Depends on:** Repo audit (2026-07-20) -- MCPOrchestrator, SettingsStore, CredentialStore, `_record_turn` transcript pipeline.

Key source files:
- Orchestrator + gate order + `_tool_registrations` + `registration_errors`: `cerebral/mcp/orchestrator.py`
- Settings closed key set: `cerebral/settings.py`
- Credential metadata + `_static_token_from_env_or_store` chain: `cerebral/db/credentials.py`
- WS server, IPC `call_tool`, `_record_turn`: `cerebral/main.py`
- Renderer routing: `tray/lib/sidebar-router.js`; deep links in `tray/main.js` (do not modify)
- Existing plugin-IPC tests: `cerebral/tests/test_plugin_settings_ipc.py`

---

## 1. Goals and priorities

1. **Control** -- enable/disable/configure every plugin from one place (primary)
2. **Extensibility** -- a new plugin dropped into `plugins/` appears in the UI with zero UI code changes
3. **Observability** -- live activity feed of tool calls (phase 3)

Non-goals for this rework: plugin marketplace, remote MCP servers, multi-machine sync, changing the discovery contract.

## 2. Constraints (do not violate)

- Main window stays **node-free** (`nodeIntegration:false`, `contextIsolation:true`). All backend access over `ws://localhost:7766`. (ADR-0007)
- `SettingsStore` keeps its **closed key set** -- new keys are added to the vocabulary, not free-form.
- Secrets never leave the keyring. The UI receives credential **metadata only** (provider, masked hint, source). No message type may return a secret value.
- The orchestrator's gate order (scan -> capabilities -> create -> register) is untouched.

---

## 3. Information architecture

### 3.1 Route collapse: 16 -> 4 sections + header control

| New section | Absorbs (current routes) | Notes |
|---|---|---|
| **Conversation** | Quick Ask, Conversation, Conversations, Queue | Active chat is the pane; conversation history is a left rail within the section; Queue renders as a badge + collapsible sub-pane |
| **Harness** | Plugins, Integrations, Credentials, Permissions | This spec's main subject -- see section 4 |
| **Library** | Memory, Documents, Recipes, Insights | Felix's knowledge and saved automations |
| **Settings** | Settings, Models | Models (LLM routing) becomes a Settings subsection |
| *(header)* | Profiles | Profile switcher dropdown in the titlebar area, not a nav item |

**Job Search:** removed from the fixed nav. Interim home: Library. Long-term: plugin-owned pages (out of scope here, but don't design against it).

### 3.2 Routing

- Keep `sidebar-router.js` and hash-based routing. New routes: `#conversation`, `#harness`, `#library`, `#settings`.
- Old hashes redirect: `#plugins` -> `#harness`, `#credentials` -> `#harness` (opens nothing specific), `#models` -> `#settings/models`, etc. Deep links like `openMainWindow('#plugins')` in `tray/main.js` keep working via these redirects -- **that is why `main.js` needs no changes**.
- Sub-navigation within a section uses hash suffixes: `#harness/<plugin_name>` opens that plugin's drawer directly.

---

## 4. Harness section

### 4.1 Layout

```
+----------+--------------------------------------+
| Filters  | [search] [Add plugin help]           |
|          | +------+ +------+ +------+ +------+   |
| Capabil- | | card | | card | | card | | card |  |
| ities    | +------+ +------+ +------+ +------+   |
| (16)     |  ... grid, auto-fit ...              |
|          |                                      |
| Status   +--------------------------------------+
| (4)      | Activity feed (phase 3)              |
+----------+--------------------------------------+
```

- **Filters** = the 16 capability classes (auto-populated from data, only show classes with >=1 plugin) + 4 status filters. Filters are additive tags, not folders; a plugin appears under every capability it declares.
- **Cards** = one per discovered plugin *and* one per registration refusal (refusals must be visible, not hidden).
- Clicking a card opens the **detail drawer** (right-side overlay within the section).

### 4.2 Status semantics

| Status | Source of truth | Indicator |
|---|---|---|
| `active` | registered in orchestrator, not disabled | green dot |
| `error` | present in `registration_errors` | red dot; card shows `reason` |
| `trusted_unverified` | loaded from `plugins/_trusted/` (scan skipped) | amber badge "trusted, unverified" -- always visible, never collapsed |
| `disabled` | in `disabled_plugins` setting | gray dot, card at reduced opacity |

A plugin can be both `trusted_unverified` and `active` -- badge and dot are independent.

### 4.3 Card contents

- Plugin name, first capability icon
- Status dot (+ trust badge if applicable)
- Tool count, source layout label (`flat` / `subdir` / `trusted`)
- Capability tags (max 3 shown, "+N" overflow)
- If `error`: the `reason` string inline

### 4.4 Detail drawer

Sections, top to bottom:

1. **Header** -- name, status, enable/disable toggle, trust badge
2. **Tools** -- list from registration. Each tool: name, one-line description (from schema), takeover indicator where applicable ("supersedes `gmail_send` from `gmail`"). A **Test call** button per tool (section 5.3), gated behind an explicit confirm for tools with side effects -- reuse the existing irreversible-modal flow where the permissions layer already requires it.
3. **Capabilities** -- the plugin's `REQUIRED_CAPABILITIES`, each linking to the capability filter
4. **Credentials** -- metadata from CredentialStore for this plugin's provider(s):
   - source: `keyring` (active profile) / `env` (fallback in use -- show the env var name) / `missing`
   - masked hint only (e.g. `****3f2a`), never the value
   - "Manage" opens the existing credential entry flow
5. **Errors** -- full `{reason, detail, path}` if the plugin was refused
6. **Source** -- file path, layout type

### 4.5 Empty and edge states

- No plugins discovered: "No plugins found in `plugins/` -- drop a `<name>.py` implementing the plugin contract to get started."
- Filter with no matches: "No plugins match" + clear-filters action.
- Orchestrator unreachable (WS down): section-level banner "Can't reach Cerebral. Retry" -- never render stale plugin state as if live.

---

## 5. WebSocket message contracts

All messages follow the existing WS envelope conventions. Shapes below are the `payload`.

### 5.1 `plugins:list` (request -> response)

Request: `{}` (no params). Response:

```json
{
  "plugins": [
    {
      "name": "google_workspace",
      "status": "active",
      "trust": "inspected",
      "source_layout": "flat",
      "path": "plugins/google_workspace.py",
      "capabilities": ["network", "credentials_read"],
      "enabled": true,
      "tools": [
        {
          "name": "gmail_send",
          "description": "Send an email via Gmail",
          "supersedes": {"tool": "gmail_send", "from_plugin": "gmail"}
        }
      ],
      "credentials": [
        {
          "provider": "google",
          "source": "keyring",
          "hint": "****3f2a",
          "env_var": null
        }
      ]
    }
  ],
  "errors": [
    {
      "plugin_name": "broken_thing",
      "reason": "REASON_NOT_INSPECTABLE_PATH",
      "detail": "subdir without server.py",
      "path": "plugins/broken_thing/"
    }
  ],
  "capability_vocabulary": ["network", "filesystem_read", "..."]
}
```

Notes:
- `supersedes` is `null` for normal tools; populated from `_tool_registrations` history.
- `credentials.source` resolution mirrors the existing keyring -> env chain (`_static_token_from_env_or_store`); `env_var` is set only when `source == "env"`. `hint` is derived server-side; if the store can't produce one safely, send `null` and the UI shows "configured" without a hint.
- `capability_vocabulary` lets the UI render the filter sidebar without hardcoding the 16 classes.
- Broadcast variant: on any registration change (startup complete, enable/disable, future hot-reload), Cerebral pushes the same payload as `plugins:changed` so open Harness views update without polling.

### 5.2 `plugins:set_enabled` (request -> response)

Request:

```json
{"plugin_name": "discord", "enabled": false}
```

Behavior:
- Adds/removes the name in the new `disabled_plugins` setting (see section 6).
- Disable: orchestrator `unregister(plugin_name)` -- existing takeover-revert logic restores any superseded tools.
- Enable: re-run the single-plugin load path (scan -> capabilities -> create -> register) for that file. Gate order is identical to startup; a plugin that fails re-scan lands in `registration_errors`, not silently active.
- Response: the updated `plugins:list` payload (single source of truth; no optimistic UI needed).

Error cases: unknown plugin name -> error response; enabling a plugin whose file no longer exists -> moves it to `errors` with a distinct reason.

### 5.3 `plugins:test_call` (request -> response)

Thin wrapper over the existing tray-IPC direct `call_tool` path -- reuse it rather than adding a parallel entry point, so transcript recording (`_record_turn`) and the permissions layer apply automatically.

Request:

```json
{"tool_name": "gmail_send", "args": {"to": "..."}, "thread": "harness-test"}
```

Response mirrors `ToolResult`: `{"is_error": false, "content_preview": "first 500 chars..."}`. The orchestrator's never-raise contract holds -- plugin exceptions come back as `is_error: true`.

UI: the drawer renders an args form from the tool's input schema (`tools_for_llm` already carries JSON Schema -- render fields from it; unknown/complex schemas fall back to a raw JSON textarea).

---

## 6. Settings changes

Add to the `SettingsStore` closed key vocabulary:

- `disabled_plugins: list[str]` -- default `[]`. Checked in `discover_plugins`: a discovered-but-disabled plugin is scanned and recorded (so its card renders with full metadata) but **not registered** -- or, if scanning disabled plugins is undesirable, recorded with metadata from filename only and `tools: []`. Pick one and document it; recommendation: scan but don't register, so the card is informative.

No other settings changes. Credentials and permissions flows are reused as-is.

---

## 7. Phase 3 -- Activity feed (spec'd now, built later)

- The feed subscribes to the live `_record_turn` WS broadcast -- **no new backend channel needed** for live view.
- Rendered as the bottom strip of the Harness section + full-page view under a `#harness/activity` sub-route. Ring buffer, last 200 events client-side.
- Row: timestamp, `plugin.tool`, ok/error, duration if available. Click -> filters the transcript to that thread.
- **Known gap to close when building this phase:** `conversation_turns` persists tool results as `{name, is_error}` only. Add a truncated `content_preview` (<=500 chars, after secret-pattern redaction) to `KIND_TOOL_RESULT` records so the feed can answer "what did it return" after restart. This is the only schema change in the whole rework -- keep it out of phases 1-2.

---

## 8. Build order

| Phase | Work | Owner |
|---|---|---|
| 1a | `plugins:list` + `plugins:changed` broadcast | backend |
| 1b | `disabled_plugins` setting + `plugins:set_enabled` | backend |
| 1c | Harness section: filters, card grid, drawer (read-only) -- against 1a | renderer |
| 2a | Enable/disable toggle + `plugins:test_call` + args-form-from-schema | both |
| 2b | Route collapse 16 -> 4, hash redirects, header profile switcher | renderer |
| 3 | Activity feed + `content_preview` transcript change | both |

1a/1b are pure backend and can start immediately. 1c needs 1a's payload shape only (this doc), not its implementation -- build against a fixture JSON first.

## 9. Acceptance checks

- Dropping a valid new `plugins/<name>.py` and restarting shows a correct card with zero UI changes.
- A plugin in `plugins/_trusted/` always shows the amber unverified badge.
- Disabling `google_workspace` restores `gmail_send` to the `gmail` plugin (takeover revert visible in the drawer).
- Killing Cerebral shows the unreachable banner within 5s; reconnect restores state via `plugins:changed`.
- No WS message in any flow contains a secret value (grep-able in tests).
- All 16 old hash routes redirect somewhere sensible; `openMainWindow('#plugins')` still lands on Harness.
