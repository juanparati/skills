# Updating existing docs

Use this when the docs location contains a `.business-docs.json` manifest. The goal is to **refresh only what the code changes affected**, keep everything else as it is, and never destroy what humans wrote.

## The manifest

`scripts/manifest.py write` creates it; you don't edit it by hand.

```json
{
  "version": 1,
  "format": "gfm",
  "repo": "/abs/path/to/repo",
  "commit": "f198b799…",
  "generated_at": "2026-10-15T10:00:00Z",
  "notes": {
    "Workflows/Lead Intake.md": ["app/Services/LeadService.php", "app/Jobs/Leads/"]
  }
}
```

`notes` is built from each note's `sources` frontmatter.

`format` is the Markdown flavor (`gfm` or `obsidian`, see `formats.md`). **Keep writing in that format** — don't ask the user again. A manifest without `format` comes from an older version of this skill and is `obsidian`. Convert between formats only if the user asks for it.

## Procedure

### 1. See what changed

```bash
python3 <skill-dir>/scripts/manifest.py changed <docs-location> --repo <repo-root>
```

It prints JSON with:

- `from` / `to` — the analyzed commit and the current `HEAD`
- `format` — the vault's Markdown flavor
- `affected_notes` — notes whose `sources` were touched, with the files that changed
- `unmapped_files` — changed files no note covers (possible **new** business logic)
- `deleted_files` — removed files (notes may describe logic that no longer exists)
- `commits` — commit messages since the last run (read them: they tell you *what* changed and *why*)

If `from` equals `to`, tell the user the docs are already up to date and stop (unless they asked for a broader refresh).

### 2. Decide what to do with each change

- **Affected note** → re-read its sources, update only the parts that changed. Keep the structure, wording and links that are still true.
- **Unmapped file with business logic** → add it to an existing note (and its `sources`), or create a new note if it's a new process/rule/entity. Link the new note from its domain hub and `Home.md`.
- **Unmapped file that's plumbing** → ignore it.
- **Deleted logic** → remove or rewrite the affected section. If a whole note no longer applies, tell the user and ask before deleting the file (other notes or the user's own notes may link to it).
- **New business terms** → new glossary notes + `Glossary.md` entry.

### 3. Protect human edits

Anything between these markers belongs to the user. Never change, move or delete it:

```markdown
<!-- manual -->
Text a human added.
<!-- /manual -->
```

When you rewrite a section that contains a manual block, keep the block in place exactly as it was.

Also respect notes **without** `business-docs` in their `tags` — those are human-made notes living in the same vault. Don't edit them; you may link to them.

### 4. Finish

- Set `updated:` to today on every note you changed (only those).
- Run `scripts/check_vault.py` and fix errors (it reads the format from the manifest).
- Run `scripts/manifest.py write` to record the new commit.
- In the report, list the notes **created / updated / proposed for deletion**, with one line each on what changed.
