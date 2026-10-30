# Which file holds what, and in which order

`scripts/plan_order.py` does this deterministically. This page exists so you can explain the result to the user and recognise when a warning needs a human decision.

## Why order matters

On a fresh database (a new developer's machine, CI, a new environment) the consolidated migrations run top to bottom. Every object must exist before something refers to it:

- a **table** before any foreign key that points at it,
- a **type** (enum, domain) before a column that uses it, an **extension** before a type or column it provides, a **schema** before anything inside it,
- a **function** before a trigger or default that calls it,
- a **table or view** before a view that reads it.

## The order is the history's

The consolidated files **keep the names of the originals that created the objects**: the users table is still created by `2023_01_10_100000_create_users_table.php`, now with its final columns. Names carry the run order (a version prefix, a sequence number, a journal position), so the order of the consolidated set is the order of the original history, minus the files that disappear. Existing databases already have rows for the kept names, which is what makes the switch cheap for them (see `reconciling.md`).

What the script decides is **where each object is written**, by these rules:

| object | written in |
| --- | --- |
| table | the original that created it (also after a rename); later columns, indexes, checks and FKs fold into it |
| foreign key | inline in its table's file when the target table's file is the same or earlier; otherwise in `deferred_foreign_keys` |
| schema, extension, type, sequence, function that reads no table | the original that created it, or an **earlier** kept file when something there needs it (a column changed to an enum created later) |
| view, function that reads tables, trigger | the original that created it when everything it reads exists by then; else the original holding its final definition; else a new file after the last original |
| implicit M2M table (Django) | the file that created it, as an `AddField` on the owner; inside the owner's `CreateModel` when that is the same file |
| data the user chose to keep | `seed_reference_data`, after the last original |

An original whose objects all went elsewhere, or were all dropped, or that only altered things, **disappears** (it is archived with the rest; its content lives on in the kept files).

The old history satisfied the dependencies by accident of time; a table created early can have received, years later, a foreign key to a table created after it. That FK is the forward reference the deferred file exists for.

## Originals that create several tables

A framework's first migration often creates many tables (Prisma `init`, Django `0001_initial`, Laravel's default users + password_reset_tokens + sessions file, Drizzle's `0000_*`, Doctrine's first `Version*`). The user chooses, per run (step 2 of the workflow), and the answer goes into that original's ops file as `"split": true | false`:

- **Keep together** (`false`): the file keeps its name and holds the final schema of every table it created, in FK order inside the file.
- **Split** (`true`): the table the file name refers to keeps the file (`create_users_table` keeps `users`); with no such table, the first one created does. Every other table gets a **new** file:
  - right after the original, when the version format has room: a free second before the next original (timestamp styles), a dotted version for Flyway (`V3.1__create_sessions.sql`); Django and Liquibase take the next free number and get their place from `dependencies` / the master changelog;
  - otherwise (Goose integers, Drizzle's journal, consecutive timestamps) after the last original, and any FK pointing at it from the files in between moves to `deferred_foreign_keys`.

A file with no decision is kept together and the plan warns. EF Core is exempt: it is one migration with a part per table anyway.

## Cycles

Two tables that reference each other (`users.current_team_id → teams`, `teams.owner_id → users`) are usually created by two different originals, so one of the FKs points forward and is deferred: no special handling. In the Laravel example, users is created first, so `users.current_team_id` goes to `deferred_foreign_keys`, which runs after every table exists.

When both tables are written in the **same** file (kept together, or EF Core's single migration), the script breaks the cycle at the softest edge: the table with the fewest NOT NULL foreign keys into the cycle goes first (ties: fewest FKs, then name), and its FKs into the cycle are deferred.

The deferred FK keeps its name and actions; only *where* it is declared changes. The table file still declares the column, except in Django, where column and FK are one `AddField` that lives in the deferred file.

On **SQLite** there is no `ALTER TABLE ... ADD CONSTRAINT`, and SQLite does not check that a referenced table exists at `CREATE TABLE` time, so forward FKs and cycles stay inline and the plan just notes them.

Cycles among views or functions cannot be broken automatically. The script stops and prints them; usually one view's `depends_on` is wrong.

## Self-references and polymorphic relations

A `parent_id → same table` foreign key stays inline. Polymorphic relations (`commentable_type` / `commentable_id`) have no foreign key, so they create no edge; the plan lists them as a warning only so the report can mention them.

## Inside one file

Objects that share a file are written in rank order, then by dependency, then in creation order:

| rank | objects |
| --- | --- |
| 0–4 | schemas, extensions, types, sequences, functions that read no table |
| 5 | tables |
| 6 | functions that read tables, triggers |
| 7 | views and materialized views |

## New files and their versions

New files are the ones no original can hold: `deferred_foreign_keys`, `seed_reference_data`, `functions_and_triggers` (functions and triggers that read something created after every candidate original), views that had to move past the last original, and split tables without room after their original. They are numbered right after the **last original version** (`manifest.source.last_version`), one second (or one sequence number) apart, in plan order: after the history, before any migration someone generates tomorrow, and never on a version an existing database already recorded for another file. Django numbers per app, after that app's last original. Pass `--base` or `--start` to override.

Django and Liquibase also get `depends_on_files` per entry: the previous file in the same app (kept or new, in plan order) plus the files of cross-app dependencies, so the tool's own ordering agrees with the plan. Kept Django files lose any dependency on originals that disappeared.

## The plan's warnings worth repeating to the user

- a multi-table original with no split decision,
- a renamed table whose kept file still carries the old name (`create_articles_table.php` creating `posts`): expected, mention it,
- an object written outside its creating file, and why,
- every deferred FK, with the file that creates its target,
- polymorphic relations, external FKs, views without `depends_on`, pending data decisions.
