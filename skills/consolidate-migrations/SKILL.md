---
name: consolidate-migrations
description: Consolidates (squashes, flattens) a project's accumulated database migrations so each table and view is created once with its final schema.
---

# Consolidate migrations

Turn a long history of migration files (create, add column, rename, drop index, …) into a set where **each table and view is created once, with its final schema**, in the project's **native migration format**. The consolidated files **keep the names of the originals**: the migration that created a table keeps its exact file name (version, description, folder, class) and now holds that table's final definition.

What the user gets, out of `N` originals:

- **kept files**: every original that created a table, view, type, function or other object that still exists keeps its name. Columns, indexes and FKs added later fold into it. An original that created several tables stays one file or is split into one per table, as the user chooses.
- **removed files**: originals that only altered, renamed or dropped things, or whose objects were all dropped. They are archived, not deleted.
- **new files**, only where nothing else fits: `deferred_foreign_keys` (FKs to a table created later in the history), `seed_reference_data` (rows the user chose to keep), tables split out of a multi-table original, and rarely `functions_and_triggers` or a view that reads something created after it.

What this skill never does: change the schema, move data, run migrations against a database, or delete the originals (they are archived). The result is a replacement for the history, and replacing the history has three failure modes worth keeping in mind all along:

- A column silently lost while merging alters is **data loss on the next fresh install**.
- A wrong tracking table makes production **re-run migrations that already ran**.
- A database that had not run every original **silently misses changes**: a kept name looks already applied there, even though the alters folded into it never ran.

Steps 2, 3, 6 and 8 exist for those reasons.

## Workflow overview

1. **Detect the stack** (`scripts/detect_stack.py`)
2. **Agree the ground rules** with the user (placement, data, multi-table files, scope)
3. **Transcribe** each original into ops, then **replay** them into the manifest (`scripts/replay.py`)
4. **Plan** which file holds each object (`scripts/plan_order.py`)
5. **Write the files** following `references/frameworks/<framework>.md`
6. **Check** the written files (`scripts/check_output.py`)
7. **Place** the files (replace in place with originals archived, or sibling directory)
8. **Reconcile the tracking table** for existing databases (`scripts/baseline.py`)
9. **Report**

Working files live in `<repo>/.consolidate-migrations/` (`stack.json`, `ops/`, `manifest.json`, `plan.json`, `precheck.*`, `baseline.*`). Tell the user at the end that the folder can be deleted, or that `manifest.json` is worth keeping as a schema description.

---

## 1. Detect the stack

```bash
python3 <skill-dir>/scripts/detect_stack.py <repo> -o <repo>/.consolidate-migrations/stack.json
```

It reports the framework, migration directories, SQL dialect, versioning style, the **files in replay order**, the files that look like they write data (`data_files`), the files that look like they create several tables (`multi_create_files`), the schema snapshot if the framework keeps one, and the tracking table. The replay order is framework-specific (Django follows `dependencies`, Liquibase its master changelog, Drizzle its journal, everything else the version prefix), and everything downstream folds the history in that order, so this is worth getting right.

Confirm with the user when the confidence is not `high`, when several candidates are listed (a NestJS repo with both Prisma and TypeORM, a Spring app with Flyway and Liquibase), or when nothing is detected (ask where the migrations live and what runs them). If the dialect is unknown, ask: raw-SQL frameworks need it to write correct DDL, and the cycle handling differs on SQLite.

Then read `references/frameworks/<framework>.md` once, now. It tells you how to read this framework's originals (step 3) and how to write its files (step 5).

## 2. Agree the ground rules

Ask these in **one message**, skipping anything the request already answered:

1. **Where the result goes.** *Replace in place*: the originals move to `<migrations-dir>.archive/` (nothing is deleted) and the consolidated files take their place, kept files under their original names. *Sibling directory*: the files go to `<migrations-dir>.consolidated/` and the originals stay untouched, for the user to swap later. Explain that in replace mode the tracking table of every existing database has to be reconciled (step 8) before the next deploy. State the precondition too: every existing database must have run **every** original before the swap. A database that is behind would take the kept names as already applied and silently miss the folded changes, so migrate it on the old history first. Step 8 writes a precheck for this.
2. **Data found inside migrations.** `detect_stack.py` listed the files that insert or update rows. *Keep* them in one final `seed_reference_data` migration, or *drop* them and list them in the report. Offer the distinction from `references/transcribing.md`: seeds (reference rows a fresh install needs) are worth keeping, backfills and one-off fixes almost never are.
3. **Migrations that create several tables.** Only when `multi_create_files` lists some, typically the framework's initial migration. Name each file and its tables, and ask whether to *keep* them together in that file (it keeps its name and holds all those tables) or *split* them, one table per file. When splitting, the table the file name refers to keeps the file and the others become new files right after it. The user may answer per file. Skip this question for EF Core, which keeps a single migration anyway.
4. **Scope.** Everything found, or a subset of directories or apps. Django apps that reference each other's models go together.

Why ask rather than assume: these choices are cheap to answer and expensive to undo. Moving files is the one step the user cannot easily reverse by hand across a team, seed rows are a product decision, and the split decides which file names survive.

## 3. Transcribe and replay

Read `references/transcribing.md` and `references/ops-and-manifest.md` before the first file.

For each original, in replay order, write `.consolidate-migrations/ops/NNN_<file>.json`: the list of schema operations the file performs, in the framework's own vocabulary. Work in chunks of about 25 files. When the history is long, split the chunks across subagents (give each this skill's `transcribing.md`, `ops-and-manifest.md`, the framework reference and its file list); transcription is order-independent, so they can run in parallel.

Then fold:

```bash
python3 <skill-dir>/scripts/replay.py <repo>/.consolidate-migrations
```

The replay is strict on purpose. An op that touches a table or column that does not exist at that point means a transcription mistake (a missed rename, a skipped file, a typo), and it is far cheaper to find it here than after the files are written. Fix every error and rerun until it passes; read the warnings (indexes dropped with a column, views that lost a table, redefined views) and make sure each one is expected.

Every `data` op carries `decision` (`keep` or `drop`) from the answer in step 2, and the ops file of every original the user answered about in question 3 carries a top-level `"split": true` or `false`. Write them in the ops files rather than editing the manifest, so a replay re-run does not lose them.

**Cross-check against the snapshot** when the framework has one (`schema.rb` / `structure.sql`, `schema.prisma`, EF `ModelSnapshot`, Diesel `schema.rs`, Django `models.py`, Drizzle's TS schema, Atlas `schema.hcl`): compare its tables, columns, indexes and FKs with the manifest. The snapshot wins on intent, but a column present in the migrations and missing from the snapshot (or the reverse) is a finding for the report, never something to drop silently.

## 4. Plan

```bash
python3 <skill-dir>/scripts/plan_order.py <repo>/.consolidate-migrations/manifest.json --mermaid
```

The plan lists every file to write, in order. Each is either **kept**, with the original's path, name and version, or **new**, named in the framework's convention and numbered right after the last original version. It also gives the objects each file holds and the originals that disappear (`removed`). `references/ordering.md` explains the rules so you can summarise them for the user in two sentences:

- The order is the history's, because the files keep their names. Each object is written in the original that created it, with everything later folded in.
- A foreign key to a table created later goes to the final `deferred_foreign_keys` file. A type or function needed earlier moves into that earlier file. A view that reads something created after it moves to the file of its final definition.

Read the warnings. Multi-table originals without a split decision (go back to question 3 if the user was not asked about one: the plan's list is exact, `multi_create_files` was a guess), deferred FKs, objects written outside their creating file, renamed tables whose kept file carries the old name, polymorphic columns without FKs, views without `depends_on` and pending data decisions are all there. `--base` / `--start` change where new files are numbered. Knex and Liquibase take `--ext` when the project uses TypeScript or YAML.

## 5. Write the files

Write into the staging directory `<migrations-dir>.consolidated/` (step 7 moves them if the user chose replace mode). Follow `references/frameworks/<framework>.md` for the templates, and `references/sql-dialects.md` for raw-SQL tools. Rules that hold for every framework:

- **Exactly one file per plan entry**, with the plan's file name and version. A kept entry is a rewrite of its original under the **same** name: keep whatever identifies the migration to the tool (class or module name, TypeORM class, Doctrine `Version` class, Liquibase changeset id and author, EF Core migration id); the framework file says which. Never add files the plan does not list; `check_output.py` flags extras.
- A **header comment** stating that the file was consolidated, from which originals (`folded_from`), and the former name when the table was renamed (a kept `create_articles_table` that now creates `posts` must say so). Future readers will `git blame` this file and find nothing else.
- **Everything in the manifest and nothing else**: for each object the entry lists (`tables`, `views`, `prerequisites`, `functions`, `triggers`, in `members` order): columns in manifest order with their nullability, defaults and comments; the primary key; indexes and uniques; checks; inline foreign keys (except the deferred ones, which the plan lists per table); table options when the originals set them. Data statements of a kept original do not stay in it: they go to the seed file or are dropped, per step 2.
- A `down` (where the framework has one) that is the exact reverse: drop what the file creates, in reverse order; drop the deferred constraints.
- Hand-write from the reference templates by default. Use the framework CLI only where the tool stores generated metadata you cannot write reliably by hand and the CLI can keep the names (EF Core's Designer and snapshot, Atlas's `atlas.sum`), and only when it is installed; the framework file has the fallback. Drizzle's journal and snapshots are edited by hand, because its CLI always assigns new names.
- Views keep the original `SELECT` verbatim.
- Many tables: split the plan entries across subagents, each with the manifest, the plan, the framework reference and its subset of entries.

## 6. Check

```bash
python3 <skill-dir>/scripts/check_output.py <repo>/.consolidate-migrations/plan.json <repo>/.consolidate-migrations/manifest.json <staging-dir>
```

For per-app frameworks staged in a sibling directory, add `--dir-map migrations=migrations.consolidated` so the checker looks where the files are. It is a static, textual check:

- every planned file exists and no unplanned file does;
- every kept file carries its original's name, and no new file reuses an original's name or version;
- every column, index, FK target and check of each table is mentioned in the file that holds the table;
- deferred FKs are in the deferred file;
- no file references a table whose file comes later. It cannot prove the DDL is right, which is why step 3 was strict, but it catches the typical consolidation accidents. Fix everything it reports as an error, and read the warnings.

Where the framework offers a **no-database** consistency check, run it too (for Laravel, Rails, Ecto, Knex, Sequelize there is none beyond a syntax check such as `php -l`, `ruby -c` or `node --check`): Django `manage.py makemigrations --check --dry-run`, Drizzle `drizzle-kit generate` (must say no changes), Prisma `prisma migrate diff --from-migrations <dir> --to-schema-datamodel schema.prisma --shadow-database-url` only if the user has a shadow database, otherwise a diff by eye. The framework file says which apply.

## 7. Place the files

**Sibling directory**: nothing to do; the files are already in `<migrations-dir>.consolidated/`. Say so in the report.

**Replace in place**: require a clean `git status` first (so the user can `git checkout` their way out of anything). Move the originals to `<migrations-dir>.archive/`, move the staged files into the migrations directory, and run the framework's post-steps from the reference: `atlas migrate hash`, Rails `db:schema:dump` (only the `version:` line should change), `diesel print-schema`, rewrite Liquibase's master changelog with includes in plan order, rewrite Drizzle's journal, rebuild for tools that embed migrations into the binary (Goose `embed.FS`, SQLx `migrate!()`, Diesel `embed_migrations!`). The archive now holds files with the **same names and class names** as the kept ones, so it must stay out of the tool's reach:
- EF Core compiles every `.cs` in the project.
- Rails, Ecto and TypeORM raise on duplicate migration classes.
- Doctrine and Laravel autoload.
- Flyway `locations`, Liquibase `includeAll` and Knex `directory` may be broader than the one folder.

## 8. Reconcile the tracking table

Every tool records which files ran. Kept files are already recorded, so an existing database (every developer's, staging, production) needs much less than a full rewrite. What still breaks:
- Removed originals keep rows with no file. Some tools refuse to run at all while that is so (Knex, SQLx, Flyway); others fail on rollback.
- New files have no row, so the tool would run them and fail on "already exists".
- Tools that store checksums reject the rewritten kept files (SQLx, Flyway, Liquibase, Prisma's `migrate dev`).

```bash
python3 <skill-dir>/scripts/baseline.py <repo>/.consolidate-migrations/plan.json --dir <migrations-dir>
```

It writes two files:
- **`precheck.sql`** (or `precheck.sh`): lists the originals a database has not run. It must come back empty, because the bookkeeping is only right for a database that ran every original.
- **`baseline.sql`** or **`baseline.sh`**: delete the rows of removed originals, insert rows for new files, refresh the checksums of kept files. Or it runs the tool's own commands (`flyway repair`, `liquibase clear-checksums` + `changelog-sync`, `atlas migrate set`, `doctrine:migrations:version --add --all`).

Pass `--dir` with the final files for SQLx, Prisma and Drizzle; their checksums are computed from the files. Explain in the report, plainly: **run the precheck, then the baseline, against every existing database before the next deploy; fresh databases need nothing.** The framework reference has the details and the pitfalls per tool.

## 9. Report

Keep it short and concrete:

- Counts: `N` originals → `K` kept (same name) + `M` new files, `R` removed, and where they are.
- The ordered list (the `--text` output of the plan, marking kept and new) and the mermaid dependency graph for anything with more than a handful of tables.
- The multi-table originals and what the user chose for each (kept together or split).
- Deferred foreign keys and why (the target is created later), objects written outside their creating file, tables that no longer exist, renamed tables whose kept file carries the old name.
- Data migrations: kept (and in which file) or dropped, each with its original file.
- Snapshot disagreements and `unresolved` raw SQL, phrased as questions the user can answer.
- The check result, and which framework-native check was run.
- The baseline procedure: the precheck first, then the file to run, and against which databases.
- The `.consolidate-migrations/` folder: delete it, or keep `manifest.json`.
