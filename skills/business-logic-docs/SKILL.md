---
name: business-logic-docs
description: Reads an application's source code, code comments and git history to explain its business logic in plain language, writing interlinked Markdown notes in GitHub flavored Markdown (default) or as an Obsidian vault.
---

# Business Logic Docs

Turn a codebase into **plain-language documentation of its business logic**: what the app does, for whom, which processes it runs, which rules it enforces and *why*.

The output is a small set of interlinked docs: many short Markdown notes, plus a glossary of business terms and mermaid diagrams for flows and states. They are written in one of two formats (see `references/formats.md`):

- **GitHub flavored Markdown** (default) — relative links, GitHub alerts, `README.md` as the entry note. Reads well on GitHub/GitLab and in any editor.
- **Obsidian flavored Markdown** — `[[wikilinks]]`, Obsidian callouts, `Home.md` as the entry note. Opens as an Obsidian vault.

The rest of this file calls the docs folder the *vault*, whatever the format.

The readers are people who need to understand the business, not the code — product people, new teammates, support, stakeholders. Some of them have mild ADHD. So the writing must be **easy to scan, airy, and never condensed**. See `references/writing-style.md` — this is the part that makes or breaks the skill.

## Workflow overview

1. **Find where the docs live and pick the format** (ask the user; detect existing docs)
2. **Pick the mode**: create, update, or coexist with human-written docs
3. **Discover** the business logic (code → comments → git history)
4. **Plan the vault**: decide every note name *before* writing
5. **Write the notes** (templates + writing style)
6. **Write the glossary**
7. **Validate** links and structure, write the manifest
8. **Report** back to the user

---

## 1. Find where the docs live and pick the format

First look for previously generated docs: search the repo for a `.business-docs.json` manifest (skip `vendor/`, `node_modules/`, `.git/`).

```bash
find . -name .business-docs.json -not -path '*/node_modules/*' -not -path '*/vendor/*' 2>/dev/null
```

- **Manifest found** → tell the user and ask: *"I found business docs at `<path>` (last analyzed at commit `<sha>`). Update them?"*
- **No manifest** → ask where to write the docs. Suggest `docs/business/` inside the repo, but any path works (for example an existing Obsidian vault outside the repo).
- **The user already named a location** in their request → use it, don't ask again.

Asking matters because the docs may belong in a vault the user syncs elsewhere, and writing into the wrong folder of their repo is annoying to undo.

Then **ask which format to write in**, with GitHub as the default:

- **GitHub flavored Markdown** *(default, recommended)* — for docs read on GitHub/GitLab or in any editor.
- **Obsidian flavored Markdown** — for docs opened as an Obsidian vault.

You can ask both questions together. Skip the format question when:

- **The user already named a format** in their request → use it.
- **A manifest exists** (update mode) → reuse its `format`. A manifest without `format` is `obsidian` (older runs only wrote Obsidian).

If the location already holds human-written Markdown (coexist mode), suggest the format they use — `[[wikilinks]]` mean Obsidian — but still ask.

Remember the answer as `<format>`: `gfm` or `obsidian`. Every note, link and callout follows it.

## 2. Pick the mode

| Situation at the chosen location | Mode | What to do |
| --- | --- | --- |
| Empty or doesn't exist | **create** | Full workflow below |
| Has `.business-docs.json` | **update** | Follow `references/updating.md` |
| Has Markdown files but no manifest | **coexist** | These were written by humans. Add your notes alongside them, link to them where relevant, and **ask before editing any existing file**. |

If the user asked about one area only (*"document how refunds work"*), still produce the entry note (`README.md` or `Home.md`), the glossary and the manifest, but only the notes for that area. Later runs can grow the vault.

## 3. Discover the business logic

Read `references/discovery.md` for where business logic hides in common frameworks and how to mine comments and git history.

The short version:

- **Map before you dive.** List routes/entry points, models/entities, jobs/commands/schedulers, services, events, state enums, validation rules, config with business values. Cluster them into **domains** (Billing, Orders, Leads…).
- **Read for the *why*.** Comments, docblocks, commit messages, PR/ticket references. A commit like *"ignore duplicate applications within 10 minutes"* is gold — it explains intent the code alone doesn't.
- **Capture the concrete values**: thresholds, time windows, limits, statuses, who can do what. "Applications are deduplicated" is weak; "*a second application with the same phone within **10 minutes** is ignored*" is useful.
- **Skip plumbing**: framework boilerplate, logging, DI wiring, generic CRUD with no rules.
- **Don't guess.** When intent is unclear, write what the code does and add an **Unclear** callout saying what's unknown.

**Large codebases:** if subagents are available, split discovery by domain and run them in parallel. Give each one the domain's entry points and ask for structured findings (actors, steps, rules with values, states, terms, source files, open questions). Keep the vault plan and the glossary in the main context so names stay consistent.

## 4. Plan the vault

Before writing anything, write down the full list of notes and their file names. This is what keeps every link resolvable and avoids two notes for the same concept.

```
<docs-location>/
├── README.md                # entry point (Home.md in Obsidian): what the app does, map of everything
├── Domains/<Domain>.md      # one hub per business area
├── Workflows/<Workflow>.md  # processes, step by step
├── Entities/<Entity>.md     # business objects, their lifecycle and rules
├── Rules/<Rule>.md          # important rules, policies, edge cases
├── Glossary.md              # index of all terms (A–Z)
├── Glossary/<Term>.md       # one note per business term
└── .business-docs.json      # manifest (written by script, step 7)
```

Naming rules:

- File names are human titles in Title Case: `Workflows/Lead Intake.md`, not `lead_intake.md`. Avoid `(`, `)`, `#` and `?` in them.
- Folder names stay in English (the tooling relies on them). Note titles and content follow the user's language.
- **Every file name is unique across the whole vault**, even in different folders. Obsidian requires this (it resolves links by file name); in GitHub format it still avoids confusion. If an entity and a glossary term share a name (`Invoice`), keep the entity note and make the glossary entry point to it, or name one `Invoice (term)` (Obsidian only, because of the parentheses).

Links (full rules in `references/formats.md`):

- **GitHub:** relative path from the note's folder, spaces as `%20`: `[Lead Intake](../Workflows/Lead%20Intake.md)`.
- **Obsidian:** exact file name: `[[Lead Intake]]`, or with display text: `[[Lead Intake|intake process]]`. Don't link to an alias — Obsidian won't resolve it.

## 5. Write the notes

Read these reference files before writing the first note:

- `references/formats.md` — the syntax of links and callouts in the chosen format
- `references/note-templates.md` — the template for each note type (written in GitHub syntax; translate for Obsidian)
- `references/writing-style.md` — the ADHD-friendly style rules, with good and bad examples

Non-negotiables for every note:

- **YAML frontmatter** with `tags`, `aliases`, `sources` (the repo-relative files/dirs the note explains — the update mode depends on this) and `updated` (today's date).
- A **TL;DR callout** right after the title with 2–3 short lines: `> [!NOTE]` + `> **TL;DR**` (GitHub) or `> [!tldr] TL;DR` (Obsidian).
- Short sections, bullets, **bold** for key terms and outcomes, *italics* for nuance and examples, blank lines between ideas.
- A **mermaid diagram** wherever there is a flow (flowchart / sequence) or a lifecycle (stateDiagram).
- Links to related notes, and glossary terms linked on first mention.
- Technical details (classes, file paths, config keys) only in a collapsed *Where in the code* section at the bottom: a `<details>` block (GitHub) or `> [!info]- Where in the code` (Obsidian).

Write in the language the user is talking to you in. Keep code identifiers as they are (in `code`), and add them to the glossary note's `aliases` so readers can search by either name.

## 6. Write the glossary

The glossary is the **shared vocabulary** between the business and the code.

- One note per term in `Glossary/`, using the glossary template.
- Include: domain nouns (Lead, Campaign, Affiliate), statuses (Rejected, Sold), roles, and jargon/acronyms used in the code or commits.
- Each term: plain definition, a concrete example, how it's called in the code, and links to the notes where it matters.
- `Glossary.md` lists all terms A–Z with a one-line definition each.

Aim for the **main** terms — the 15–40 words someone needs to follow a conversation about this business — not every variable name.

## 7. Validate and write the manifest

Run the checker and fix every error it reports (broken links, missing frontmatter, missing TL;DR, wrong-format syntax, duplicate names). Treat warnings about dense paragraphs as real: split them.

```bash
python3 <skill-dir>/scripts/check_vault.py <docs-location> --format <format>
```

Then write the manifest (records the analyzed commit, the format, and maps notes ↔ source files from each note's `sources`):

```bash
python3 <skill-dir>/scripts/manifest.py write <docs-location> --repo <repo-root> --format <format>
```

## 8. Report back

Keep it short:

- Where the docs are and how to open them:
  - GitHub: *"browse `<path>` on GitHub or in your editor, start at `README.md`"*
  - Obsidian: *"open the folder as a vault in Obsidian, start at `Home.md`"*
- What was created/updated (counts per folder; in update mode, the list of changed notes)
- Open questions you flagged as `Unclear`, so the user can answer them
