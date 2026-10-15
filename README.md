# Skills

A collection of [Claude skills](https://docs.claude.com/en/docs/claude-code/skills) by Juan Lago.

## Install

### With the `skills` CLI

Install all skills, or pick individual ones interactively:

```sh
npx skills add juanparati/skills
```

### As a Claude Code plugin

```
/plugin marketplace add juanparati/skills
/plugin install skills@juan-skills
```

## Skills

| Skill | Description |
| ----- | ----------- |
| [business-logic-docs](skills/business-logic-docs/SKILL.md) | Reads code, comments and git history to explain an app's business logic as an interlinked Obsidian-style vault (domains, workflows, entities, rules, glossary, mermaid diagrams). Updates existing docs incrementally. |

## Repository layout

```
skills/<name>/SKILL.md   # one directory per skill (+ optional scripts/, references/, assets/)
templates/skill/         # starting point for new skills
.claude-plugin/          # Claude Code plugin + marketplace manifests
```

## Development

`.claude/skills/` and `skills-lock.json` hold skills installed locally for development only. They are git-ignored and are not part of the published collection.

## License

[MIT](LICENSE)
