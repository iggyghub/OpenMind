---
name: live-verify
description: Exercises a landed slice or tool against a real running Cerebral over the WebSocket IPC to prove end-to-end functionality, preventing "tests green, capability absent" failures. Triggers: "live-verify", "verify against real Cerebral", "run the live checklist", "does it actually work end to end"
---

This is distinct from the built-in `verify` skill. It is Cerebral-IPC specific and exercises a landed slice or tool against a real running Cerebral over the WebSocket IPC instead of trusting unit tests.

### Before you start
Cerebral speaks WebSocket on `ws://localhost:7766`. The operator may already have a Cerebral instance running. Check first and do NOT start a second instance.

### Procedure
1. Snapshot the currently running `cerebral.main` process IDs BEFORE launching anything, so the ones you started can be told apart from the operator's.
2. Launch Cerebral in the background with: `python -m cerebral.main`
3. Wait for the WebSocket on `ws://localhost:7766` to accept a connection and for a heartbeat before driving anything.
4. Drive the target tool over the IPC by sending a JSON message of the form:
   ```json
   {"type": "call_tool", "data": {"name": "<tool_name>", "args": {}}}
   ```
   and read the broadcast whose `type` is `tool_result` to get the real outcome.
5. Report the real result verbatim -- never paraphrase a live result into "it works".
6. Tick the matching checklist item in the relevant docs file.
7. ALWAYS reap: kill only the process IDs that appeared since the snapshot. Never leave an orphan `cerebral.main` running.

### Picking the checklist
The checklist lives in `docs/*-live-verify.md` -- for example `docs/jobs-live-verify.md` or `docs/v1-live-verify.md`. Tick the specific item that the slice under test covers.

### Fail-closed rule
If Cerebral will not start, or the tool call errors, or no `tool_result` arrives, report that plainly AND still reap the processes you started. Never leave a half-started backend behind, and never report success you did not observe.

### Reading the result
A `tool_result` carries `is_error`. An `is_error` result is a real finding to report, not something to retry silently.
