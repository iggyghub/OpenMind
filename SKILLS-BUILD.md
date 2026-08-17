# SKILLS-BUILD.md — dev-skills campaign driver

Autonomous slice loop building 5 Claude Code skills under `.claude/skills/`.
Each slice = one skill = one issue = one PR. `scripts/run-skills.ps1` drives this file.
Skills are independent (each in its own `.claude/skills/<name>/` dir) so they do not
collide, but the loop still lands each PR before the next for a clean master.

## Status: done

<!-- ready = slices remain; done = SK-5 landed; blocked = a session needs a human -->

## Next slice — start here

- **Active:** none — campaign COMPLETE (SK-1..SK-5 all landed 2026-08-17)
- **Model:** sonnet

## Queue

- [x] SK-1 — #360 — campaign-scaffold (generate a loop campaign)
- [x] SK-2 — #361 — loop-doctor (diagnose + heal a stalled loop)
- [x] SK-3 — #362 — plugin-scaffold (ADR-0005-compliant MCP plugin skeleton)
- [x] SK-4 — #363 — live-verify (exercise a slice against real Cerebral)
- [x] SK-5 — #364 — adr-amend (house-style ADR amendment)

## Landed PRs

- SK-1 -> PR #365
- SK-2 -> PR #366
- SK-3 -> PR #367
- SK-4 -> PR #777 (built autonomously by Felix's self_dev: clone -> edit -> test -> PR -> auto-merge -> load)
- SK-5 -> PR #779 (same; one follow-up fix by hand -- the new-ADR example hardcoded 0009, which would collide since the highest is 0024)

Note: self_dev's planner could not SEE .claude/skills at the time, so both
slices were driven by naming the exact path and forcing a NEWFILE block.
PR #778 widens the candidate list so future skills/docs slices are native.

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
