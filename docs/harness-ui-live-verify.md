# Harness UI rework -- live-verify checklist

Behaviour that can only be confirmed in a running Electron main window
lands here. Each slice appends items rather than editing prior ones; a
human ticks them off after eyeballing the UI. Backend / renderer logic
is covered by pytest / jest and MUST be green before an item is worth
running.

## S1 (#469) -- plugins:list + plugins:changed broadcast

- [ ] With Cerebral running, connect the tray; the WS log shows a
      `plugins:changed` event delivered inside `_greet` AND one fired
      right after `Listening - waiting for tray connection` (startup-
      complete broadcast). Payload carries `plugins[]`, `errors[]`,
      `capability_vocabulary` (16 entries).
- [ ] Sending `{"type":"plugins:list"}` over the WS returns a
      `plugins:list` event with the same payload shape.
- [ ] For `google_workspace`, the `tools[]` entry for `gmail_send`
      carries `"supersedes": {"tool":"gmail_send","from_plugin":"gmail"}`.
- [ ] With a Todoist API token stored in the keyring, the `todoist`
      card's `credentials[0]` reads
      `{provider:"todoist", source:"keyring", hint:"****<last4>", env_var:null}`.
      Unset the keyring, set `TODOIST_API_TOKEN`, refetch: `source` flips
      to `"env"` and `env_var:"TODOIST_API_TOKEN"`; hint still masked.
- [ ] Grep the raw WS trace for the full token value; MUST NOT appear.
