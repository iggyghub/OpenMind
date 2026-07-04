# ADR-0010: shell_exec subprocess sandbox

**Date:** 2026-07-03
**Status:** Accepted

## Context

ADR-0005 closed with a deliberate gap. `shell_exec` is the highest-blast-radius class in the 16-class vocabulary and is **denied by default**; a one-time settings opt-in flips it to `ask`. But flipping it only moves the decision to the user — an approved shell command still runs with the full privileges of the Cerebral process: it can read any file the user can, reach the network, and inherit every secret in Cerebral's environment (including a keyring session). ADR-0005 named this and reserved a slot for the fix:

> *"There is no subprocess sandbox in v1; siting the gate in the orchestrator means a future sandbox can become an IPC contract without re-architecting consent."* — ADR-0005, Gate location

This ADR builds that future sandbox. It is the one non-duplicative piece of a chat-agent design diagram the user supplied (2026-07-03): the rest of that diagram (agent loop, native tool-calling, an approval gate, transcript persistence) re-draws OpenMind's already-designed **Chain** (ADR-0008), **ADR-0005 gate**, and **Conversation store** in a foreign Tauri/Rust/React stack that would violate ADR-0002. Only the native OS sandbox had no counterpart, so only it is built — in `cerebral/` (Python), not as a second app.

The threat this closes is ADR-0005 threat #1 (prompt injection → tool misuse) applied to shell specifically: a poisoned page steers the planner into a `shell_exec` the user approves under a plausible pretext, and the command then reads a secret and exfiltrates it, or writes outside the working area. A capability gate authorizes *whether* the command runs; it does nothing to bound *what* it can reach once running. The sandbox is that bound.

## Decision

**A `shell_exec` command always executes inside an OS sandbox — execution is never un-sandboxed.** The sandbox is a Windows AppContainer child process wrapped in a Job Object. It is not an optional mode; there is no code path that runs `shell_exec` outside it.

**Design (each fork resolved against the docs):**

- **Stack — Python via `pywin32`/`ctypes`, no Rust.** Job Object and AppContainer are Win32 APIs. The diagram's `sandbox.rs` was Rust only because it was Tauri; ADR-0002 keeps this a `cerebral/` capability. A thin `Sandbox` interface (`spawn(cmd, workdir) -> result`) fronts the platform impl.
- **Platform — Windows-only in v1; other OSes keep `shell_exec` denied (fail-closed).** The `Sandbox` interface leaves a seam for a future Linux `bwrap`/seccomp/cgroups impl. Where no sandbox backend is present, the ADR-0005 opt-in that flips `shell_exec` deny→ask is **not offered** and the class stays denied — a shell that cannot be sandboxed is not run.
- **File boundary — AppContainer kernel ACL granting only a per-profile sandbox workdir.** This *replaces* a string path-denylist entirely (the diagram's `isBlockedPath`). Kernel-enforced boundaries are not bypassable by `..` traversal, NTFS junctions, 8.3 short names, or `\\?\` prefixes — the whole class of string-match bypasses is gone because there is no string match. Everything outside the workdir is denied for writes.
- **Network — denied by default (no AppContainer network capability).** This closes the exfil chain: an injected command cannot open an outbound connection. A future call that legitimately needs egress carries an ADR-0005 `network_egress_local`/`network_egress_cloud` capability and is a deliberate, separately-gated extension; v1 denies network inside the sandbox unconditionally.
- **Environment — scrubbed minimal env (`PATH`, `TEMP`, `SystemRoot` only).** No `*_API_KEY`, no inherited keyring session. Stops "read a secret from my own environment" inside the sandbox.
- **Resource caps — Job Object: 1 GB commit / 32 active processes / 120 s wall-clock.** The diagram's 10 s CPU cap is **dropped**: it kills legitimate work (a `pip install` with a compile step) long before the wall clock matters. The wall-clock kill is the runaway guard.

**No ACL vocabulary change.** `shell_exec` stays deny-by-default; the existing settings opt-in still flips deny→ask; the sandbox makes that flip *safe* rather than adding a knob. The 16-class capability vocabulary and the two cross-cutting flags (`passive`, `irreversible`) are **unchanged**. The gate still runs in the orchestrator, outside the child's address space, exactly as ADR-0005 sites it — the sandbox is the "IPC contract" that ADR-0005 said a future sandbox would become, reached *after* the gate authorizes the call.

**No new persistence.** `shell_exec` output flows into the existing SQLite **Conversation store** as `tool_result` turns (CONTEXT.md). Output is truncated at capture (~30 k chars, `[truncated]` marker) before it enters the transcript or a model context, so a command that prints megabytes cannot blow the store or a single LLM call.

## Considered and rejected

- **Build the diagram as drawn (Tauri/Rust/React second app).** Violates ADR-0002, duplicates the ADR-0008 Chain and the ADR-0005 gate, and swaps the 16-class model for a weaker `isBlockedPath` string denylist. A parallel agent stack beside Felix, most of it re-implementing what exists.
- **A string path-denylist (`isBlockedPath`) for the file boundary.** Bypassable by `..`, junctions, 8.3 names, and `\\?\` prefixes; a kernel ACL on the workdir is stronger and simpler.
- **Low-integrity token instead of AppContainer.** A low-integrity token restricts writes to medium-integrity objects but does **not** deny network and barely restricts reads — it leaves the two dimensions injection needs (reads, network) open. AppContainer denies both by default.
- **Inherit the parent environment.** Puts every Cerebral secret one `set` command away inside the sandbox; a scrubbed env is the point.
- **Keep the diagram's 10 s CPU cap.** Kills legitimate compiles; the 120 s wall-clock is the correct runaway bound.
- **A new capability class for "sandboxed shell."** Unnecessary — `shell_exec` already exists; the sandbox is execution mechanics on that class, not a new permission surface.

## Consequences

- `pywin32` (or a `ctypes` Win32 wrapper) becomes a runtime dependency for the shell sandbox on Windows. A host without it fails closed for `shell_exec` — consistent with ADR-0005's fail-closed stance.
- The ADR-0005 line "There is no subprocess sandbox in v1" is **superseded** for `shell_exec` by this ADR; the gate-location rationale it stated (gate in the orchestrator so a sandbox becomes an IPC contract) is **fulfilled**, not changed.
- `shell_exec` can be moved from deny→ask by a user who understands the blast radius, now with a real containment boundary underneath the decision rather than none. The friction of the opt-in is retained; only its safety improves.
- The 16-class vocabulary, both cross-cutting flags, the consent surfaces, and the Conversation store are all **unchanged**. This ADR is execution mechanics beneath the `shell_exec` class, layered on ADR-0005, not a permissions change.
- Non-Windows hosts gain nothing yet: `shell_exec` stays denied there until a platform sandbox backend lands. That is the intended fail-closed behaviour, not a regression — the class was denied by default already.
