---
name: plugin-scaffold
description: Scaffold an ADR-0005-compliant hand-authored MCP plugin skeleton under plugins/<name>.py that passes the inspectability + capability gate on the first try. Use when the user says "scaffold a plugin", "new MCP plugin", "add a Felix capability", or "/plugin-scaffold". Complements the runtime NL builder.py (LLM-generated path) -- this is the hand-authored path.
---

# plugin-scaffold

## Quick start

0. **Check prior art first.** Before scaffolding, search the web for an existing
   implementation of this capability from another agent harness or MCP server
   (MCP registries, LangChain/AutoGPT tools, GitHub). Adapting a proven
   implementation beats writing one from scratch. Note what you found (or that
   nothing suitable exists) before proceeding.

1. Pick the plugin shape:
   - Single file: `plugins/<name>.py` (preferred for simple plugins)
   - Package: `plugins/<name>/server.py` (use when the plugin needs sibling modules)

2. Copy [TEMPLATE.py](TEMPLATE.py) to `plugins/<name>.py` and replace every `TODO` placeholder.

3. Declare `REQUIRED_CAPABILITIES` correctly (see below), add tools to `list_tools()`, implement `call_tool()`.

4. Add a test stub at `cerebral/tests/test_plugin_<name>.py`.

5. Wire the plugin in `cerebral/main.py` (import, `set_token_provider` call if static-token, orchestrator registration).

## Capability declaration

`REQUIRED_CAPABILITIES: frozenset[str]` is mandatory -- the orchestrator refuses plugins that omit it.

**The 16-class closed vocabulary** (exact strings only):
`vault_unlock`, `secrets_read`, `fs_read`, `fs_write`, `fs_delete`,
`clipboard`, `shell_exec`, `code_install`,
`network_egress_local`, `network_egress_cloud`, `network_recon`, `network_config`,
`external_data_read`, `external_data_write`, `device_control`, `screen_capture`

**AST-mapped call sites** (the inspectability scanner auto-requires these):
- `keyring.get_password` / `keyring.set_password` -> `secrets_read`
- `aiohttp` / `httpx` / `requests` outbound -> `network_egress_cloud` (or `_local` for LAN)
- `open()` reads -> `fs_read`; writes -> `fs_write`; `os.remove` -> `fs_delete`
- `subprocess.*` / `os.system` -> `shell_exec`
- `pyperclip.*` -> `clipboard`
- `mss` / `pyautogui.screenshot` -> `screen_capture`

**Hand-declared (AST never auto-requires these -- declare them yourself):**
- `external_data_read` -- your tool reads from an external account/API
- `external_data_write` -- your tool mutates an external account/API
- `device_control` -- notifications, camera, etc.

**Static-token posture (todoist/youtube/notion precedent):** deliberately
over-declare `secrets_read` even when the token arrives via `provider.current()`
rather than a direct `keyring.*` call. The AST audit only fails on under-declaration;
over-declaration is audit-safe and is the correct posture for credential-gated plugins.

## Credential wiring

**Static API token** (no OAuth -- the common case):
- Use the `TokenProvider` Protocol from TEMPLATE.py.
- Add `set_token_provider(fn)` module-level setter.
- Wire from `cerebral/main.py`: `plugins.<name>.set_token_provider(lambda: _get_<name>_token_provider())`.
- The factory reads from `CredentialStore` (keyring, per-profile `profile_<id>/<name>/api_token`) with env-var fallback.
- Declare `secrets_read` in `REQUIRED_CAPABILITIES` (over-declaration posture above).

**OAuth (Gmail/Calendar precedent):**
- `TokenProvider` carries both `current()` and `refresh()`.
- `CredentialStore` reads `client_secret`, `refresh_token`, `access_token` from keyring.
- See `plugins/gmail.py` for the full OAuth shape.

## Irreversible tools

Any tool whose effect cannot be undone must pass `irreversible=True` to `Tool(...)`:

```python
Tool(name="myp_delete_item", ..., irreversible=True)
```

This triggers the alwaysOnTop modal regardless of session/persistent bypasses
(ADR-0005 amendment 2026-05-20).

## Test stub layout

Tests live at `cerebral/tests/test_plugin_<name>.py`. Inject a stub provider
and stub `fetch_fn` -- no real network, keyring, or env reads in the suite.
See `cerebral/tests/test_plugin_todoist.py` for the static-token pattern.

Minimum: one test that asserts `REQUIRED_CAPABILITIES` is a non-empty frozenset,
and one happy-path tool call against a stub fetch_fn.

## Checklist

- [ ] `PLUGIN_NAME` matches the filename stem
- [ ] `REQUIRED_CAPABILITIES: frozenset[str]` declared with correct classes
- [ ] `list_tools()` returns at least one `Tool(...)`
- [ ] Write/delete tools have `irreversible=True` where appropriate
- [ ] Static-token plugins: `TokenProvider` Protocol + `set_token_provider` seam
- [ ] `create()` zero-arg factory at module bottom
- [ ] Test stub at `cerebral/tests/test_plugin_<name>.py`
- [ ] Plugin wired in `cerebral/main.py`
- [ ] File body is ASCII-only
