# Discovering the business logic

Goal: find **what the business does and why**, not how the framework is wired. Work outside-in: entry points → domain objects → rules → history.

## 1. Orient (10 minutes, not more)

- Read `README`, `CLAUDE.md`/`AGENTS.md`, any `docs/` folder. Human docs often name the domains and the jargon — reuse their vocabulary.
- Look at the dependency manifest (`composer.json`, `package.json`, `Gemfile`, `pyproject.toml`, `go.mod`) to know the framework.
- Get the size of things: count files per top-level folder, `git log --oneline | wc -l`.

## 2. Map the entry points and the nouns

Where business logic usually hides, per framework:

| Framework | Entry points | Domain objects | Rules & processes |
| --- | --- | --- | --- |
| **Laravel** | `routes/*.php`, `app/Console/Commands`, `routes/console.php` / `app/Console/Kernel.php` (schedule), `app/Http/Controllers` | `app/Models`, `app/Enums`, `database/migrations` | `app/Services`, `app/Actions`, `app/Jobs`, `app/Listeners`, `app/Observers`, `app/Policies`, `app/Rules`, `app/Http/Requests` (validation), `config/*.php` (business values), custom folders like `app/Processors`, `app/Workflows` |
| **Rails** | `config/routes.rb`, `lib/tasks`, `config/schedule.rb` | `app/models`, `db/schema.rb` | `app/services`, `app/jobs`, model callbacks/validations, `app/policies` |
| **Django** | `urls.py`, management commands, Celery beat | `models.py` | `services.py`, signals, `forms.py`/serializers validation, Celery tasks |
| **Node/TS** (Express, Nest…) | routers/controllers, queue consumers, cron | ORM schemas (Prisma, TypeORM, Mongoose) | `services/`, `use-cases/`, `domain/` |
| **Spring/Java** | `@RestController`, `@Scheduled`, listeners | `@Entity` | `@Service`, domain packages |
| **Go** | `cmd/`, HTTP handlers, workers | structs in `internal/domain` or similar | `internal/service`, `usecase` |

Also always check:

- **Enums / status constants** → lifecycles (state diagrams).
- **Config files and env defaults** → business numbers (limits, windows, prices, percentages).
- **Validation rules** → business constraints.
- **Scheduled jobs** → recurring business processes.
- **External integrations** (HTTP clients, webhooks) → partners, buyers, payment providers.
- **Tests** → test names often describe rules in plain words (*"it ignores duplicate applications within the window"*). Great source.

Cluster everything into **domains** (5–12 for a typical app). A domain is a business area a non-developer would recognize, not a technical layer.

## 3. Read for rules

For each domain, follow its main entry point through the code and note:

- **Trigger** → **steps** → **decisions** → **outcomes**
- **Rules** with their **concrete values**
- **Statuses** and what moves an object between them
- **Actors** (customer, admin, partner, scheduler…)
- **Business terms** for the glossary
- **Source files** (for the note's `sources` frontmatter)

## 4. Mine comments and git history for the *why*

Comments and docblocks: read them in the files you already identified. Search for intent markers:

```bash
grep -rnE "(TODO|FIXME|HACK|NOTE|IMPORTANT|because|business|client|legal|requirement)" app/ --include=*.php | head -100
```

Git history — use it to explain *why* rules exist and to discover recent features:

```bash
# Recent history overview (cap it; big repos have thousands of commits)
git log --no-merges --format='%h %ad %s' --date=short -n 400

# History of a key file (why did this rule change?)
git log --follow --format='%h %ad %s' --date=short -- app/Services/LeadService.php | head -40

# History of one function/region
git log -L ':isDuplicate:app/Services/LeadService.php' --format='%h %s' | head -80

# Commits mentioning a concept
git log --no-merges -i --grep='duplicate' --format='%h %ad %s' --date=short | head -30

# Merge commits often carry PR titles / ticket ids
git log --merges --format='%h %s' -n 100
```

Use commit messages as **evidence for intent**, then confirm against the current code (the code wins if they disagree — rules change). Mention the history only when it helps understanding (*"Since March 2025, …"*).

## 5. When you can't tell

Write what the code does, and add an **Unclear** callout (GitHub syntax shown; Obsidian uses `> [!question] Unclear`, see `formats.md`):

```markdown
> [!IMPORTANT]
> **Unclear:** the code skips leads from `source = 7`, but nothing explains why. *Ask the team: which source is 7, and why is it excluded?*
```

These questions are valuable output — list them in the final report.
