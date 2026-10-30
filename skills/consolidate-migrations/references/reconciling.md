# Reconciling the tracking table

Every migration tool records which files ran. After the consolidation, an existing database (every developer's, staging, production) already has the schema, and:

- the **kept** files (same name as the original that created their objects) are already recorded as applied: their rows stay. Only tools that store a checksum of the file need the new checksum (SQLx, Prisma, Flyway, Liquibase, Atlas);
- the **removed** originals still have rows with no file behind them: harmless for some tools, fatal for others;
- the **new** files (deferred FKs, seeds, split tables, moved views) have no row, so the tool would run them and fail on "already exists".

`scripts/baseline.py` writes the fix for the detected framework; this page is the overview so you can explain it in the report and spot the strict tools. Fresh databases need nothing: they run the consolidated set from scratch.

## The precondition

A kept name looks applied to the tool, **even where the alters folded into it never ran**. A database that ran `create_users_table` but not yet `add_current_team_id_to_users_table` would treat the rewritten `create_users_table` as done and never get the column. Nothing fails; the column is just missing.

So the bookkeeping is only correct for a database that ran **every** original. `baseline.py` writes a precheck next to the baseline (`precheck.sql`, a query listing the originals that database has not run, or the tool's status command): it must come back empty. A database that is behind is migrated on the old history first (the archived files), then baselined.

## Per framework

| framework | table | applied row without a file | file without a row | checksum | baseline mechanism |
| --- | --- | --- | --- | --- | --- |
| Laravel | `migrations(id, migration, batch)` | ignored (`Migration not found` only on rollback) | runs | none | SQL: delete removed rows, insert new files with a batch number |
| Django | `django_migrations(app, name, applied)` | ignored; `migrate --prune <app>` deletes them | runs | none | SQL: delete removed `(app, name)` rows, insert new files (not `migrate --fake`: a kept file depending on a new split-out one fails the history check) |
| Rails | `schema_migrations(version)` | shown as `NO FILE`; rollback to it raises | runs | none | SQL: delete removed versions, insert new; then `db:schema:dump` |
| Ecto | `schema_migrations(version bigint, inserted_at)` | `** FILE NOT FOUND **`, ignored | runs | none | SQL: delete removed versions, insert new |
| EF Core | `__EFMigrationsHistory(MigrationId, ProductVersion)` | ignored | runs | none | SQL: delete every row but the first original's (the single migration keeps its id) |
| Doctrine | `doctrine_migration_versions(version FQCN, executed_at, execution_time)` | warns and asks to continue | runs | none | `doctrine:migrations:version --delete --all` then `--add --all` |
| TypeORM | `migrations(id, timestamp, name)` | ignored; `migration:revert` fails on it | runs | none | SQL: delete removed classes, insert new `(timestamp, class name)` |
| Prisma | `_prisma_migrations(id, checksum, migration_name, ...)` | `migrate deploy` ignores it; `dev`/`status` report it | runs | sha256, checked by `dev`/`status` | SQL: delete removed, update kept checksums, insert new rows (as `migrate resolve --applied` would) |
| Flyway | `flyway_schema_history(installed_rank, version, script, checksum, ...)` | **validate fails** (unless `ignoreMigrationPatterns=*:missing`) | runs if above the max version, else silently skipped | CRC32, **validated** | SQL: delete removed, insert new with `checksum NULL`, then `flyway repair` (also realigns kept checksums) |
| Liquibase | `DATABASECHANGELOG(ID, AUTHOR, FILENAME, MD5SUM, ...)` | ignored (`unexpected-changesets` lists them) | runs | MD5 of the changes, **validated** | `liquibase clear-checksums`, then `changelog-sync` |
| Knex | `knex_migrations(id, name, batch, migration_time)` | **refuses to run** ("migration directory is corrupt") | runs | none | SQL: delete removed (mandatory), insert new with a batch |
| Sequelize | `SequelizeMeta(name)` | ignored; `db:migrate:undo` fails on it | runs | none | SQL: delete removed, insert new file names |
| Drizzle | `__drizzle_migrations(id, hash, created_at)` (schema `drizzle` on Postgres) | ignored; the migrator compares `created_at` with the journal's `when` | runs when `when` is newer than the last `created_at` | sha256, stored, never compared | SQL: one row with the last entry's `when` and hash, only when there are new files |
| Goose | `goose_db_version(id, version_id, is_applied, tstamp)` | ignored by `up`; `down` of it fails | **fails** if below the max version (unless `-allow-missing`) | none | SQL: delete removed versions (keep version 0), insert new as applied |
| Atlas | `atlas_schema_revisions(version, hash, ...)` | ignored | fails under linear exec order if older than the last revision | `atlas.sum` (directory) and per-revision hash | `atlas migrate hash` then `atlas migrate set <last>`; check `migrate status` |
| Diesel | `__diesel_schema_migrations(version, run_on)` | ignored by `run`; `revert` fails | runs | none | SQL: delete removed versions, insert new 14-digit versions; then `diesel print-schema` |
| SQLx | `_sqlx_migrations(version, description, checksum sha384, ...)` | **`migrate run` fails** (unless `--ignore-missing`) | runs | sha384, **always validated** | SQL: delete removed, update kept checksums, insert new with theirs (so hash after the files are final) |

## What to say in the report

1. The precheck first: run it against each existing database; any row means that database is behind and must be migrated on the old history before anything else.
2. The file to run (`baseline.sql` or `baseline.sh`) and that it is bookkeeping only: it changes no schema. Kept files keep their rows; only removed originals, new files and checksums are touched.
3. Which databases: every one that already has the schema. Name the obvious ones (local dev, CI databases that persist, staging, production).
4. When: after the files are placed and **before** the next deploy or `migrate` run, because the strict tools (Flyway, Knex, SQLx, Goose, Liquibase on checksums) stop working in between.
5. How to confirm: the tool's status command (`migrate:status`, `showmigrations`, `db:migrate:status`, `migrate status`, `flyway info`, `knex migrate:status`, `goose status`, `atlas migrate status`, `diesel migration list`, `sqlx migrate info`) shows every consolidated file as applied and nothing pending.

## Numbers the user has to provide

- Laravel and Knex store a **batch** for the new files: ask for `SELECT MAX(batch)` and pass `--batch` to `baseline.py` (default: computed as `MAX(batch)+1` in the SQL). Kept files keep their original batch, so a `migrate:rollback` of the last batch only reverts the new files.
- SQLx, Prisma and Drizzle store **checksums** of the final files: run `baseline.py` with `--dir` after the files are placed and never edit them afterwards without re-running it.
