# ADR-0006: Discord user-account integration (self-bot, non-OpenClaw)

**Date:** 2026-05-27
**Status:** Accepted

## Context

PRD #1 story 41 says Felix needs to be reachable on any channel that
gets messages to the user's phone. The user's preferred channel for
that is Discord, but specifically *their own personal Discord account*
-- not a bot account.

OpenClaw 2026.4.29's Discord channel plugin (the harness path that
already serves Telegram in #168) is **bot-API only**. There is no
`gateway.auth.token` route to a user-account login. So Felix cannot
reach a real human's DMs through the OpenClaw harness today, and the
near-term bot path that #164 was scoped for has been deferred ("add
bots at a later date" per user, 2026-05-27).

We need a Discord integration path that:

- Reads the user's incoming DMs from real humans.
- Sends replies on the user's behalf, appearing as the user.
- Lives entirely inside Cerebral, since OpenClaw cannot proxy a
  user-account session.

This is unambiguously the **self-bot** scenario.

## Decision

Build `plugins/discord_user.py` -- a Cerebral plugin that connects
directly to Discord using the user's personal user-account token, via
the `discord.py-self` library, **bypassing OpenClaw entirely**.

The plugin is independent of `plugins/openclaw_channels.py`
(#168 / PR #171): it does not consume OpenClaw's bot-channel surface,
it does not require the gateway to be running, and approving a future
OpenClaw Discord *bot* channel would land alongside it as a separate
integration path.

### Library choice: `discord.py-self`

Two candidates evaluated:

| Library            | Pros                                                                                                          | Cons                                                                  |
|--------------------|---------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| `discord.py-self`  | Actively maintained fork of `discord.py`. Broad API surface (DMs, presence, reactions, edit, delete, voice). API parity with `discord.py` means a future migration to the bot path would be largely import-rename. Well-known community, predictable release cadence. | Heavier dependency; ships some surface (voice, slash commands) Felix doesn't need. |
| `selfcord.py`      | Lighter, more focused on selfbot use cases. Smaller install footprint.                                        | Smaller community, slower releases, narrower coverage (no presence helpers, partial DM API). Diverges from `discord.py` -- future bot migration is a rewrite, not a rename. |

Decision: **`discord.py-self`**.

The dependency weight is a fixed one-time cost. The API-parity-with-
`discord.py` story is the load-bearing reason -- if and when the user
opts into a Discord *bot* path later (the still-open [#164](../../issues/164)
follow-up), the plugin's tool implementations carry over with the
package import as the only diff. `selfcord.py` would lock us into a
selfbot-only path with a rewrite cost on migration.

### Token storage: keyring + env fallback (the #160 pattern)

The Discord user-account token is a **highly sensitive personal
credential**. Detection of automation against it results in *permanent*
account ban (see ToS section below). It MUST NEVER be:

- Logged.
- Committed to the repo.
- Written to `~/.openclaw/openclaw.json` (or any other plain-JSON
  config file).
- Echoed back to the renderer over the WebSocket IPC.

The plugin reads the token via the established
[#160](../../issues/160) static-token resolution chain:

1. **Per-profile keyring entry** -- `provider="discord_user"`,
   `field="api_token"`, via `cerebral/db/credentials.py`'s
   `CredentialStore.get_secret`. Soft-imported -- if `keyring` is not
   installed, the resolver falls through silently.
2. **Env-var fallback** -- `DISCORD_USER_TOKEN`.

The provider is *deliberately not* added to the canonical
`_STATIC_TOKEN_PROVIDERS` UI list in `cerebral/main.py` for slice 1.
The tray's "API keys" section is friendly "click to paste" surface
appropriate for ordinary API tokens (Todoist, Notion, etc.); a
ToS-violating user-account credential should require the user to do a
slightly more deliberate thing (set an env var, or write the keyring
entry from a separate tool). This is friction-as-safety. Slice-2+ may
reconsider once the auto-reply allowlist + low-detection defaults are
in place.

The token is forwarded only to:

- The plugin's own outbound HTTP requests (REST tools).
- The `discord.py-self` client during the gateway handshake.

The plugin's `_scrub` posture (same as `plugins/openclaw_channels.py`)
strips any seen-token value from log lines and `ToolResult` content
before they leave the process.

### Capability declaration

`REQUIRED_CAPABILITIES = frozenset({"secrets_read", "external_data_read",
"external_data_write", "network_egress_cloud"})`:

- `secrets_read` -- deliberate over-declaration matching the
  `todoist.py` / `youtube.py` / `openclaw_channels.py` posture. The
  AST audit would not auto-require it (the plugin never calls
  `keyring.*` directly), but the plugin's job is to drive a personal
  Discord account behind a sensitive credential; handing that the
  silent-class free pass is the wrong default.
- `external_data_read` -- `discord_list_conversations` and
  `discord_get_messages` read the user's DM transcripts (external
  data).
- `external_data_write` -- `discord_send_message` mutates external
  state (a real DM lands in the recipient's client).
- `network_egress_cloud` -- the plugin reaches `discord.com` and
  `gateway.discord.gg` directly. Unlike `openclaw_channels.py`
  (loopback only), Discord is a cloud endpoint, so cloud egress is the
  correct class.

### Why this lives outside OpenClaw

OpenClaw is the canonical messaging harness ([ADR-0003](0003-openclaw-as-harness.md)).
We are intentionally violating that boundary here, for one and only
one reason: **OpenClaw 2026.4.29 cannot do this**. The OpenClaw
Discord channel plugin requires a bot token (`selectionLabel:
"Discord (Bot API)"`, per `openclaw channels capabilities --channel
discord --json`); it has no user-account login flow.

The two alternatives considered:

1. **Wait for OpenClaw to add user-account support.** Indefinite
   timeline; not on any roadmap. Blocks story 41 for Discord
   specifically.
2. **Extend OpenClaw ourselves with a user-account channel plugin.**
   Inverts the dependency direction and ties our v1 timeline to
   OpenClaw's release cadence. Out of scope.

So `plugins/discord_user.py` is a *parallel* integration path. When
the user opts into a Discord *bot* channel later (the existing
[#164](../../issues/164) follow-up that the user has deferred but not
cancelled), the bot path goes through OpenClaw via the existing
`openclaw_channels` plugin. The two coexist; they do not race,
because they connect to two *different* Discord identities (the
user's personal account vs. a separate bot user).

### Acknowledgement of Discord ToS violation

Discord's [Developer Terms](https://discord.com/developers/docs/policies-and-agreements/developer-terms-of-service)
forbid automating personal user accounts. Discord actively detects
self-bots via:

- Endpoint-sequence analysis (which REST endpoints a client hits in
  what order, vs. the official client's patterns).
- Heartbeat timing on the WS gateway.
- TLS fingerprinting (JA3/JA4 patterns inconsistent with the official
  Electron client).
- Behavioural anomalies -- superhuman reply latency, replying without
  ever opening the official client, identical message timing across
  conversations, never going idle.

**Detection results in permanent ban of the human Discord account** --
DMs, friend list, server ownership, Nitro, purchase history are all
lost. There is no recovery path for self-bot bans; Discord does not
unban for this category.

The user filing [#175](../../issues/175) has explicitly accepted this
risk (2026-05-27 conversation). Mitigations the plugin will build in
across slices 2/3 (human-shaped reply delays, typing indicators,
per-sender allowlist, sleep-hours windows, rate limits) **reduce
detection probability but do not eliminate it**. Realistic survival
timeline per community evidence: months-to-years with conservative
usage, weeks with aggressive usage. No contributor should run this
plugin against a Discord account they are not prepared to lose.

This acknowledgement is replicated in two operator-facing surfaces:

- `CONTEXT.md` -- the "Discord user-account integration" section, so
  the next contributor reading the domain doc sees the posture before
  reading the plugin code.
- `SETUP.md` -- the "Discord (user account) -- experimental, high
  risk" subsection, kept separate from the bot-API mention so a user
  setting up Discord via the harness doesn't accidentally consume the
  self-bot instructions.

## Consequences

- A second, parallel Discord integration path now exists. `plugins/
  openclaw_channels.py` (bot-API via the harness, paused until
  [#164](../../issues/164) resumes) and `plugins/discord_user.py`
  (user account, direct) are independent. They can run simultaneously
  without racing because they bind two different Discord identities.
- `discord.py-self` becomes a project dependency. It is **optional**
  -- the plugin's `_default_client_factory` lazy-imports it inside
  the function body; a missing install path returns the
  graceful-degradation "Discord user plugin not available" error
  rather than crashing Cerebral. Add it to `cerebral/requirements.txt`
  as an optional dep documented in `SETUP.md`'s experimental
  subsection.
- The 16-class ACL vocabulary (ADR-0005) is unchanged. Self-bot
  detection risk is *not* a permission class -- it is an operator
  posture documented in CONTEXT.md / SETUP.md / this ADR.
- Slice 1 ships the architecture only: outbound `discord_send_message`,
  draft-only inbound (no auto-reply). Slice 2 adds the per-sender
  auto-reply allowlist and low-detection defaults; slice 3 adds the
  richer outbound surface (react/edit/delete) and presence automation.
  Each slice is its own PR per the per-issue-PRs convention.
- Story 41's "any OpenClaw-supported channel" qualifier is amended
  out (alongside this issue's merge) to "any channel that gets Felix
  to the user's phone" -- the value of the story is remote access,
  not OpenClaw-specifically.
