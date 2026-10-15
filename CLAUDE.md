# CLAUDE.md

This repo is a monorepo of Claude skills, published as `juanparati/skills`. It is distributed two ways: the `npx skills` CLI (discovers `skills/*/SKILL.md`) and the Claude Code plugin marketplace (`.claude-plugin/`, one bundled plugin `skills` in marketplace `juan-skills`).

## Adding a skill

1. Copy `templates/skill/` to `skills/<name>/`.
2. `<name>` is kebab-case and must match the `name` field in the SKILL.md frontmatter.
3. Write a specific `description`: it is what Claude uses to decide when to trigger the skill.
4. Put supporting files in `scripts/`, `references/` or `assets/` inside the skill directory.
5. Add a row to the Skills table in `README.md`.

## Conventions

- Flat layout: `skills/<name>/SKILL.md`. No category folders.
- Claude-only: no `agents/*.yaml` or other agent-specific manifests.
- Never put templates or work-in-progress samples under `skills/`; everything there gets published.
- Bump `version` in `.claude-plugin/plugin.json` when releasing changes.
- `.claude/skills/` and `skills-lock.json` are local dev tooling (git-ignored). Don't edit or publish them.
