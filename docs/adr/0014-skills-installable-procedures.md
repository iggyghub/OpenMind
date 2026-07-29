# 14. Skills: installable procedures, distinct from plugins, recipes, and the growth loop

Date: 2026-07-29
Status: accepted (grill session)

## Context

Felix can gain new *tools* (a **Plugin**, built by the **growth loop** /
`plugins/builder.py`) and replay a *chain* (a **Recipe**), but it has no way to
gain a reusable *procedure* — packaged know-how like a grill-style design
interview, a test-first build loop, or "break this plan into issues." Users
acquire these the way Claude Code users do: install packaged skills, in
practice almost always from GitHub.

Nothing in the current model holds them. A Plugin adds code and a tool; a
Recipe freezes one specific chain and is user-authored; the growth loop builds
a new Plugin. And installed instructions that steer the planner are a new trust
surface (prompt-injection) that the plugin path's `code_install` gate does not
describe.

## Decision

1. **A Skill is an installable package of instructions (a procedure)**,
   optionally bundling resource files (templates, reference data, helper
   scripts), that the planner loads to change how it approaches a class of task.
   Full term recorded in `CONTEXT.md`.

2. **A skill adds know-how, never capability.** It invokes only tools the
   profile already has; a bundled script runs only when the procedure calls an
   existing gated tool (e.g. `shell_exec` in the ADR-0010 sandbox). Every call
   it triggers hits the ADR-0005 gate exactly as a direct user request would.
   An online skill's worst case is *proposing* an action the gate still stops —
   not an escalation path. This is why a skill needs no smoke test (unlike the
   builder) and no per-skill sandbox.

3. **The subsystem ships as a plugin — `plugins/skills.py`** — the way the
   growth loop ships as `plugins/builder.py`. Tools: `skill_install`,
   `skill_uninstall`, `skill_enable`, `skill_disable`, `skill_list`,
   `skill_use`. Everything rides existing MCP + ADR-0005 rails; no new injection
   engine.

4. **Storage: two roots, global scope.** Seed skills are version-controlled in
   repo `skills/<name>/`; installed skills land in gitignored
   `cerebral/data/skills/<name>/` (installed wins on a name collision). A skill
   is machine know-how (like a Plugin), not identity (like a Recipe or Memory),
   so it is **global** in v1. Per-profile skill sets are deferred.

5. **Install source is a GitHub repo** (`owner/repo`, optional subpath/ref); a
   repo may hold several skills, and the user picks which to install.
   Provenance = repo URL + commit SHA. **The install reuses the existing
   `FS_WRITE` capability — no new class.** The ADR-0005 vocabulary is closed at
   16, and a skill adds no capability of its own, so it does not warrant
   `code_install`'s heavier treatment (that class exists because a plugin runs
   *code* as Felix; a skill is inspectable text that only ever calls
   already-gated tools). A skill install is a GitHub fetch
   (`network_egress_cloud`, silent) plus a file write to
   `cerebral/data/skills/` (`FS_WRITE`, ask) — one consent per install. Public
   repos only in v1; private repos (via the `github` connected account) and any
   curated marketplace/registry are deferred.

6. **Installed is not trusted: skills land disabled.** Only the user's
   review-then-enable makes a skill visible to the planner (mirrors the
   builder's new-plugin flag). Enabled state is an **opt-in `enabled_skills`
   list in `felix-settings.json`** — the inverse of the existing
   `disabled_plugins` key (plugins default on with an opt-out list; skills
   default off with an opt-in list). `skill_list` / `skill_use` only see names
   in `enabled_skills`. Provenance (repo + SHA + installed-at) lives in a
   per-skill sidecar under `cerebral/data/skills/<name>/`, not in settings.
   Update = re-fetch -> re-review -> re-enable; changed content drops back to
   disabled. Inspectable plain text is the primary defense.

7. **Invocation is via `skill_use(name)`**, which returns the skill's
   instructions (+ resource manifest) into context. Two triggers, both through
   that one tool: **planner-decided** (it sees `skill_list` descriptions and
   calls `skill_use` when a task matches) and **user-explicit** (voice "grill me
   on X" / typed `/grill-me`).

8. **UI: a Skills panel under the Harness nav section, sibling to the Plugins
   panel** (Harness = installed capabilities: Plugins = tools, Skills =
   procedures). Reuses the Plugins-panel pattern + ADR-0012 panel-spec: an
   install field (paste `owner/repo`), an installed list (name / source /
   enable toggle / declared tools), and a detail view (full instruction text
   read-only + provenance + enable/disable/uninstall). Recipes stay in Library
   (user-authored and kept, not installed).

9. **`kind: agent` is reserved but deferred.** v1 skill front-matter carries a
   `kind` field (`procedure` default). A later slice adds `kind: agent` — run
   the skill as a scoped sub-planner in its own context with a tool allowlist
   that is a subset of the profile's grants. No sub-context isolation,
   concurrency, or summary-folding is built now; only the field is reserved so
   agents slot in without a redesign.

## Considered options

- **One dir for seed + installed skills** (the builder's approach, which writes
  generated plugins into `plugins/`): rejected — a skill has no smoke test, so
  untrusted fetched text should not land in the source tree.
- **Skills as a variant of Recipe or Plugin**: rejected — a Recipe freezes one
  chain and is user-authored; a Plugin adds code and a tool. A skill is
  installed know-how that adds neither.
- **Auto-injecting a matched skill's text** the moment the planner thinks one
  is relevant: rejected — needs a relevance matcher, bloats context
  unpredictably, and hides the decision from the transcript. `skill_use` keeps
  it explicit and on existing rails.
- **Enforcing a skill's declared `tools:` as a hard allowlist for procedure
  skills**: rejected in v1 — a procedure runs in the main planner context,
  which already holds every tool; dynamic mid-conversation filtering is real
  machinery. `tools:` is shown for transparency; enforcement is inherent only to
  the deferred `agent` kind.

## Consequences

- Felix gains a third growth axis alongside new tools (growth loop) and saved
  chains (Recipes): installed procedures (Skills). The growth-loop framing in
  `CONTEXT.md` widens accordingly.
- The planner's system prompt must state that skills exist and how to reach them
  (`skill_list` / `skill_use`), or `skill_use` never gets called — the same
  "the prompt never mentioned it" failure ADR-0013 found for memory.
- One extra tool round-trip (`skill_use`) before a skill's procedure runs.
  Accepted; it matches Claude Code's Skill tool and keeps the transcript honest.
