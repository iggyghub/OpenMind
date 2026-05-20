# ADR-0005: Security and permissions model

**Date:** 2026-05-11
**Status:** Accepted

## Context

Felix executes tasks on behalf of the user across files, shell, network, secrets, and external accounts. The LLM is the decision-maker for which MCP tool fires and with what arguments, which means an attacker who can put text in front of the model (a poisoned web page, an email, ambient audio captured for the queue, a user-generated plugin) can in principle steer Felix into acting against the user. We need a permissions model before more tools land, because retrofitting consent onto 30 already-running plugins is harder than declaring the contract once and enforcing it at registration.

Ranked threats, highest first:

1. **Prompt injection → tool misuse.** Hostile content reaches the LLM and convinces it to call a destructive tool.
2. **Opaque uninspectable plugins.** Binary or obfuscated third-party plugins whose behaviour cannot be reviewed.
3. **Ambient/queue actuation.** A queued candidate from passive 5W1H extraction executes without the user's wake.
4. **Builder output misbehaviour.** A natural-language-generated plugin under-declares what it does.
5. **Readable third-party plugins.** Hand-authored plugins from outside the user.
6. **Multi-user/household.** Permissions leaking across profiles in a shared install. (Design must not preclude; not solved in v1.)
7. **Process compromise.** Out of scope — that's OS hygiene, not the permissions model.

## Decision

**Permission unit.** Hybrid: a fixed closed set of capability classes is primary; per-tool overrides are an escape hatch. Tools cannot invent new classes.

**Vocabulary (16 classes, exhaustive):** `vault_unlock`, `secrets_read`, `fs_read`, `fs_write`, `fs_delete`, `clipboard`, `shell_exec`, `code_install`, `network_egress_local`, `network_egress_cloud`, `network_recon`, `network_config`, `external_data_read`, `external_data_write`, `device_control`, `screen_capture`.

**Two cross-cutting flags** (modifiers on a call, not classes):

- `passive` — call originated from a queued 5W1H candidate, not a wake. Escalates the policy one notch (silent → ask, ask → deny) and defeats session/persistent bypasses.
- `irreversible` — caller declares the effect cannot be undone. Forces a modal confirmation even when a session/persistent bypass would otherwise apply. Per-tool target pre-approvals (e.g. "always send to john@x") are per-tool overrides; they do not class-bypass `irreversible`.

**Default policy per class (day-1 ACL):**

- *silent:* `fs_read`, `clipboard`, `network_egress_local`, `network_egress_cloud`, `external_data_read`, `device_control`
- *ask:* `vault_unlock`, `secrets_read`, `fs_write`, `fs_delete`, `code_install`, `network_recon`, `network_config`, `external_data_write`, `screen_capture`
- *deny:* `shell_exec` (one-time settings opt-in flips it to ask)

The ☁ cloud-traffic indicator from issue #29 remains a visibility cue only; it does not add a gate on top of `network_egress_cloud`, because sensitive cloud calls already trip `secrets_read` or `external_data_write` first.

**Grant durations.** *Once* / *session* / *persistent*. Default state for ask-class calls is "ask" — nothing is silently granted on first launch. A session lasts until profile switch or Cerebral restart, whichever comes first.

**Inspectability gate (plugin registration).** A plugin is *inspectable* iff: (1) it is text Python in `plugins/<name>.py` or `plugins/<name>/server.py`; (2) it passes a static-pattern scan that is a strict superset of `builder.py`'s current `_FORBIDDEN_PATTERNS`; (3) its `REQUIRED_CAPABILITIES` set is demonstrably complete via an AST walk — every call site whose target requires a capability declares that capability. Opaque plugins are refused at registration with a clear error surfaced in the tray's plugin list.

**Escape hatch.** `plugins/_trusted/` bypasses the inspectability check but still passes through capability gates. Trusted plugins display a permanent red "trusted, unverified" badge in the tray.

**AST completeness check** is mandatory for builder-generated plugins, optional for hand-authored (the author signs off via the declaration). Hand-authored plugins still pass through steps (1) and (2).

**Builder-specific.**

- Output schema gains `required_capabilities: list[str]` (validated against the 16-class vocab) and `description: str`.
- A single install-time prompt consumes `code_install` once for the whole build, showing: plugin name, LLM-generated description, declared capabilities in user-facing language, pip dependencies, source preview link, Install/Cancel.
- Installed builder plugins carry a "new plugin" flag default-on that disables session/persistent bypasses on their tools until the user clears it in tray settings. Hand-authored plugins have the flag default-off.
- Updates: the builder refuses to overwrite an existing plugin. Users uninstall then re-create. ACL never carries across versions.
- AST-completeness failures are one-shot in v1 (surface the gap, stop). The self-correcting loop is a future deepening once issue #6 structured output lands.

**Queue admission.** Liberal queue, strict execution. All 5W1H candidates queue. A heuristic verb denylist (`send/transfer/wire/delete/purchase/pay/unlock/disable`) marks queued items with a 🛑 badge and collapses detail until the user expands. Execution still requires a wake, and the capability gate fires with `passive=True` so queue-originated calls escalate one notch.

**Profile scope.** ACL is per-profile. CONTEXT.md's "system settings are global" rule does not bind permissions — consent belongs with identity, like memory. New profiles inherit a system default-default snapshot and diverge from there. Persistent grants and per-tool overrides live in SQLite (`profile_acl` table). Once and session grants live in RAM and are cleared on profile switch or Cerebral restart.

**Consent surface.**

- Default: tray notification with inline Once / Session / Persistent / Deny buttons and a Why? expander.
- `irreversible`-flagged calls: modal in the visualiser window, explicit accept required.
- Voice in active mode: Felix speaks the gist and listens via a Vosk constrained grammar of `["yes", "no", "later"]`.
- Fail-closed if no UI or voice surface is available — a silent grant in that state is indistinguishable from compromise.

**Permissions UI** (tray pulldown → Permissions, two tabs):

1. *Capabilities* — 16 rows, one policy toggle per class, session-grant revoke list, new-plugin-flag clearer.
2. *Tools* — searchable list, per-tool override dropdown (inherit / silent / ask / deny).

**Migration.** One PR (not 30). Adds `REQUIRED_CAPABILITIES: frozenset[str]` to every existing plugin module and adds orchestrator enforcement that refuses plugins missing the constant. No grandfathered "trusted tier" — every plugin declares, every plugin gates.

**Gate location.** The capability gate runs in the orchestrator, outside the plugin's address space. There is no subprocess sandbox in v1; siting the gate in the orchestrator means a future sandbox can become an IPC contract without re-architecting consent.

## Consequences

- Every plugin must declare `REQUIRED_CAPABILITIES`. Plugins that don't are refused at registration. This is enforced day one, not opt-in.
- The 16-class vocabulary is closed. Adding a class is a deliberate ADR-level change, not something a plugin can do on its own.
- Builder-generated plugins are usable on the same trust floor as hand-authored ones because the AST check makes the capability declaration verifiable.
- Opaque/binary plugins cannot be installed via the normal path. Users who need them drop them into `plugins/_trusted/` and accept the red badge — a visible, ongoing reminder rather than a silent exception.
- Queue-originated calls cannot ride a persistent grant to silently execute, which closes the ambient-actuation attack class without taking the queue's coverage away.
- The fail-closed default means a headless or surface-less Cerebral cannot grant `ask`-class capabilities at all. That is the intended behaviour: better a refused action than a silent one.
- Per-profile ACL means a guest profile cannot inherit the owner's persistent grants. This is the foundation the multi-user/household case (T5) will build on later; the schema does not need to change to support it.
- `shell_exec` is denied by default. Power users will hit this on first run and have to flip it in settings. That friction is intentional: shell is the highest-blast-radius class in the vocabulary.

## Amendment (2026-05-18) — Connected-account credential storage mechanics

**Context.** The original decision named `secrets_read` as the class for credential reads (the AST completeness check maps it only to `keyring.*`) and put persistent grants in SQLite `profile_acl`, but never said *where* external-account credentials (OAuth client secrets, refresh/access tokens) are stored. The real Gmail/Calendar API arc forces the decision, and CONTEXT.md now defines a **Connected account** as per-profile identity (consent belongs with identity, like memory and ACL — never global).

**Decision.** A Connected account's credential is split by sensitivity:

- **Secret material** (OAuth `client_secret`, `refresh_token`, `access_token`) → the OS keyring via the `keyring` library, namespaced per profile (`service="openmind"`, `username="profile_<id>/<provider>/<field>"`). This operationalizes the mechanism the capability audit already sanctions — keyring moves from audit-mapped-but-unused to a real runtime dependency.
- **Non-secret metadata** (`client_id`, connected account email, granted scopes, connection status, timestamps) → a new per-profile SQLite table adjacent to `profiles`.

Credentials are per-profile, never global, never written to plaintext `felix-settings.json`. Reading a refresh/access token at tool-call time is a `secrets_read` (ask-class) call; because the read goes through `keyring.get_password`, the AST completeness check *requires* the declaration (it is not an over-declaration).

**Considered and rejected.** Plaintext `felix-settings.json` (cleartext secrets — wrong default). SQLite-only with bespoke at-rest encryption (re-implements what the OS keyring already provides; keyring is already the audit-sanctioned path). Env vars (the status quo being replaced — not user-manageable, not per-profile).

**Consequences.** `keyring` becomes a real dependency (first actual use). A keyring-unavailable host fails closed for connected-account tools — consistent with the fail-closed stance above. The 16-class vocabulary is **unchanged**: `secrets_read` already covers this; no new class, this amendment is mechanics only.

## Amendment (2026-05-20) — Per-tool irreversible declaration on the Tool dataclass

**Context.** The original decision listed `irreversible` as a cross-cutting flag (a modifier on a call, not a class) and routed irreversible-flagged calls through a modal — but never said *where* the per-tool declaration lives. The modal mechanism shipped end-to-end in #43 (`CallFlags`) + #49 (`ModalSurface`) + #50 (voice consent) and the orchestrator's dispatch ladder has read `flags.irreversible` since #49. The gap: no production dispatch site translated tool metadata into `CallFlags(irreversible=True)`, so the modal mechanism was unreachable for any tool in practice. Re-surfaced as a deferred non-goal in every write-class plugin retro since #116 (Gmail), #117 (Calendar), #133 (Todoist CRUD), and #136 (Notion). #139 closes the gap.

**Decision.** The `Tool` dataclass (`cerebral/mcp/orchestrator.py`) gains a field `irreversible: bool = False`. Plugins opt in per-tool by passing `irreversible=True` to `Tool(...)` inside `list_tools()`. The orchestrator caches the `Tool` object alongside `_tool_index` (`_tool_lookup: dict[str, Tool]`, populated in `register` and cleared in `_remove_from_index`) and ORs the declaration into `CallFlags` at the start of `call_tool` and `check_capabilities`, before any gate / ACL / modal routing reads the flags. The merge semantic is one-way: a caller that explicitly passes `irreversible=True` keeps it (you can opt in even if the declaration is False); a caller that omits the flag inherits the declaration. The modal mechanism, the `ModalRequest.to_ipc()` envelope, and the 22 existing modal-routing tests are unchanged.

**Considered and rejected.** A module-level `IRREVERSIBLE_TOOLS: frozenset[str]` set in the orchestrator (centralized lookup, but couples the orchestrator to plugin tool names and breaks per-plugin discoverability — the LLM no longer sees the flag in `tools_for_llm` schema). A `CallFlags.from_tool(tool)` builder-time constructor (more indirection for a single bool today; the dispatch-site lookup is the simpler shape until additional tool-shape modifiers land).

**First marked tool.** `gmail_send` in `plugins/gmail.py`. Other write-class tools (`todoist_delete_task`, `calendar_create_event`, `notion_create_page`, etc.) stay unmarked in this slice; future marking is a one-line edit per plugin and does not need its own ADR amendment.

**Consequences.** The 16-class capability vocabulary is **unchanged** — `irreversible` was always a `CallFlags` modifier, not a class. The modal mechanism is **unchanged** — same IPC envelope, same Accept/Cancel semantic, same fail-closed rules. New irreversible tools mark on arrival; the mechanism is the line of code, not the ADR. One pre-existing gap is **explicitly not closed** by this amendment: the tray-IPC `call_tool` handler at `cerebral/main.py:1127` calls `_orc.call_tool(tool_name, tool_args)` with no capability argument, so the orchestrator's gate ladder (and therefore the irreversible-routing branch) is skipped entirely from that entrypoint. That gap is independent of the irreversible flag and is left to a separate slice; #139's wiring fires for queue-originated calls (the `approve_item` path at `cerebral/main.py:1167-1168`) and from any direct caller that supplies a capability.
