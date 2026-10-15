# Output formats

The docs are written in one of two Markdown flavors. The user picks one in step 1; **GitHub is the default**.

- **`gfm` — GitHub flavored Markdown.** For docs read on GitHub/GitLab or in any editor. Links are relative file paths.
- **`obsidian` — Obsidian flavored Markdown.** For docs opened as an Obsidian vault. Links are `[[wikilinks]]` resolved by file name.

Everything else is the same in both: folder layout, frontmatter, mermaid diagrams, writing style, update flow. The templates in `note-templates.md` are written in GitHub syntax; when writing Obsidian, translate each construct with the table below.

The checker (`check_vault.py --format <fmt>`) enforces the chosen syntax. In `gfm` mode, leftover Obsidian syntax is an error.

---

## Mapping

| Construct | `gfm` (default) | `obsidian` |
| --- | --- | --- |
| Entry note | `README.md` | `Home.md` |
| Link | `[Lead Intake](../Workflows/Lead%20Intake.md)` | `[[Lead Intake]]` |
| Link with other text | `[intake process](../Workflows/Lead%20Intake.md)` | `[[Lead Intake\|intake process]]` |
| Link to the entry note | `[Home](../README.md)` | `[[Home]]` |
| TL;DR | `> [!NOTE]` then `> **TL;DR**` | `> [!tldr] TL;DR` |
| Tip | `> [!TIP]` | `> [!tip]` |
| Warning / edge cases | `> [!WARNING]` then `> **Edge cases**` | `> [!warning] Edge cases` |
| Example | `> **Example:** …` (plain quote) | `> [!example]` |
| Unclear | `> [!IMPORTANT]` then `> **Unclear:** …` | `> [!question] Unclear` |
| Where in the code (collapsed) | `<details>` block, see below | `> [!info]- Where in the code` |
| File names | unique across the vault *(recommended)* | unique **across the whole vault** *(required)* |

---

## GitHub (`gfm`) details

### Links

Links are **relative paths from the note's own folder**, with spaces written as `%20`:

| From | To | Link |
| --- | --- | --- |
| `README.md` | `Workflows/Lead Intake.md` | `[Lead Intake](Workflows/Lead%20Intake.md)` |
| `Workflows/Lead Intake.md` | `Workflows/Lead Scoring.md` | `[Lead Scoring](Lead%20Scoring.md)` |
| `Workflows/Lead Intake.md` | `Glossary/Lead.md` | `[Lead](../Glossary/Lead.md)` |
| `Glossary/Lead.md` | `Glossary.md` | `[Glossary](../Glossary.md)` |

Always include the `.md` extension. Other special characters in file names (`(`, `)`, `#`, `?`) must be percent-encoded too, so prefer simple names.

### Callouts (GitHub alerts)

GitHub supports **only** `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]` and `[!CAUTION]`, in upper case, with **no custom title on the same line** and **no folding**. Put the title as a bold first line:

```markdown
> [!NOTE]
> **TL;DR**
> A lead is **sold** to the buyer who pays the most, **within 2 seconds**.
```

```markdown
> [!IMPORTANT]
> **Unclear:** the code skips leads from `partner_x`, but no comment or commit says why.
```

Examples are a plain blockquote:

```markdown
> **Example:** *Ana applies at 10:02. A second application with her phone arrives at 10:07.*
> → The second one is **ignored**.
```

### Collapsed "Where in the code"

Use an HTML `<details>` block. The **blank line after `</summary>`** is required, or the Markdown inside won't render:

```markdown
<details>
<summary>Where in the code</summary>

- `app/Services/LeadService.php` — *receives and checks new leads*
- config `leads.duplicate_window`

</details>
```

### Frontmatter

Keep the YAML frontmatter (`tags`, `aliases`, `sources`, `updated`): the scripts need it. GitHub shows it as a small table at the top of the note, which is fine.

---

## Obsidian (`obsidian`) details

- Obsidian resolves `[[links]]` by **file name**, so every file name must be unique across the vault, even in different folders. If an entity and a glossary term share a name (`Invoice`), keep the entity note and make the glossary entry point to it, or name one `Invoice (term)`.
- Link with the exact file name, never with an alias: Obsidian won't resolve it.
- Callout types and titles are free (`> [!warning] Edge cases`), and a `-` after the type collapses it (`> [!info]- Where in the code`).

---

## The same note in both formats

### `gfm`

```markdown
# Duplicate Applications

> [!NOTE]
> **TL;DR**
> A second application from the **same person within 10 minutes** is **ignored**.

Applies during [Lead Intake](../Workflows/Lead%20Intake.md), for every [Lead](../Glossary/Lead.md).

> **Example:** *Ana applies at 10:02 and again at 10:07.* → The second one is **ignored**.

<details>
<summary>Where in the code</summary>

- `app/Services/ApplicationService.php`

</details>
```

### `obsidian`

```markdown
# Duplicate Applications

> [!tldr] TL;DR
> A second application from the **same person within 10 minutes** is **ignored**.

Applies during [[Lead Intake]], for every [[Lead]].

> [!example]
> *Ana applies at 10:02 and again at 10:07.* → The second one is **ignored**.

> [!info]- Where in the code
> - `app/Services/ApplicationService.php`
```
