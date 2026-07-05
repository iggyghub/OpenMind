# SKILLS-BUILD.md — dev-skills campaign driver

Autonomous slice loop building 5 Claude Code skills under `.claude/skills/`.
Each slice = one skill = one issue = one PR. `scripts/run-skills.ps1` drives this file.
Skills are independent (each in its own `.claude/skills/<name>/` dir) so they do not
collide, but the loop still lands each PR before the next for a clean master.

## Status: ready

<!-- ready = slices remain; done = SK-5 landed; blocked = a session needs a human -->

## Next slice — start here

- **Active:** SK-1 — #360
- **Model:** sonnet

## Queue

- [ ] SK-1 — #360 — campaign-scaffold (generate a loop campaign)
- [ ] SK-2 — #361 — loop-doctor (diagnose + heal a stalled loop)
- [ ] SK-3 — #362 — plugin-scaffold (ADR-0005-compliant MCP plugin skeleton)
- [ ] SK-4 — #363 — live-verify (exercise a slice against real Cerebral)
- [ ] SK-5 — #364 — adr-amend (house-style ADR amendment)

## Landed PRs

<!-- session appends: "SK-1 -> PR #NNN" as each merges -->

## SAFETY

- **Skill-authoring only.** Each slice writes a `SKILL.md` (+ optional bundled
  templates) and nothing else. Do NOT launch a real loop, create real issues,
  register a real plugin, or start a real Cerebral from within a slice — the skills
  *document* how to do those; building them must not *do* them.
- Follow `.claude/skills/write-a-skill`: valid frontmatter (`name` + `description`
  with trigger phrases), progressive disclosure, bundled resources only if needed.
- Verify = SKILL.md exists with valid frontmatter and any referenced bundled file
  exists. No pytest for these slices.
- If a slice needs a human decision, set `Status: blocked` with a one-line reason
  and stop without merging.
