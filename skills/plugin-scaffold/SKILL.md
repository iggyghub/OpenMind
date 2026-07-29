---
name: plugin-scaffold
description: Scaffold an ADR-0005-compliant hand-authored MCP plugin skeleton under plugins/<name>.py that passes the inspectability + capability gate on the first try. Use when the user says "scaffold a plugin", "new MCP plugin", "add a Felix capability", or wants a new tool hand-written rather than grown via the builder.
kind: procedure
tools: [read_file, create_file, search_files, run_command]
---

# plugin-scaffold

Complements the growth loop (`plugins/builder.py`, LLM-generated path) -- this
is the hand-authored path for a new Felix plugin.

## Quick start

1. Pick the shape: a single file `plugins/<name>.py` (preferred for simple
   plugins), or a package `plugins/<name>/server.py` if it needs sibling
   modules.
2. Read the bundled [TEMPLATE.py](TEMPLATE.py) with `read_file`, then write it
   to `plugins/<name>.py` with `create_file`, replacing every `TODO`
   placeholder.
3. Declare `REQUIRED_CAPABILITIES` correctly (below), add tools to
   `list_tools()`, implement `call_tool()`.
4. Add a test stub at `cerebral/tests/test_plugin_<name>.py`.
5. Use `search_files` to find the plugin-registration block in
   `cerebral/main.py` (import + orchestrator registration, plus
   `set_token_provider` if this is a static-token plugin) and wire it in.
6. Run the new test with `run_command` (e.g. `pytest cerebral/tests/test_plugin_<name>.py -q`).

## Capability declaration

`REQUIRED_CAPABILITIES: frozenset[str]` is mandatory -- the orchestrator
refuses plugins that omit it.

**The 16-class closed vocabulary** (exact strings only):
`vault_unlock`, `secrets_read`, `fs_read`, `fs_write`, `fs_delete`,
`clipboard`, `shell_exec`, `code_install`,
`network_egress_local`, `network_egress_cloud`, `network_recon`, `network_config`,
`external_data_read`, `external_data_write`, `device_control`, `screen_capture`

**Auto-mapped call sites** (the inspectability scanner requires these
automatically): `keyring.get_password`/`set_password` -> `secrets_read`;
`aiohttp`/`httpx`/`requests` outbound -> `network_egress_cloud` (or `_local`
for LAN); `open()` reads -> `fs_read`, writes -> `fs_write`, `os.remove` ->
`fs_delete`; `subprocess.*`/`os.system` -> `shell_exec`; `pyperclip.*` ->
`clipboard`; `mss`/`pyautogui.screenshot` -> `screen_capture`.

**Hand-declared** (the scanner never infers these -- declare them yourself):
`external_data_read` (reads an external account/API), `external_data_write`
(mutates one), `device_control` (notifications, camera, etc).

**Static-token posture** (todoist/youtube/notion precedent): over-declare
`secrets_read` even when the token arrives via `provider.current()` rather
than a direct `keyring.*` call. The audit only fails on under-declaration.

## Credential wiring

**Static API token** (the common case, no OAuth): use the `TokenProvider`
Protocol from `TEMPLATE.py`, add a module-level `set_token_provider(fn)`, and
wire it from `cerebral/main.py`. The factory reads from `CredentialStore`
(keyring, per-profile `profile_<id>/<name>/api_token`) with an env-var
fallback. Declare `secrets_read` (over-declaration posture above).

**OAuth**: the `TokenProvider` Protocol carries both `current()` and
`refresh()`; `CredentialStore` reads `client_secret`/`refresh_token`/
`access_token` from keyring. See `plugins/gmail.py` for the full shape.

## Irreversible tools

Any tool whose effect can't be undone must pass `irreversible=True` to
`Tool(...)` -- this forces the always-on-top confirmation modal regardless of
session/persistent bypasses (ADR-0005 amendment).

## Test stub layout

Tests live at `cerebral/tests/test_plugin_<name>.py`. Inject a stub token
provider and stub `fetch_fn` -- no real network, keyring, or env reads in the
suite (see `cerebral/tests/test_plugin_todoist.py` for the static-token
pattern). Minimum: one test asserting `REQUIRED_CAPABILITIES` is a non-empty
frozenset, and one happy-path tool call against the stub `fetch_fn`.

## Checklist

- [ ] `PLUGIN_NAME` matches the filename stem
- [ ] `REQUIRED_CAPABILITIES: frozenset[str]` declared with the correct classes
- [ ] `list_tools()` returns at least one `Tool(...)`
- [ ] Write/delete tools have `irreversible=True` where appropriate
- [ ] Static-token plugins have the `TokenProvider` Protocol + `set_token_provider` seam
- [ ] `create()` zero-arg factory at the module bottom
- [ ] Test stub added at `cerebral/tests/test_plugin_<name>.py`
- [ ] Plugin wired into `cerebral/main.py`
- [ ] File body is ASCII-only
