# Transcribing originals into ops

The job per original migration is small: read it, write one `ops/NNN_<file>.json` describing what it does to the schema, in the terms of `ops-and-manifest.md`. `NNN` is the file's position in `stack.json`'s replay order (zero-padded to three digits, or more if there are over 999 files). `replay.py` folds them and complains when something does not add up.

Work in chunks of about 25 originals. Beyond that, attention drifts and columns get lost. When the history is long, hand chunks to subagents: transcription is order-independent, so they can run in parallel, each with this file, `ops-and-manifest.md`, the framework reference and its list of files.

## What to capture

Everything that ends up in the database schema: columns with type, nullability, default, unsigned/auto-increment flags; primary keys; indexes and uniques (with their names when the original names them); checks; foreign keys with their actions; comments; table options (engine, charset); views; triggers, functions, enum types, extensions, schemas, sequences.

Keep values in the **framework's vocabulary**. A Laravel `$table->string('name', 80)` becomes `{"name": "name", "type": "string", "length": 80}`, not `varchar(80)`; a Postgres `email citext NOT NULL` becomes `{"name": "email", "type": "citext", "nullable": false}`. The writing step reproduces the same framework, so translating twice only adds mistakes.

Use `nullable: false` only when the original says NOT NULL (or the DSL defaults to it, like Laravel and Rails do for most columns). Use the original's own index and constraint names; when it let the database generate the name, leave `name` null and the writer will follow the framework's convention.

## Implicit expansions

Each DSL has helpers that create more than one thing. These are the usual places where a consolidation silently loses an index or a foreign key. The framework reference lists them; the common ones:

| original | ops |
| --- | --- |
| Laravel `foreignId('user_id')->constrained()->cascadeOnDelete()` | column `user_id` (foreignId, unsigned bigint) + FK to `users(id)` on delete cascade (no explicit index: MySQL adds one implicitly, record only what the code says) |
| Laravel `morphs('commentable')` | `commentable_type` string + `commentable_id` unsignedBigInteger + composite index; set `polymorphic: ["commentable"]` on the table |
| Laravel `timestamps()` / `softDeletes()` / `id()` | `created_at` + `updated_at` nullable timestamps / `deleted_at` nullable / `id` bigIncrements primary |
| Rails `t.references :user, foreign_key: true` | `user_id` bigint + index + FK; `t.timestamps` = `created_at`, `updated_at` NOT NULL |
| Django `ForeignKey(User, on_delete=CASCADE)` on attribute `author` | column `author_id` with `attr: "author"` + index + FK; `ManyToManyField` = a through table (`implicit_m2m_of` the owner) with two FKs and a composite unique; `AbstractUser` = all its fields plus `groups` / `user_permissions` through tables |
| Ecto `add :user_id, references(:users, on_delete: :delete_all)` | column + FK; indexes are separate `create index(...)` calls |
| Knex `.references('id').inTable('users')` / Sequelize `references: { model, key }` | column + FK |
| Prisma SQL `-- CreateIndex ... UNIQUE INDEX` | index with `unique: true` |
| EF Core `.Annotation("Npgsql:ValueGenerationStrategy", ...)` on a key | `auto_increment: true` |

## Renames, drops and rebuilds

- `rename_table` and `rename_column` carry state forward; later ops use the **new** name. Write the op at the point where the rename happens and keep using the new name in the files after it.
- A table that is dropped later still needs its `create_table` op transcribed (do not skip it because you know the end): the replay must see every reference to it to warn about FKs and views that pointed at it.
- **SQLite rebuild blocks** (Prisma, Rails on SQLite, Django on SQLite): `CREATE TABLE "new_X" (...); INSERT INTO "new_X" (...) SELECT ... FROM "X"; DROP TABLE "X"; ALTER TABLE "new_X" RENAME TO "X";` is a *redefinition of X*. Transcribe it as the ops that turn the old X into the new one (`add_column`, `drop_column`, `rename_column` when the INSERT maps `body` into `content`, `alter_column`), not as a drop and a create: that keeps `renamed_from`/`folded_from` and the FK references intact. Recreated indexes in the block are the same indexes; only add `add_index` for genuinely new ones. Constraint names inside the block may change (after a table rename Prisma rebuilds `Article_authorId_fkey` as `Post_authorId_fkey`): record that as `drop_foreign_key` + `add_foreign_key`, because the name is what existing databases carry and what `down` must drop.
- `CREATE OR REPLACE VIEW` is a `create_view` again; the replay keeps the later definition and notes the replacement.
- Getting this right decides which files survive: the original whose `create_*` op brings an object into existence keeps its file name and ends up holding that object. A rebuild block or a `CREATE OR REPLACE` written as a fresh `create_table` would make the wrong original look like the creator (and fail the replay with "already exists").

## Data inside schema migrations

Statements that write rows do not belong in the schema and need the user's decision (asked in step 2). Record each as a `data` op with a `kind`, so the decision can be applied uniformly:

- **seed**: reference rows a fresh install needs to function (roles, countries, default settings, the first admin). Candidates to keep in `seed_reference_data`, in the framework's own statement form, made idempotent where the dialect allows (`INSERT ... ON CONFLICT DO NOTHING`, `INSERT IGNORE`, `insertOrIgnore`).
- **backfill**: populates a new column from existing data (`UPDATE posts SET status = 'draft' WHERE status IS NULL`). Existing databases already hold the result and a fresh database has no rows, so keeping it is pointless. Usually dropped.
- **fix**: one-off cleanups of bad rows. Usually dropped.

Put the user's answer on each `data` op as `decision` (`keep` or `drop`) before replaying; the plan warns about entries still `pending`. Whatever the decision, the report lists every data migration with its file and summary, because the user may want to move seeds into the framework's seeder instead.

Django `RunPython` and Rails `execute`/`update_all` inside migrations are the same thing in another costume: record the function's effect as `code` (the body or a faithful summary) and classify it.

## When the original is raw SQL you cannot classify

Use a `raw` op with the statement and a note. It goes into `manifest.unresolved`, the plan warns, and the report shows it to the user. That is better than guessing, and far better than dropping it.

## Self-check before running replay.py

- Every file in `stack.json` has an ops file, even when its `ops` list is empty.
- No table is referenced before its `create_table` (replay will tell you, but it is cheaper to notice while reading).
- Columns created by helpers (`timestamps()`, `id()`, `references`) are present.
- Every `create_view` lists what it reads in `depends_on`.
- Every FK's `ref_table` is spelled as the table was named *at that point of the history*.
