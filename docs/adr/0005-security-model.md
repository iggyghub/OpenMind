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

## Amendment (2026-05-23) — Static-token credential storage

**Context.** The 2026-05-18 amendment introduced the per-profile keyring + SQLite split for OAuth credentials (`client_secret` / `refresh_token` / `access_token`), but the five static-API-token plugins shipped since then (youtube #109, todoist #130, notion #136, toggl #142, clockify #145) all read their tokens from process env vars (`YOUTUBE_API_KEY` / `TODOIST_API_TOKEN` / `NOTION_API_TOKEN` / `TOGGL_API_TOKEN` / `CLOCKIFY_API_KEY`). Env vars are not per-profile, not user-manageable from the UI, and force the user to bake secrets into shell config to use any of the five plugins. Re-surfaced as a deferred non-goal in every static-token plugin retro (#130 / #136 / #142 / #145 §11 candidate (f)).

**Decision.** Static-API-token credentials use the SAME per-profile store as OAuth credentials. `CredentialStore.SECRET_FIELDS` extends from `("client_secret", "refresh_token", "access_token")` to `("client_secret", "refresh_token", "access_token", "api_token")` — `"api_token"` is the fourth secret field, namespaced per profile in the keyring as `profile_<id>/<provider>/api_token`. The metadata row in `connected_account_credentials` represents a static-token account as a degenerate case: `client_id=""`, `email=""`, `scopes=[]`, `status="connected"` (vs OAuth's `status="connected"` after consent). The tray Credentials window's new **API keys** section is the canonical config surface; the env var stays as a fallback (**keyring wins, env fallback**) so existing setups keep working. Reading a static API token is `secrets_read` (ask-class) and goes through `keyring.get_password` (in `CredentialStore`), so the AST audit *requires* the declaration — already declared at posture-B on all five plugins.

**Considered and rejected.** A separate `static_token_accounts` table (duplicates upsert/delete logic + audit invariants for no gain — the OAuth row's shape is a strict superset). Keyring-only with no SQLite row (loses last-rotated metadata + forces iteration to enumerate state). Hard env cutoff (breaking change for users who set env vars before this slice; the keyring-wins-env-fallback semantic makes the migration transparent). A one-click "import from env" button (adds button-state complexity for no real ergonomic gain over the auto-fallback's "Set (env)" status pill).

**Consequences.** The 16-class capability vocabulary is **unchanged** — `secrets_read` already covers this; this amendment is mechanics only. `plugins/youtube.py` joins the four other static-token plugins on the `TokenProvider` seam (was the lone holdout reading env inside `__init__`); a freshly-set key now picks up without a Cerebral restart (was one-shot at construction). Future per-profile static-token mode is the default once this ships; system-wide env-var-only mode remains supported as the fallback. The tray's Credentials window now carries TWO sections (Google OAuth + API keys); the secret-write-only contract (the renderer never receives a token value back) carries from #114 across both sections.

## Amendment (2026-06-01) — Consent surface routing under the Main window

**Context.** The original 2026-05-11 decision declared the consent surface as "tray notification with inline buttons" for ask-class calls and "modal in the visualiser window" for irreversibles. Both statements diverged from what shipped: ask-class actually renders as a per-`request_id` 360x340 alwaysOnTop frameless `BrowserWindow` (`tray/windows/consent.html`), and irreversibles render as a per-`request_id` 420x320 alwaysOnTop frameless `BrowserWindow` (`tray/windows/irreversible-modal.html`) — the visualiser is a 200x200 transparent click-through that cannot host a modal in practice. The drift was tolerable when the tray was a tray-only app. The introduction of the **Main window** (a chat-primary control surface, see CONTEXT.md "Main window" / "Conversation") forces a re-decision: where do gates render now that Felix has a canvas of his own?

**Decision.** Consent surface routing splits by class, not by context:

- **Ask-class gates** render as **inline cards in the Main window's Conversation pane.** The card shows the same vocabulary (Once / Session / Persistent / Deny + Why? expander). When the Main window is not focused, an OS notification fires (reusing the existing `NotificationManager` pipeline) and clicking it focuses the Main window with the card scrolled into view. The separate `tray/windows/consent.html` `BrowserWindow` is retired.
- **Irreversible-flagged gates** continue to render as a **separate alwaysOnTop modal** (`tray/windows/irreversible-modal.html`), regardless of whether the Main window is open or focused. The deliberate friction of an alwaysOnTop interrupt is the feature — the 2026-05-18 / 2026-05-20 amendments lean on it, and inlining an irreversible into a scrollable chat would let a careless click ratify an undo-impossible action.
- **Voice consent** (Vosk constrained grammar of `["yes", "no", "later"]`) is unchanged — same fast path in active mode regardless of which visual surface would otherwise render.
- **Fail-closed** remains unchanged. If the Main window is unreachable AND the irreversible modal can't open AND voice isn't available, the gate denies.

**Considered and rejected.** Both-inline (B): puts irreversibles in a chat scroll, weakens the blast-radius friction that the 16-class vocab + irreversible flag exist to provide. Context-routed for both (D): "will this prompt as a modal or as a card?" becomes unpredictable for the user — a stable mental model beats clever routing. Keep both as separate alwaysOnTop popups (A): leaves the Main window oddly amputated, with Felix's own actions showing up in a different surface than Felix's own conversation. Reverting to "tray notification" as the literal ADR-0005 text says: the OS notification carries 2–4 buttons reliably on Windows 10/11 only and not on macOS — making the canonical surface a chat card and using the notification as a focus-the-window ping is the platform-portable shape.

**Consequences.** The 16-class capability vocabulary is **unchanged**. The two cross-cutting flags (`passive`, `irreversible`) and their escalation semantics are **unchanged**. The IPC envelope for `consent_request` and `irreversible_modal_request` is **unchanged** — same `request_id`, same payload, same response shape; only the renderer changes. `tray/windows/consent.html` is removed; its logic moves into a Conversation-pane card component in the Main window. `tray/windows/irreversible-modal.html` stays as-is. The `ConsentManager` in `tray/lib/consent-manager.js` continues to own the pending-request map; it routes to the Conversation card surface instead of opening a separate window. `ModalManager` is unchanged. Per-tool pre-approvals (target allowlists) and the `irreversible` blast-radius override remain orthogonal to surface routing.

## Amendment (2026-06-15) — Recipes save the plan, not the grant

**Context.** ADR-0008 introduces **Recipes**: saved, named, user-approved chains (a frozen sequence of tool calls) a user re-runs on command. A Recipe is a natural place to accidentally weaken the gate — "I already approved this 3-step sequence once, why prompt me every time?" The pull toward caching the consent alongside the steps is exactly the ambient-actuation / standing-grant risk this ADR exists to bound (threats #1 and #3).

**Decision.** A Recipe stores the **plan, never the grant.** Re-running a Recipe re-fires **every per-step capability gate** exactly as if Felix had planned the chain fresh that moment — with `passive=False` (the user actively invoked it). A Recipe with a `gmail_send` step pops the irreversible modal on **every** run. The save grants nothing. This reuses the active-loop dispatch path (the issue #238 gate pattern), so per-step gating is the default, not a Recipe-specific addition.

**Considered and rejected.** Standing approval (the save also records a session/persistent grant over the sequence) — turns a saved Recipe into a pre-approved footgun and lets a once-reviewed chain silently execute sensitive tools later, defeating "liberal queue, strict execution." Plan-level approval (approve the whole chain once at run time) — the planner computes each step from the previous result, so there is no full plan to pre-approve, and pre-approving uncomputed steps weakens the gate.

**Consequences.** The 16-class vocabulary and both cross-cutting flags are **unchanged** — Recipes add no permission surface; they ride the existing gate. Recipes are per-profile (a Recipe never appears for another profile, falling out of per-profile storage, consistent with "consent belongs with identity"). A Recipe whose tool was uninstalled since the save fails that step gracefully rather than crashing the chain. Parameterized Recipes (run-time fill-in-the-blank args) are post-v1 and do not change this stance: re-gating is per *call*, regardless of how the args were bound. This also records that the `main.py` "no-capability dispatch" gap noted as open in the 2026-05-20 amendment's consequences was **closed by issue #238** — both the active loop and Recipe replay depend on that closure.

## Amendment (2026-06-25) — Browser web-login password storage

**Context.** The browser-automation harness needs to drive a **dedicated secondary web account** (the user's own throwaway Google account, namespaced under the `google_web` provider — distinct from the OAuth Workspace `google` provider) while logged in. The preferred mechanism is session reuse: log in once by hand into a Playwright persistent context and persist the authenticated cookies (`storageState`), so no password is ever stored. But the user explicitly requires **unattended re-login** — when the session expires with nobody at the keyboard, the harness must be able to re-authenticate. That forces a credential the prior amendments deliberately excluded: a raw account password. Every secret in `SECRET_FIELDS` to date (`client_secret`, `refresh_token`, `access_token`, `api_token`) is *scoped and revocable*; a password is neither. `test_secret_rejects_unknown_field` enshrined `"password"` as a rejected field precisely to mark that boundary.

**Decision.** `CredentialStore.SECRET_FIELDS` extends from four fields to five: `password` is the fifth, namespaced per profile in the keyring as `profile_<id>/<provider>/password`, identical mechanics to the other four (keyring-only, never SQLite, never logged, swept by `delete_credential`). The connected-account email is non-secret and lives in the `connected_account_credentials.email` column as before. Reading the password at re-login time is `secrets_read` (ask-class), via `keyring.get_password` — the AST audit *requires* the declaration, same as every other secret read. Storage is scoped to the dedicated browser-automation provider (`google_web`); the OAuth `google` provider does not gain a password field.

**Considered and rejected.** Session-only with no stored password (the `storageState` ideal) — rejected here only because the user requires unattended re-login; it remains the recommended path and the password is a fallback for session expiry, not the per-run mechanism. Storing the password outside the keyring (env var / plaintext `felix-settings.json`) — same cleartext-default failure the 2026-05-18 amendment already rejected. A separate `web_login_accounts` table/store — duplicates the upsert/delete/audit invariants the existing per-profile store already provides, for one extra field. Adding `password` to the OAuth `google` provider — conflates two distinct identities (the Workspace account vs. the throwaway web account) in one namespace; the distinct `google_web` provider keeps them separate.

**Consequences.** The 16-class capability vocabulary is **unchanged** — `secrets_read` already covers password reads; this amendment is mechanics only. `SECRET_FIELDS` is now `("client_secret", "refresh_token", "access_token", "api_token", "password")`; `delete_credential`'s sweep covers the new field automatically (it iterates the tuple), preserving the delete-completeness invariant. The blast radius of this one field is materially larger than the others — a leaked password is full, non-revocable account access — so it is **scoped to a dedicated secondary account by convention** and never used for a primary identity; mitigating that blast radius is the reason the recommended path stays session-reuse and the password is the unattended-fallback only. A keyring-unavailable host fails closed for this field exactly as it does for the others (`set_secret` raises the `pip install keyring` error; `get_secret` returns `None`).

## Amendment (2026-07-02) — Unattended stored-password re-login retired; reuse failure escalates to a human

**Context.** The 2026-06-25 amendment admitted the `password` field specifically to enable **unattended re-login** when the `google_web` session expires with nobody at the keyboard, with session-reuse as the recommended path and the password as the fallback. Live verification killed the fallback in practice. Two independent failures (see `.learnings/LEARNINGS.md` #5): (1) a week-old session did not degrade to a step-up wall — it **fully expired**, so reusing it bounced to Google's public logged-out page; (2) the headless password login then hung and failed because `accounts.google.com/signin/v2/identifier` redirects to the **account-chooser** (no email field) for a remembered account — on top of the 06-25 finding that a headless password login trips Google's bot wall regardless. A scripted login against a third-party IdP's step-up/bot defenses is not a battle the harness can win. The user's own framing: "I can't automate the Google login — just notify me and open the window."

**Decision.** The unattended path no longer drives the stored password. `BrowserSession.ensure_logged_in(unattended=True)` now returns `LoginState.NEEDS_VERIFICATION` on **any** reuse failure (dead session *or* detected "verify it's you" wall — the two are no longer distinguished). The `browser_session` plugin's existing escalation handles both: it fires an OS notification and opens a **visible attended window** (Cerebral runs in the user's interactive session, so it can) for the human to complete the sign-in, then resumes. The attended path (`_login_attended` → `wait_for_manual_login`) never reads or fills credentials — it only watches for the logged-in state; any password pre-fill the user sees is Chrome's own autofill inside the persistent profile, not the harness. The `password` **secret field remains in `SECRET_FIELDS`** and the tray write-surface stays, but `_login_unattended`/`PlaywrightDriver.login_with_password`/`LoginState.REAUTHENTICATED` are now **DORMANT** (defined, unit-covered, no production caller).

**Considered and rejected.** Keep attempting the password first, escalate only on failure (wastes ~30s per expiry and trips Google's bot wall before escalating — the escalation is the reliable path, so go straight to it). Harden `login_with_password` to click through the account-chooser (polishes a path that still bot-walls; effort spent on a dead end). Delete the `password` field + tray surface + ADR-0005 (2026-06-25) storage entirely (a larger, separate teardown across keyring + tray UI; left as a deliberate follow-up so the retirement and the storage-removal are decided independently).

**Consequences.** The 16-class capability vocabulary is **unchanged**; `SECRET_FIELDS` is **unchanged** (still five fields — this retires a *code path*, not the storage). The recommended path (session-reuse) is now the *only* automated path; unattended recovery is an attended hand-off, not a password replay — which also removes the largest-blast-radius secret from the runtime hot path (it is stored but no longer auto-read). Whether to also remove the now-dormant `password` storage + tray field is an open follow-up. The 2026-06-25 amendment's "unattended re-login" rationale is **superseded** by this one; its storage mechanics still stand until that follow-up decides otherwise.
